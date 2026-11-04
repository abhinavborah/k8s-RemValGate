import json
import shlex

import pytest

from remvalgate.baselines import BaselineAction
from remvalgate.baselines.observe import Kind, Observation, observe, symptom_query
from remvalgate.baselines.retrieval_only import replace_service, retrieval_only
from remvalgate.baselines.rule import fault_type_rule
from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, check, check_chain, load_policy
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, Registry, Slice, load_registry
from remvalgate.overlay.fixes import exec_shell
from remvalgate.retrieval import PastFix, RetrieverRefused, Source
from remvalgate.retrieval.pool_corpus import pool_incidents
from remvalgate.retrieval.stub import DevStubRetriever

NS = "test-social-network"
HOTEL = "test-hotel-reservation"
POLICY = load_policy(DEFAULT_POLICY_PATH)
DEV, POOL = "p-synth-dev", "p-synth-pool"
REGISTRY = Registry(slices={DEV: Slice.DEVELOPMENT, POOL: Slice.POOL}, placeholder=None)


def deployment(name, replicas=1, node_selector=None, port=9090, labels=None):
    labels = labels or {"app": name}
    template_spec = {"containers": [{"name": name, "ports": [{"containerPort": port}]}]}
    if node_selector:
        template_spec["nodeSelector"] = node_selector
    return {
        "kind": "Deployment",
        "metadata": {"name": name},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": labels},
            "template": {"metadata": {"labels": labels}, "spec": template_spec},
        },
    }


def service(name, target_port=9090, selector=None, extra_ports=()):
    ports = [{"port": 9090, "targetPort": target_port}]
    ports += [{"port": p, "targetPort": p} for p in extra_ports]
    return {
        "kind": "Service",
        "metadata": {"name": name},
        "spec": {"selector": selector or {"app": name}, "ports": ports},
    }


def pod(name, labels, phase="Running", scheduled=None, conditions=True):
    """A pod. A Pending pod is unscheduled unless scheduled=True, as with a bad node selector."""
    if scheduled is None:
        scheduled = phase != "Pending"
    status = {"phase": phase}
    if conditions:
        status["conditions"] = [
            {"type": "PodScheduled", "status": "True" if scheduled else "False"}
        ]
    return {"kind": "Pod", "metadata": {"name": name, "labels": labels}, "status": status}


def listing(*items):
    return {"items": list(items)}


def healthy(*names):
    items = []
    for n in names:
        items += [deployment(n), service(n), pod(f"{n}-abc", {"app": n})]
    return listing(*items)


def with_fault(base, *items, drop=()):
    kept = [i for i in base["items"] if (i["kind"], i["metadata"]["name"]) not in drop]
    return listing(*kept, *items)


def admitted(action: BaselineAction) -> bool:
    return check(action.command, POLICY, NS).admitted


# --- observe ---------------------------------------------------------------------------------


def test_a_healthy_namespace_has_no_observation():
    assert observe(healthy("user-service", "media-service")) == []


def test_replicas_zero_is_observed_as_scaled_to_zero():
    state = with_fault(
        healthy("user-service", "media-service"),
        deployment("media-service", replicas=0),
        drop=[("Deployment", "media-service")],
    )
    assert observe(state) == [
        Observation(Kind.SCALED_TO_ZERO, "media-service", "deployment media-service has 0 replicas")
    ]


def test_a_deployment_without_replicas_is_not_scaled_to_zero():
    item = deployment("media-service")
    del item["spec"]["replicas"]
    assert observe(listing(item, service("media-service"))) == []


def test_a_node_selector_with_an_unscheduled_pod_is_unschedulable():
    state = with_fault(
        healthy("unique-id-service"),
        deployment("unique-id-service", node_selector={"kubernetes.io/hostname": "extra-node"}),
        pod("unique-id-service-new", {"app": "unique-id-service"}, phase="Pending"),
        drop=[("Deployment", "unique-id-service")],
    )
    (found,) = observe(state)
    assert (found.kind, found.service) == (Kind.UNSCHEDULABLE_SELECTOR, "unique-id-service")


def test_a_node_selector_with_running_pods_is_not_a_fault():
    state = listing(
        deployment("a", node_selector={"disk": "ssd"}), pod("a-1", {"app": "a"}), service("a")
    )
    assert observe(state) == []


def test_an_unscheduled_pod_without_a_node_selector_is_not_blamed_on_it():
    state = listing(deployment("a"), pod("a-1", {"app": "a"}, phase="Pending"), service("a"))
    assert observe(state) == []


def test_an_unscheduled_pod_of_another_deployment_is_not_matched():
    state = listing(
        deployment("a", node_selector={"disk": "ssd"}),
        deployment("b"),
        pod("b-1", {"app": "b"}, phase="Pending"),
    )
    assert observe(state) == []


def test_a_target_port_that_no_container_listens_on_is_a_port_mismatch():
    state = with_fault(
        healthy("user-mention-service"),
        service("user-mention-service", target_port=9999),
        drop=[("Service", "user-mention-service")],
    )
    (found,) = observe(state)
    assert (found.kind, found.service) == (Kind.PORT_MISMATCH, "user-mention-service")
    assert (found.port_index, found.port) == (0, 9090)


def test_a_named_target_port_is_not_compared_with_numbers():
    state = listing(deployment("a"), service("a", target_port="http"))
    assert observe(state) == []


def test_the_mismatching_port_is_found_among_several():
    state = listing(
        deployment("a"),
        {
            "kind": "Service",
            "metadata": {"name": "a"},
            "spec": {
                "selector": {"app": "a"},
                "ports": [{"port": 80, "targetPort": 9090}, {"port": 81, "targetPort": 7}],
            },
        },
    )
    (found,) = observe(state)
    assert found.port_index == 1


def test_a_service_with_no_matching_deployment_is_ignored():
    assert observe(listing(service("orphan", target_port=1))) == []


def test_observations_are_ordered_by_kind_then_service():
    state = listing(
        deployment("zz", replicas=0),
        deployment("aa", replicas=0),
        deployment("b", node_selector={"x": "y"}),
        pod("b-1", {"app": "b"}, phase="Pending"),
        service("c", target_port=1),
        deployment("c"),
    )
    assert [(o.kind, o.service) for o in observe(state)] == [
        (Kind.SCALED_TO_ZERO, "aa"),
        (Kind.SCALED_TO_ZERO, "zz"),
        (Kind.UNSCHEDULABLE_SELECTOR, "b"),
        (Kind.PORT_MISMATCH, "c"),
    ]


def test_the_symptom_query_names_each_observation():
    state = listing(deployment("m", replicas=0))
    assert symptom_query(observe(state)) == "deployment m has 0 replicas"
    assert symptom_query([]) == ""


# --- baseline 1: the fault-type rule ---------------------------------------------------------


def test_the_rule_scales_a_deployment_back_up():
    obs = observe(listing(deployment("media-service", replicas=0)))
    action = fault_type_rule(obs, NS)
    assert shlex.split(action.command) == [
        "kubectl", "-n", NS, "scale", "deployment", "media-service", "--replicas=1"
    ]  # fmt: skip
    assert admitted(action)


def test_the_rule_removes_an_unschedulable_node_selector():
    state = listing(
        deployment("unique-id-service", node_selector={"kubernetes.io/hostname": "extra-node"}),
        pod("unique-id-service-1", {"app": "unique-id-service"}, phase="Pending"),
    )
    action = fault_type_rule(observe(state), NS)
    tokens = shlex.split(action.command)
    assert tokens[:6] == ["kubectl", "-n", NS, "patch", "deployment", "unique-id-service"]
    assert json.loads(tokens[tokens.index("-p") + 1]) == [
        {"op": "remove", "path": "/spec/template/spec/nodeSelector"}
    ]
    assert admitted(action)


def test_the_rule_restores_the_target_port_it_found():
    state = listing(deployment("user-mention-service"), service("user-mention-service", 9999))
    action = fault_type_rule(observe(state), NS)
    tokens = shlex.split(action.command)
    assert tokens[:6] == ["kubectl", "-n", NS, "patch", "service", "user-mention-service"]
    assert json.loads(tokens[tokens.index("-p") + 1]) == [
        {"op": "replace", "path": "/spec/ports/0/targetPort", "value": 9090}
    ]
    assert admitted(action)


def test_the_rule_escalates_when_nothing_matches():
    action = fault_type_rule([], NS)
    assert action.command is None
    assert action.reason


def test_with_several_faults_the_rule_acts_on_the_first_in_priority_order():
    state = listing(
        deployment("zz", replicas=0),
        deployment("aa", replicas=0),
        deployment("c"),
        service("c", target_port=1),
    )
    action = fault_type_rule(observe(state), NS)
    assert shlex.split(action.command)[5] == "aa"


def test_the_rule_gives_the_same_answer_on_the_same_state():
    obs = observe(listing(deployment("a", replicas=0)))
    assert fault_type_rule(obs, NS) == fault_type_rule(list(obs), NS)


@pytest.mark.parametrize("namespace", ["default", "kube-system", "Bad_NS"])
def test_the_rule_refuses_a_namespace_outside_the_sandbox(namespace):
    obs = observe(listing(deployment("a", replicas=0)))
    with pytest.raises(ValueError, match="namespace"):
        fault_type_rule(obs, namespace)


@pytest.mark.parametrize("namespace", ["default", "kube-system", "Bad_NS"])
def test_retrieval_only_refuses_a_namespace_outside_the_sandbox(namespace):
    # the recorded stub fix has no namespace flag, so without the guard it would take this one
    obs = observe(listing(deployment("a", replicas=0)))
    with pytest.raises(ValueError, match="namespace"):
        retrieval_only(_stub(), obs, DEV, namespace)


# --- baseline 2: retrieval only --------------------------------------------------------------


def _stub():
    return DevStubRetriever(registry=REGISTRY)


def test_retrieval_only_applies_the_top_hit_to_the_observed_service():
    obs = observe(listing(deployment("media-service", replicas=0)))
    action = retrieval_only(_stub(), obs, DEV, NS)
    assert shlex.split(action.command) == [
        "kubectl", "-n", NS, "scale", "deployment", "media-service", "--replicas=1"
    ]  # fmt: skip
    assert admitted(action)


def test_retrieval_only_rewrites_each_part_of_a_chained_fix():
    entry = PastFix(
        entry_id="stub-chain",
        source=Source.INCIDENT,
        fault_type="ScalePod",
        target_service="demo-x",
        summary="deployment scaled to zero replicas",
        fix_command="kubectl scale deployment demo-x --replicas=1 "
        "&& kubectl rollout restart deployment/demo-x",
    )
    obs = observe(listing(deployment("media-service", replicas=0)))
    action = retrieval_only(DevStubRetriever(registry=REGISTRY, corpus=[entry]), obs, DEV, NS)
    assert action.command == (
        f"kubectl -n {NS} scale deployment media-service --replicas=1 "
        f"&& kubectl -n {NS} rollout restart deployment/media-service"
    )
    assert check_chain(action.command, POLICY, NS).admitted


def test_retrieval_only_replaces_the_recorded_service_name_as_a_whole_word():
    obs = [Observation(Kind.PORT_MISMATCH, "svc", "service svc port does not match", 0, 9090)]
    action = retrieval_only(_stub(), obs, DEV, NS)
    assert "demo-service-a" not in action.command
    assert "svc" in action.command


def test_replace_service_matches_whole_names_only():
    command = "kubectl scale deployment demo-service-b demo-service-b-extra xdemo-service-b"
    assert replace_service(command, "demo-service-b", "media") == (
        "kubectl scale deployment media demo-service-b-extra xdemo-service-b"
    )


def test_replace_service_handles_a_name_in_a_resource_path():
    assert replace_service("kubectl rollout status deployment/a-b", "a-b", "z") == (
        "kubectl rollout status deployment/z"
    )


def test_retrieval_only_escalates_without_an_observation():
    action = retrieval_only(_stub(), [], DEV, NS)
    assert action.command is None
    assert action.reason


def test_retrieval_only_does_not_add_a_second_namespace_flag():
    obs = observe(listing(deployment("media-service", replicas=0)))
    action = retrieval_only(_stub(), obs, DEV, NS)
    assert shlex.split(action.command).count("-n") == 1


@pytest.mark.parametrize(
    "recorded", [f"-n {NS}", f"--namespace {NS}", f"-n{NS}", f"--namespace={NS}"]
)
def test_retrieval_only_replaces_a_recorded_namespace(recorded):
    entry = PastFix(
        "stub-ns", Source.INCIDENT, "ScalePod", "demo-x", "deployment scaled to zero replicas",
        f"kubectl {recorded} scale deployment demo-x --replicas=1",
    )  # fmt: skip
    obs = observe(listing(deployment("geo", replicas=0)))
    action = retrieval_only(DevStubRetriever(REGISTRY, corpus=[entry]), obs, DEV, HOTEL)
    assert action.command == f"kubectl -n {HOTEL} scale deployment geo --replicas=1"


def test_a_pool_record_from_the_other_app_acts_in_the_problems_namespace():
    # a social-network ScalePod record retrieved for a hotel-reservation problem
    # built from a pool problem, so it is an evaluation-set touch, logged as a fixture- entry
    incidents = pool_incidents(load_registry(DEFAULT_REGISTRY_PATH))
    (entry,) = [e for e in incidents if e.entry_id == "pool-scale_pod_zero_social_net-mitigation-2"]
    assert f"-n {NS} " in entry.fix_command
    obs = observe(listing(deployment("geo", replicas=0)))
    action = retrieval_only(DevStubRetriever(REGISTRY, corpus=[entry]), obs, DEV, HOTEL)
    assert action.command == f"kubectl -n {HOTEL} scale deployment geo --replicas=1"
    verdict = check_chain(action.command, POLICY, HOTEL)
    assert verdict.admitted, verdict.reason


def test_a_recorded_namespace_after_the_exec_separator_is_kept():
    entry = PastFix(
        "stub-exec", Source.INCIDENT, "RevokeAuth", "mongodb-geo", "admin lost its role",
        f"kubectl -n {HOTEL} exec deploy/mongodb-geo -- tool -n keep",
    )  # fmt: skip
    obs = [Observation(Kind.SCALED_TO_ZERO, "mongodb-rate", "admin lost its role")]
    action = retrieval_only(DevStubRetriever(REGISTRY, corpus=[entry]), obs, DEV, NS)
    assert action.command == f"kubectl -n {NS} exec deploy/mongodb-rate -- tool -n keep"


def test_retrieval_only_passes_the_retrievers_refusal_on():
    obs = observe(listing(deployment("a", replicas=0)))
    with pytest.raises(RetrieverRefused):
        retrieval_only(_stub(), obs, POOL, NS)


def test_a_recorded_fix_the_allow_list_rejects_is_returned_for_the_harness_to_reject():
    # the recorded exec fix is not in the allow-list; the baseline does not filter, the harness does
    obs = [Observation(Kind.SCALED_TO_ZERO, "demo-database", "login fails user missing database")]
    action = retrieval_only(_stub(), obs, DEV, NS)
    assert action.command is not None
    assert not admitted(action)


def test_details_state_only_what_was_observed_not_a_cause():
    state = listing(
        deployment("a", replicas=0),
        deployment("b", node_selector={"x": "y"}),
        pod("b-1", {"app": "b"}, phase="Pending"),
        deployment("c"),
        service("c", target_port=1),
    )
    details = " ".join(o.detail for o in observe(state))
    assert "does not exist" not in details
    assert "does not match" not in details


def test_with_several_declared_ports_the_lowest_is_the_fix_value():
    # pins the shortcut in observe: the lowest declared port, which could be a metrics port
    item = deployment("a", port=9090)
    item["spec"]["template"]["spec"]["containers"][0]["ports"].append({"containerPort": 7})
    (found,) = observe(listing(item, service("a", target_port=1)))
    assert found.port == 7


class EmptyRetriever:
    def retrieve(self, query, problem_id, k):
        return []


class TwoHitRetriever:
    def retrieve(self, query, problem_id, k):
        return _stub().retrieve(query, DEV, k=2)


def test_retrieval_only_escalates_when_the_retriever_returns_no_hit():
    obs = observe(listing(deployment("a", replicas=0)))
    action = retrieval_only(EmptyRetriever(), obs, DEV, NS)
    assert action.command is None
    assert "no hit" in action.reason


def test_retrieval_only_uses_the_first_hit_when_a_retriever_returns_more_than_asked():
    obs = observe(listing(deployment("media-service", replicas=0)))
    action = retrieval_only(TwoHitRetriever(), obs, DEV, NS)
    assert action.command is not None
    assert "stub-002" in action.reason


@pytest.mark.parametrize(
    ("state", "entry_id"),
    [
        (listing(deployment("m", replicas=0)), "stub-002"),
        (
            listing(
                deployment("u", node_selector={"x": "y"}),
                pod("u-1", {"app": "u"}, phase="Pending"),
            ),
            "stub-003",
        ),
        (listing(deployment("p"), service("p", target_port=1)), "stub-001"),
    ],
)
def test_each_observed_symptom_retrieves_the_stub_entry_of_its_fault(state, entry_id):
    action = retrieval_only(_stub(), observe(state), DEV, NS)
    assert entry_id in action.reason


def test_a_scheduled_pod_still_pending_is_not_unschedulable():
    # Pending also covers a scheduled pod that is pulling its image
    state = listing(
        deployment("a", node_selector={"disk": "ssd"}),
        pod("a-1", {"app": "a"}, phase="Pending", scheduled=True),
    )
    assert observe(state) == []


def test_a_pending_pod_with_no_conditions_yet_is_not_unschedulable():
    state = listing(
        deployment("a", node_selector={"disk": "ssd"}),
        pod("a-1", {"app": "a"}, phase="Pending", conditions=False),
    )
    assert observe(state) == []


ROUND_TRIP_STATES = [
    listing(deployment("media-service", replicas=0)),
    listing(
        deployment("unique-id-service", node_selector={"kubernetes.io/hostname": "extra-node"}),
        pod("unique-id-service-1", {"app": "unique-id-service"}, phase="Pending"),
    ),
    listing(deployment("user-mention-service"), service("user-mention-service", 9999)),
]


@pytest.mark.parametrize("state", ROUND_TRIP_STATES)
def test_the_rule_command_survives_exec_shell_and_the_upstream_parser(state, upstream_parser):
    command = fault_type_rule(observe(state), NS).command
    assert upstream_parser.parse(exec_shell(command))["args"] == [command]


@pytest.mark.parametrize("state", ROUND_TRIP_STATES)
def test_the_retrieval_command_survives_exec_shell_and_the_upstream_parser(state, upstream_parser):
    command = retrieval_only(_stub(), observe(state), DEV, NS).command
    assert upstream_parser.parse(exec_shell(command))["args"] == [command]


def test_with_two_kinds_of_observation_the_command_is_the_fix_for_the_first():
    state = listing(
        deployment("aaa-service", replicas=0),
        deployment("bbb-service"),
        service("bbb-service", target_port=9999),
    )
    obs = observe(state)
    assert [(o.kind, o.service) for o in obs] == [
        (Kind.SCALED_TO_ZERO, "aaa-service"),
        (Kind.PORT_MISMATCH, "bbb-service"),
    ]
    action = retrieval_only(_stub(), obs, DEV, NS)
    assert shlex.split(action.command) == [
        "kubectl", "-n", NS, "scale", "deployment", "aaa-service", "--replicas=1"
    ]  # fmt: skip
    # the same service and fault kind as the rule baseline acts on
    assert fault_type_rule(obs, NS).command == action.command


def test_with_the_kinds_swapped_the_command_follows_the_first_observation():
    # a port mismatch listed first: the fix is for it, never for the scaled deployment
    port = "service bbb-service targetPort is not a declared container port"
    obs = [
        Observation(Kind.PORT_MISMATCH, "bbb-service", port, 0, 9090),
        Observation(Kind.SCALED_TO_ZERO, "aaa-service", "deployment aaa-service has 0 replicas"),
    ]
    action = retrieval_only(_stub(), obs, DEV, NS)
    assert "stub-001" in action.reason
    assert "bbb-service" in action.command
    assert "aaa-service" not in action.command
