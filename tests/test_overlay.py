import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from remvalgate import overlay
from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.harness.manifest import (
    DEFAULT_REGISTRY_PATH,
    SEAL_NAME,
    RunRefused,
    Slice,
    check_seal,
    load_registry,
)
from remvalgate.overlay import confirmation
from remvalgate.overlay.checks import has_available_replica, poll_until
from remvalgate.overlay.fixes import FIXES, exec_shell, known_fix, select


def _factory():
    return object()


def test_shipped_overlay_covers_the_development_slice_and_is_registered():
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    dev = {i for i, s in registry.slices.items() if s is Slice.DEVELOPMENT}
    assert dev <= set(overlay.PROBLEMS) <= set(registry.slices)
    assert overlay.register({"upstream-1": _factory}) == list(overlay.PROBLEMS)


def test_every_overlay_problem_has_a_known_fix():
    assert set(FIXES) == set(overlay.PROBLEMS)


def test_register_adds_entries_and_returns_their_ids():
    registry = {"upstream-1": _factory}
    added = overlay.register(registry, {"ours-1": _factory, "ours-2": _factory})
    assert added == ["ours-1", "ours-2"]
    assert set(registry) == {"upstream-1", "ours-1", "ours-2"}


def test_register_refuses_an_upstream_id_and_adds_nothing():
    registry = {"upstream-1": _factory}
    with pytest.raises(ValueError, match="upstream-1"):
        overlay.register(registry, {"ours-1": _factory, "upstream-1": _factory})
    assert list(registry) == ["upstream-1"]


_ids = st.sets(st.text(alphabet="abcdef-_0123456789", min_size=1, max_size=8), max_size=6)


@given(registry_ids=_ids, overlay_ids=_ids)
def test_register_is_the_union_or_a_refusal_that_changes_nothing(registry_ids, overlay_ids):
    registry = dict.fromkeys(registry_ids, _factory)
    before = dict(registry)
    problems = dict.fromkeys(overlay_ids, _factory)
    if registry_ids & overlay_ids:
        with pytest.raises(ValueError):
            overlay.register(registry, problems)
        assert registry == before
    else:
        overlay.register(registry, problems)
        assert set(registry) == registry_ids | overlay_ids


def test_importing_the_overlay_does_not_load_aiopslab():
    # aiopslab reads config.yml on import, which CI does not have
    code = (
        "import sys, remvalgate.overlay\n"
        "assert not any(m.startswith('aiopslab') for m in sys.modules)"
    )
    subprocess.run([sys.executable, "-I", "-c", code], check=True)


def test_every_overlay_id_builds_its_intended_problem():
    # dev_problems imports aiopslab, so its top-level names are read from the source
    tree = ast.parse(Path(overlay.__file__).with_name("dev_problems.py").read_text())
    defined = {n.name for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    built = {i: f.problem for i, f in overlay.PROBLEMS.items()}
    assert built == {
        "dev-k8s_target_port-misconfig-mitigation-1": "target_port_user_mention",
        "dev-scale_pod_zero_social_net-mitigation-1": "ScalePodMediaMitigation",
        "dev-assign_to_non_existent_node_social_net-mitigation-1": (
            "AssignNonExistentNodeUniqueIdMitigation"
        ),
        "pool-assign_to_non_existent_node_social_net-mitigation-1": (
            "AssignNonExistentNodeAvailableMitigation"
        ),
        "pool-k8s_target_port-misconfig-mitigation-4": "target_port_compose_post",
        "pool-k8s_target_port-misconfig-mitigation-5": "target_port_home_timeline",
        "pool-k8s_target_port-misconfig-mitigation-6": "target_port_user_timeline",
        "pool-scale_pod_zero_social_net-mitigation-2": "ScalePodTextMitigation",
        "pool-scale_pod_zero_social_net-mitigation-3": "ScalePodPostStorageMitigation",
        "pool-assign_to_non_existent_node_social_net-mitigation-2": (
            "AssignNonExistentNodeHomeTimelineMitigation"
        ),
        "pool-assign_to_non_existent_node_social_net-mitigation-3": (
            "AssignNonExistentNodeUserTimelineMitigation"
        ),
        "conf-k8s_target_port-misconfig-mitigation-1": "target_port_social_graph",
        "conf-scale_pod_zero_social_net-mitigation-1": "ScalePodSocialGraphMitigation",
        "conf-assign_to_non_existent_node_social_net-mitigation-1": (
            "AssignNonExistentNodeSocialGraphMitigation"
        ),
    }
    assert set(built.values()) <= defined


@pytest.mark.parametrize(
    ("deployment", "expected"),
    [
        (None, False),
        (SimpleNamespace(status=None), False),
        (SimpleNamespace(status=SimpleNamespace(available_replicas=None)), False),
        (SimpleNamespace(status=SimpleNamespace(available_replicas=0)), False),
        (SimpleNamespace(status=SimpleNamespace(available_replicas=1)), True),
    ],
)
def test_has_available_replica(deployment, expected):
    assert has_available_replica(deployment) is expected


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.mark.parametrize(
    ("ready_at", "deadline", "expected", "calls"),
    [
        (0, 10.0, True, 1),  # ready at once
        (2, 10.0, True, 3),  # ready on the third check, before the deadline
        (None, 10.0, False, 3),  # checks at 0, 5 and 10, then gives up
        (None, 0.0, False, 1),  # deadline already passed: one check, no wait
    ],
)
def test_poll_until(ready_at, deadline, expected, calls):
    clock = _Clock()
    seen = []

    def check():
        seen.append(clock.now)
        return ready_at is not None and len(seen) > ready_at

    assert poll_until(check, deadline, 5.0, clock, clock.sleep) is expected
    assert len(seen) == calls


def test_every_known_fix_survives_the_upstream_parser(upstream_parser):
    parser = upstream_parser
    for problem_id in FIXES:
        for command in known_fix(problem_id, "test-social-network"):
            assert parser.parse(exec_shell(command))["args"] == [command]


def test_a_command_ending_in_a_double_quote_is_refused_before_the_parser_loses_it(upstream_parser):
    parser = upstream_parser
    # the upstream behaviour the refusal guards against
    lossy = 'kubectl get pods -l "app=a"'
    reply = f'```\nexec_shell("{lossy.replace(chr(34), chr(92) + chr(34))}")\n```'
    assert parser.parse(reply)["args"] != [lossy]
    with pytest.raises(ValueError):
        exec_shell(lossy)


def test_the_target_port_fix_patches_to_the_shared_port():
    patch = FIXES["dev-k8s_target_port-misconfig-mitigation-1"][0][-1]
    assert patch == '[{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 9090}]'


@pytest.mark.parametrize(
    ("number", "service"),
    [(4, "compose-post-service"), (5, "home-timeline-service"), (6, "user-timeline-service")],
)
def test_each_pool_target_port_problem_has_its_own_service_and_the_shared_fix(number, service):
    problem_id = f"pool-k8s_target_port-misconfig-mitigation-{number}"
    (fix,) = FIXES[problem_id]
    assert fix[:3] == ["patch", "service", service]
    assert fix[-1] == FIXES["dev-k8s_target_port-misconfig-mitigation-1"][0][-1]
    # the service passed in the body of the factory registered for this id, read from the source
    tree = ast.parse(Path(overlay.__file__).with_name("dev_problems.py").read_text())
    (function,) = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == overlay.PROBLEMS[problem_id].problem
    ]
    passed = [
        k.value.value
        for n in ast.walk(function)
        if isinstance(n, ast.Call)
        for k in n.keywords
        if k.arg == "faulty_service" and isinstance(k.value, ast.Constant)
    ]
    assert passed == [service]
    assert load_registry(DEFAULT_REGISTRY_PATH).slices[problem_id] is Slice.POOL


@pytest.mark.parametrize(
    ("number", "cls", "service"),
    [(2, "ScalePodTextMitigation", "text-service"),
     (3, "ScalePodPostStorageMitigation", "post-storage-service")],
)  # fmt: skip
def test_each_pool_scale_pod_problem_has_its_own_service_and_scales_it_back(number, cls, service):
    problem_id = f"pool-scale_pod_zero_social_net-mitigation-{number}"
    assert FIXES[problem_id] == [
        ["scale", "deployment", service, "--replicas=1"],
        ["rollout", "status", f"deployment/{service}", "--timeout=300s"],
    ]
    _assert_sets_service_after_base(cls, "ScalePodSocialNetMitigation", service)
    assert load_registry(DEFAULT_REGISTRY_PATH).slices[problem_id] == Slice.POOL


def _assert_sets_service_after_base(cls: str, base: str, service: str) -> None:
    source = Path(overlay.__file__).with_name("dev_problems.py").read_text()
    (node,) = [n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == cls]
    assert [b.id for b in node.bases] == [base]
    (init,) = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"]
    # the base constructor hard-codes user-service, so the assignment must come after it
    assert [ast.unparse(s) for s in init.body] == [
        "super().__init__()",
        f"self.faulty_service = {service!r}",
    ]


def _faulty_service(path: Path, cls: str) -> str:
    # the one value the class assigns to self.faulty_service, read from its source
    (node,) = [
        n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef) and n.name == cls
    ]
    (value,) = [
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "self.faulty_service"
    ]
    # the confirmation classes assign confirmation.SERVICE
    return getattr(confirmation, value.attr) if isinstance(value, ast.Attribute) else value.value


def test_no_two_scale_pod_problems_share_a_service():
    dev_problems = Path(overlay.__file__).with_name("dev_problems.py")
    services = {
        problem_id: _faulty_service(dev_problems, factory.problem)
        for problem_id, factory in overlay.PROBLEMS.items()
        if "scale_pod_zero" in problem_id
    }
    # an upstream ScalePod problem keeps the service its base task sets
    upstream = [
        i for i in load_registry(DEFAULT_REGISTRY_PATH).slices
        if "scale_pod_zero" in i and i not in overlay.PROBLEMS
    ]  # fmt: skip
    base = (
        Path(__file__).resolve().parents[1]
        / "external/AIOpsLab/aiopslab/orchestrator/problems/scale_pod/scale_pod_social_net.py"
    )
    services |= {i: _faulty_service(base, "ScalePodSocialNetBaseTask") for i in upstream}
    assert len(set(services.values())) == len(services)


@pytest.mark.parametrize(
    ("number", "cls", "service"),
    [(2, "AssignNonExistentNodeHomeTimelineMitigation", "home-timeline-service"),
     (3, "AssignNonExistentNodeUserTimelineMitigation", "user-timeline-service")],
)  # fmt: skip
def test_each_pool_assign_node_problem_has_its_own_service_and_removes_the_selector(
    number, cls, service
):
    problem_id = f"pool-assign_to_non_existent_node_social_net-mitigation-{number}"
    assert FIXES[problem_id] == [
        [
            "patch", "deployment", service, "--type=json",
            "-p", '[{"op": "remove", "path": "/spec/template/spec/nodeSelector"}]',
        ],
        ["rollout", "status", f"deployment/{service}", "--timeout=300s"],
    ]  # fmt: skip
    # the stricter eval comes from the base, since the upstream eval passes with the fault in place
    _assert_sets_service_after_base(cls, "AssignNonExistentNodeAvailableMitigation", service)
    assert load_registry(DEFAULT_REGISTRY_PATH).slices[problem_id] == Slice.POOL


def test_no_two_assign_node_problems_share_a_service():
    dev_problems = Path(overlay.__file__).with_name("dev_problems.py")
    base = (
        Path(__file__).resolve().parents[1]
        / "external/AIOpsLab/aiopslab/orchestrator/problems/assign_non_existent_node"
        / "assign_non_existent_node_social_net.py"
    )
    services = {}
    for problem_id, factory in overlay.PROBLEMS.items():
        if "assign_to_non_existent_node" not in problem_id:
            continue
        # the pool variant sets no service and keeps the one its upstream base task sets
        if factory.problem == "AssignNonExistentNodeAvailableMitigation":
            services[problem_id] = _faulty_service(base, "AssignNonExistentNodeSocialNetBaseTask")
        else:
            services[problem_id] = _faulty_service(dev_problems, factory.problem)
    assert len(services) == 5
    assert len(set(services.values())) == len(services)


# the confirmation slice: static checks only, since building a confirmation problem in a test
# would be an evaluation-set touch (BRD 7.2)

REPO = Path(__file__).resolve().parents[1]
CONF_IDS = [
    "conf-k8s_target_port-misconfig-mitigation-1",
    "conf-scale_pod_zero_social_net-mitigation-1",
    "conf-assign_to_non_existent_node_social_net-mitigation-1",
]


def test_confirmation_ids_are_registered_and_only_in_the_confirmation_slice():
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    conf = [i for i, s in registry.slices.items() if s is Slice.CONFIRMATION]
    assert conf == CONF_IDS
    assert set(CONF_IDS) <= set(overlay.PROBLEMS)
    assert set(confirmation.SPEC) == set(CONF_IDS)


def test_each_confirmation_problem_targets_its_planned_service_and_base():
    tree = ast.parse(Path(overlay.__file__).with_name("dev_problems.py").read_text())
    names = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names[node.name] = {b.id for b in node.bases if isinstance(b, ast.Name)}
        elif isinstance(node, ast.FunctionDef):
            names[node.name] = {
                n.func.id
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
    planned = {
        "conf-k8s_target_port-misconfig-mitigation-1": "TargetPortMisconfig",
        "conf-scale_pod_zero_social_net-mitigation-1": "ScalePod",
        "conf-assign_to_non_existent_node_social_net-mitigation-1": "AssignNonExistentNode",
    }
    for problem_id in CONF_IDS:
        spec = confirmation.definition(problem_id, "a" * 40)
        assert spec["factory"] == overlay.PROBLEMS[problem_id].problem
        assert spec["type"] == planned[problem_id]
        assert spec["service"] == "social-graph-service"
        assert spec["namespace"] == "test-social-network"
        assert spec["base"] in names[overlay.PROBLEMS[problem_id].problem]
        # the factory itself takes its service from SERVICE, not from a literal of its own
        assert "confirmation.SERVICE" in spec["source"][0]
        assert "-service" not in spec["source"][0].replace("confirmation.SERVICE", "")
        # the known fix acts on the planned service and nothing else
        assert all("social-graph-service" in " ".join(args) for args in FIXES[problem_id])


def _recorded_checksums():
    text = (REPO / "docs" / "confirmation_problems.md").read_text(encoding="utf-8")
    return dict(re.findall(r"^\| `(conf-[^`]+)` \|.*\| `([0-9a-f]{64})` \|$", text, re.M))


def test_recorded_checksums_match_the_definitions():
    pin = pinned_commit(REPO)
    assert _recorded_checksums() == {i: confirmation.checksum(i, pin) for i in CONF_IDS}


# the committed seal, read and never written: a change that closes gate G1 fails here, not at the
# next pool run

SEAL = REPO / "runs" / SEAL_NAME
TOUCH_LOG = REPO / "runs" / "touch_log.jsonl"


def test_committed_seal_holds_for_the_definitions_and_the_pin():
    pin = pinned_commit(REPO)
    check_seal(load_registry(DEFAULT_REGISTRY_PATH), TOUCH_LOG, pin)
    assert json.loads(SEAL.read_text(encoding="utf-8"))["aiopslab"] == pin


def test_a_changed_sealed_checksum_closes_the_gate(tmp_path):
    seal = json.loads(SEAL.read_text(encoding="utf-8"))
    original = dict(seal["checksums"])
    seal["checksums"][CONF_IDS[1]] = "0" * 64
    (tmp_path / SEAL_NAME).write_text(json.dumps(seal), encoding="utf-8")
    shutil.copy(TOUCH_LOG, tmp_path / TOUCH_LOG.name)
    # the committed checksums stand in for the definitions, so this test covers the refusal only;
    # the test above covers the real definitions and the pin
    with pytest.raises(RunRefused, match=rf"changed since the seal \(gate G1\): {CONF_IDS[1]}$"):
        check_seal(
            load_registry(DEFAULT_REGISTRY_PATH),
            tmp_path / TOUCH_LOG.name,
            seal["aiopslab"],
            lambda i, _: original[i],
        )


@pytest.mark.parametrize(
    "field",
    [
        "problem_id", "slice", "factory", "type", "base", "service", "namespace", "fix",
        "aiopslab", "source", "checks",
    ],
)  # fmt: skip
def test_checksum_changes_when_any_part_of_the_definition_changes(field):
    record = confirmation.definition(CONF_IDS[0], "a" * 40)
    value = record[field]
    changed = {**record, field: value + (["x"] if isinstance(value, list) else "x")}
    assert confirmation.digest(changed) != confirmation.digest(record)
    assert confirmation.checksum(CONF_IDS[0], "a" * 40) == confirmation.digest(record)


def test_checksums_differ_between_the_confirmation_problems():
    assert len({confirmation.checksum(i, "a" * 40) for i in CONF_IDS}) == 3


def test_validation_selects_only_the_requested_ids():
    assert select(CONF_IDS, set(CONF_IDS)) == CONF_IDS
    assert select([CONF_IDS[1], CONF_IDS[1]], set(CONF_IDS)) == [CONF_IDS[1]]


def test_validation_with_no_ids_skips_the_confirmation_slice():
    expected = [i for i in overlay.PROBLEMS if i not in CONF_IDS]
    assert select([], set(CONF_IDS)) == expected


def test_validation_refuses_an_id_that_is_not_an_overlay_problem():
    with pytest.raises(ValueError, match="k8s_target_port-misconfig-mitigation-1"):
        select(["k8s_target_port-misconfig-mitigation-1"], set(CONF_IDS))


def test_checksum_covers_the_source_of_each_problem_and_its_overlay_bases():
    def heads(problem_id):
        return [
            src.splitlines()[0] for src in confirmation.definition(problem_id, "a" * 40)["source"]
        ]

    assert heads(CONF_IDS[0]) == [
        "def target_port_social_graph() -> K8STargetPortMisconfigMitigation:"
    ]
    assert heads(CONF_IDS[1]) == [
        "class ScalePodSocialGraphMitigation(ScalePodSocialNetMitigation):"
    ]
    assert heads(CONF_IDS[2]) == [
        "class AssignNonExistentNodeSocialGraphMitigation("
        "AssignNonExistentNodeAvailableMitigation):",
        "class AssignNonExistentNodeAvailableMitigation(AssignNonExistentNodeSocialNetMitigation):",
    ]
    record = confirmation.definition(CONF_IDS[0], "a" * 40)
    assert "def has_available_replica" in record["checks"]
    # read with universal newlines, so the checksum is the same on every system
    assert all(chr(13) not in s for s in [record["checks"], *record["source"]])


def test_checksum_source_follows_every_base_defined_in_the_overlay():
    text = "class M:\n    x = 1\n\n\nclass B:\n    y = 2\n\n\nclass A(B, M, Imported):\n    pass\n"
    heads = [src.splitlines()[0] for src in confirmation._sources("A", text)]
    assert heads == ["class A(B, M, Imported):", "class B:", "class M:"]
