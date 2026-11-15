import json
from fractions import Fraction

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, check, check_chain, load_policy
from remvalgate.harness.harm import Reason, Status
from remvalgate.harness.manifest import Slice
from remvalgate.harness.oracle import (
    Clean,
    Label,
    ProblemLabels,
    coverage,
    dedup_key,
    fault_shows,
    label_problem,
    one_slice,
    originals,
    pass_at_k,
    problem_fault_type,
    read_labels,
    reset_namespace,
    smallest_k,
    summary,
)
from remvalgate.harness.outcome import Outcome
from remvalgate.overlay.fixes import CannotSend

# synthetic commands only: no pool or confirmation problem is read
POLICY = load_policy(DEFAULT_POLICY_PATH)
NS = "sandbox-a"
SCALE = f"kubectl -n {NS} scale deployment svc-a --replicas=1"
PORT_PATCH = '[{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 9090}]'
PATCH = f"kubectl -n {NS} patch service svc-a --type=json -p '{PORT_PATCH}'"
RESTART = f"kubectl -n {NS} rollout restart deployment/svc-b"
REJECTED = "allow-list: OUT_OF_SANDBOX"


def _same(a: str, b: str) -> bool:
    return dedup_key(a, POLICY) == dedup_key(b, POLICY)


# --- near-identical candidates -----------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        # spacing and quoting
        f"kubectl  -n {NS}   scale deployment svc-a --replicas=1",
        f"kubectl -n '{NS}' scale \"deployment\" svc-a '--replicas=1'",
        # --flag value and --flag=value, flags in another place or order
        f"kubectl -n {NS} scale deployment svc-a --replicas 1",
        f"kubectl scale deployment svc-a --replicas=1 -n {NS}",
        f"kubectl --namespace={NS} scale --replicas=1 deployment svc-a",
    ],
)
def test_near_identical_commands_share_a_key(variant):
    assert _same(SCALE, variant)


def test_a_json_patch_compares_as_parsed_json():
    reordered = '[{"value":9090,"path":"/spec/ports/0/targetPort","op":"replace"}]'
    assert _same(PATCH, f"kubectl -n {NS} patch service svc-a -p '{reordered}' --type json")


@pytest.mark.parametrize(
    "other",
    [
        # another value, also one that means the same number
        f"kubectl -n {NS} scale deployment svc-a --replicas=2",
        f"kubectl -n {NS} scale deployment svc-a --replicas=01",
        # another target or another spelling of the resource
        f"kubectl -n {NS} scale deployment svc-b --replicas=1",
        f"kubectl -n {NS} scale deploy svc-a --replicas=1",
        f"kubectl -n {NS} scale deployment/svc-a --replicas=1",
        # another namespace
        "kubectl -n sandbox-b scale deployment svc-a --replicas=1",
    ],
)
def test_commands_that_may_act_differently_stay_apart(other):
    assert not _same(SCALE, other)


def test_patch_bodies_and_flag_names_that_differ_stay_apart():
    two_ops = json.dumps([json.loads(PORT_PATCH)[0], {"op": "remove", "path": "/spec/x"}])
    swapped = json.dumps(list(reversed(json.loads(two_ops))))
    first = f"kubectl -n {NS} patch service svc-a --type=json -p '{two_ops}'"
    # the operations of a JSON patch are a list, which keeps its order
    assert not _same(first, f"kubectl -n {NS} patch service svc-a --type=json -p '{swapped}'")
    # the rule merges only commands that give their flags the same names
    assert not _same(PATCH, PATCH.replace(" -p ", " --patch "))


def test_a_repeated_flag_keeps_its_order():
    # the order of a repeated flag's values is kept, whatever kubectl makes of it
    one, two = '{"spec":{"replicas":1}}', '{"spec":{"replicas":2}}'
    a = f"kubectl -n {NS} patch deployment svc-a -p '{one}' -p '{two}'"
    b = f"kubectl -n {NS} patch deployment svc-a -p '{two}' -p '{one}'"
    assert not _same(a, b)


def test_an_exec_command_compares_token_for_token():
    script = "db.getUsers()"
    a = f"kubectl -n {NS} exec mongodb-a-1 -- mongo admin --eval '{script}'"
    b = f"kubectl -n {NS} exec mongodb-a-1 -- mongo admin --eval 'db.getUsers( )'"
    assert _same(a, f'kubectl  -n {NS} exec mongodb-a-1 -- mongo admin --eval "{script}"')
    assert not _same(a, b)


def test_a_chain_compares_part_by_part_in_order():
    chain = f"{SCALE} && {RESTART}"
    assert _same(
        chain, f"{SCALE.replace('=', ' ')} && kubectl  -n {NS} rollout restart deployment/svc-b"
    )
    assert not _same(chain, SCALE)
    assert not _same(chain, f"{RESTART} && {SCALE}")


def test_originals_points_each_command_at_its_first_twin():
    twin = SCALE.replace("=", " ")
    assert originals([SCALE, PATCH, twin, None, PATCH, RESTART], POLICY) == [0, 1, 0, None, 1, 5]


# --- labelling a problem ------------------------------------------------------------------------


class FakeRun:
    """Stands in for one execution with its reset: records each command and returns its outcome."""

    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        outcome = self.outcomes[command]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


CANDIDATES = [
    (0, SCALE, None),
    (1, "kubectl scale deployment svc-a --replicas=1", REJECTED),
    (2, SCALE.replace("=", " "), None),
    (3, PATCH, None),
    (4, None, "the reply has no non-empty command"),
]


# AC: each candidate gets an outcome label and the problem a flag for whether any worked; near
# identical candidates are deduplicated before labelling and take the label of their twin
def test_each_distinct_admitted_command_runs_once_and_twins_take_its_label():
    run = FakeRun({SCALE: Outcome.RECOVERED, PATCH: Outcome.APPLIED_INEFFECTIVE})
    labels = label_problem("p-synth", CANDIDATES, None, run, POLICY)
    assert run.calls == [SCALE, PATCH]
    assert [label.outcome for label in labels.candidates] == [
        Outcome.RECOVERED,
        None,
        Outcome.RECOVERED,
        Outcome.APPLIED_INEFFECTIVE,
        None,
    ]
    assert [label.duplicate_of for label in labels.candidates] == [None, None, 0, None, None]
    assert labels.candidates[1].rejection == REJECTED
    assert labels.candidates[4].rejection == "the reply has no non-empty command"
    assert (labels.n, labels.c) == (5, 2)
    assert labels.any_working_candidate
    assert labels.mixed
    assert labels.plan is None


def test_the_plans_command_is_labelled_apart_and_runs_last():
    run = FakeRun(
        {
            SCALE: Outcome.APPLIED_INEFFECTIVE,
            PATCH: Outcome.APPLIED_HARMFUL,
            RESTART: Outcome.RECOVERED,
        }
    )
    labels = label_problem("p-synth", CANDIDATES, (RESTART, None), run, POLICY)
    assert run.calls == [SCALE, PATCH, RESTART]
    assert labels.plan == Label(None, RESTART, None, None, Outcome.RECOVERED)
    # the plan's command is not one of the K, so its success is not the problem's flag
    assert (labels.n, labels.c, labels.any_working_candidate) == (5, 0, False)
    # a harmful execution is not working, even when it removed the fault
    assert not labels.mixed


def test_the_plans_command_takes_the_label_of_a_candidate_it_duplicates():
    run = FakeRun({SCALE: Outcome.RECOVERED, PATCH: Outcome.APPLIED_INEFFECTIVE})
    labels = label_problem("p-synth", CANDIDATES, (SCALE.replace("=", " "), None), run, POLICY)
    assert run.calls == [SCALE, PATCH]
    assert labels.plan.duplicate_of == 0
    assert labels.plan.outcome is Outcome.RECOVERED


def test_a_chained_plan_command_is_admitted_as_3c_admits_it_and_runs():
    chain = f"{SCALE} && {RESTART}"
    # sample admits the K with check, 3c's action path with check_chain
    assert not check(chain, POLICY, NS).admitted
    assert check_chain(chain, POLICY, NS).admitted
    run = FakeRun(
        {SCALE: Outcome.RECOVERED, PATCH: Outcome.APPLIED_INEFFECTIVE, chain: Outcome.RECOVERED}
    )
    labels = label_problem("p-synth", CANDIDATES, (chain, None), run, POLICY)
    assert run.calls == [SCALE, PATCH, chain]
    assert labels.plan == Label(None, chain, None, None, Outcome.RECOVERED)


def test_a_rejected_plan_command_is_never_run():
    run = FakeRun({SCALE: Outcome.RECOVERED, PATCH: Outcome.APPLIED_INEFFECTIVE})
    labels = label_problem("p-synth", CANDIDATES, (CANDIDATES[1][1], REJECTED), run, POLICY)
    assert run.calls == [SCALE, PATCH]
    assert labels.plan == Label(None, CANDIDATES[1][1], REJECTED, None, None)


def test_a_command_that_cannot_be_sent_has_no_outcome_and_its_twin_neither():
    run = FakeRun({SCALE: CannotSend("the parser changes the command"), PATCH: Outcome.RECOVERED})
    labels = label_problem("p-synth", CANDIDATES, None, run, POLICY)
    first, twin = labels.candidates[0], labels.candidates[2]
    assert first.outcome is None and twin.outcome is None
    assert (
        first.rejection
        == twin.rejection
        == ("cannot be sent unchanged: the parser changes the command")
    )
    assert twin.duplicate_of == 0
    # the candidates with no outcome are left out, so one working candidate is not mixed
    assert labels.c == 1 and not labels.mixed


def test_a_failed_reset_stops_the_labelling():
    run = FakeRun({SCALE: AssertionError("the reset left the cluster changed")})
    with pytest.raises(AssertionError, match="reset left"):
        label_problem("p-synth", CANDIDATES, None, run, POLICY)


def test_a_plan_with_no_diagnosis_has_no_candidates():
    labels = label_problem("p-synth", [], None, FakeRun({}), POLICY)
    assert (labels.n, labels.c, labels.any_working_candidate, labels.mixed) == (0, 0, False, False)


@pytest.mark.parametrize(
    "outcome, rejection",
    [
        (Outcome.RECOVERED, "a rejection as well"),
        (None, None),
        # an escalation is a system's outcome, never a command's
        (Outcome.ESCALATED_WITH_NONE, None),
        # a string read from a file is not an Outcome
        ("RECOVERED", None),
    ],
)
def test_impossible_labels_are_refused(outcome, rejection):
    with pytest.raises(ValueError):
        Label(0, SCALE, rejection, None, outcome)


def _labels(*outcomes) -> ProblemLabels:
    candidates = tuple(
        Label(seed, SCALE, REJECTED if o is None else None, None, o)
        for seed, o in enumerate(outcomes)
    )
    return ProblemLabels("p-synth", candidates, None)


@pytest.mark.parametrize(
    "outcomes, mixed",
    [
        ((Outcome.RECOVERED, Outcome.RECOVERED), False),
        ((Outcome.APPLIED_INEFFECTIVE, Outcome.APPLIED_HARMFUL), False),
        ((Outcome.RECOVERED, None), False),
        ((Outcome.RECOVERED, Outcome.APPLIED_INEFFECTIVE), True),
        ((Outcome.RECOVERED, Outcome.APPLIED_HARMFUL, None), True),
    ],
)
def test_mixed_needs_a_working_and_a_failed_execution(outcomes, mixed):
    assert _labels(*outcomes).mixed is mixed


def test_a_labels_record_reads_back():
    run = FakeRun(
        {
            SCALE: Outcome.RECOVERED,
            PATCH: Outcome.APPLIED_INEFFECTIVE,
            RESTART: Outcome.APPLIED_HARMFUL,
        }
    )
    labels = label_problem("p-synth", CANDIDATES, (RESTART, None), run, POLICY)
    record = json.loads(json.dumps(labels.record()))
    assert record["n"] == 5 and record["c"] == 2 and record["mixed"] is True
    assert read_labels(record | {"run_id": "oracle-p-synth"}) == labels


def test_a_record_whose_counts_disagree_with_its_labels_is_refused():
    record = _labels(Outcome.RECOVERED, Outcome.APPLIED_INEFFECTIVE).record() | {"c": 2}
    with pytest.raises(ValueError, match="disagree"):
        read_labels(record)


# --- coverage and the K rule --------------------------------------------------------------------


def _reference(n, c, k):
    # Chen et al. 2021 (arXiv 2107.03374), Figure 3, the numerically stable form
    if n - c < k:
        return 1.0
    return 1.0 - np.prod(1.0 - k / np.arange(n - c + 1, n + 1))


@pytest.mark.parametrize("n", range(1, 11))
def test_pass_at_k_matches_the_reference_implementation(n):
    for c in range(n + 1):
        for k in range(1, n + 1):
            assert float(pass_at_k(n, c, k)) == pytest.approx(_reference(n, c, k), abs=1e-12)


def test_pass_at_k_by_hand():
    # one working candidate in 8: a draw of 2 misses it with probability C(7, 2) / C(8, 2)
    assert pass_at_k(8, 1, 2) == 1 - Fraction(21, 28)
    assert pass_at_k(8, 0, 8) == 0
    # n - c < k: every draw of k holds a working candidate
    assert pass_at_k(8, 4, 5) == 1


@pytest.mark.parametrize(
    "n, c, k", [(8, 9, 1), (8, 1, 9), (8, 1, 0), (8, -1, 1), (8, 1, 1.0), (True, 1, 1)]
)
def test_pass_at_k_refuses_bad_counts(n, c, k):
    with pytest.raises(ValueError):
        pass_at_k(n, c, k)


def test_coverage_sums_over_problems_and_skips_problems_with_no_candidates():
    counts = [(8, 0), (8, 1), (8, 8), (0, 0)]
    assert coverage(counts, 1) == Fraction(1, 8) + 1
    assert coverage(counts, 8) == 2


# AC: the rule for K, stated before the labels are read: the smallest k whose coverage equals the
# coverage at k = 8
@pytest.mark.parametrize(
    "counts, k",
    [
        ([(8, 8), (8, 8)], 1),
        ([(8, 8), (8, 4)], 5),
        ([(8, 0), (8, 1), (8, 8)], 8),
        ([(8, 0), (8, 0)], 1),
        ([(8, 6), (0, 0)], 3),
    ],
)
def test_smallest_k(counts, k):
    assert smallest_k(counts) == k


@given(st.lists(st.integers(0, 8), min_size=1, max_size=20))
def test_smallest_k_is_the_closed_form(cs):
    # with n = 8 everywhere, coverage at k is full once k > 8 - c for every problem with c >= 1
    counts = [(8, c) for c in cs]
    working = [c for c in cs if c]
    assert smallest_k(counts) == (8 - min(working) + 1 if working else 1)
    values = [coverage(counts, k) for k in range(1, 9)]
    assert values == sorted(values)
    assert values[-1] == len(working)


def test_the_k_rule_needs_one_k():
    with pytest.raises(ValueError, match="different K"):
        smallest_k([(8, 1), (4, 1)])
    with pytest.raises(ValueError, match="no problem has candidates"):
        smallest_k([(0, 0)])


# AC: the number of problems with mixed outcomes and the coverage for k = 1 to 8 are reported
def test_summary():
    problems = [
        ProblemLabels(f"p-{i}", _labels(*outcomes).candidates, None)
        for i, outcomes in enumerate(
            [
                (Outcome.RECOVERED, Outcome.APPLIED_INEFFECTIVE),
                (Outcome.APPLIED_INEFFECTIVE, None),
                (Outcome.RECOVERED, Outcome.RECOVERED),
            ]
        )
    ]
    problems.append(ProblemLabels("p-none", (), None))
    result = summary(problems)
    assert result == {
        "problems": 4,
        "k": 2,
        "no_candidates": 1,
        "with_working_candidate": 2,
        "mixed": 1,
        "candidates": 6,
        "without_outcome": 1,
        "duplicates": 0,
        "coverage": {1: 1.5, 2: 2.0},
        "smallest_k": 2,
    }


def test_summary_refuses_a_problem_labelled_twice():
    labels = _labels(Outcome.RECOVERED)
    with pytest.raises(ValueError, match="more than once"):
        summary([labels, labels])


def test_summary_of_problems_with_no_candidates():
    result = summary([ProblemLabels("p-none", (), None)])
    assert (result["k"], result["coverage"], result["smallest_k"]) == (0, {}, None)


# --- when the fault shows -----------------------------------------------------------------------


def _status(key, reason=None):
    if reason is None:
        return {key: Status(NS, key, True, None, "")}
    return {key: Status(NS, key, False, reason, "pod x: not healthy")}


@pytest.mark.parametrize(
    "fault_type, service, status, shows",
    [
        ("TargetPortMisconfig", "svc-a", _status("Deployment/svc-a"), True),
        ("ScalePod", "svc-a", _status("Deployment/svc-a"), False),
        ("ScalePod", "svc-a", _status("Deployment/svc-b"), True),
        ("AssignNonExistentNode", "svc-a", _status("Deployment/svc-a"), False),
        ("AssignNonExistentNode", "svc-a", _status("Deployment/svc-a", Reason.PHASE), True),
        ("AssignNonExistentNode", "svc-a", {}, False),
        # a database fault shows in the client of the database, not in the database
        ("RevokeAuth", "mongodb-geo", _status("Deployment/mongodb-geo", Reason.NOT_READY), False),
        ("RevokeAuth", "mongodb-geo", _status("Deployment/geo", Reason.NOT_READY), True),
        ("UserUnregistered", "mongodb-rate", _status("Deployment/rate"), False),
        ("UserUnregistered", "mongodb-rate", _status("Deployment/rate", Reason.NOT_READY), True),
        # the injector deletes the client's pods whatever its script did, so a client pod still
        # being created does not show that the fault took
        ("RevokeAuth", "mongodb-geo", _status("Deployment/geo", Reason.PHASE), False),
        ("UserUnregistered", "mongodb-rate", _status("Deployment/rate", Reason.PHASE), False),
        # the singleton rules (#73)
        ("MisconfigApp", "geo", _status("Deployment/geo"), False),
        ("MisconfigApp", "geo", _status("Deployment/geo", Reason.NOT_READY), True),
        ("MisconfigApp", "geo", _status("Deployment/geo", Reason.PHASE), True),
        (
            "AuthMissMongoDB",
            "url-shorten-mongodb",
            _status("Deployment/url-shorten-service"),
            False,
        ),
        (
            "AuthMissMongoDB",
            "url-shorten-mongodb",
            _status("Deployment/url-shorten-service", Reason.NOT_READY),
            True,
        ),
        (
            "AuthMissMongoDB",
            "url-shorten-mongodb",
            _status("Deployment/url-shorten-mongodb", Reason.NOT_READY),
            False,
        ),
        ("WrongBinUsage", "profile", {}, False),
        ("WrongBinUsage", "profile", _status("Deployment/profile", Reason.PHASE), False),
        ("WrongBinUsage", "profile", _status("Deployment/profile"), True),
        ("WrongBinUsage", "profile", _status("Deployment/profile", Reason.NOT_READY), True),
    ],
)
def test_fault_shows(fault_type, service, status, shows):
    assert fault_shows(fault_type, service, status) is shows


def test_fault_shows_refuses_an_unknown_fault_type():
    with pytest.raises(ValueError, match="unknown fault type"):
        fault_shows("PodKill", "svc-a", {})


# --- the reset between executions ---------------------------------------------------------------

DEPLOY = {"spec": {"replicas": 1}}
SERVICE = {"spec": {"ports": [{"targetPort": 8080}]}}
CLEAN = Clean(
    objects={"Deployment/svc-a": DEPLOY, "Service/svc-a": SERVICE},
    sent={"Deployment/svc-a": {"sent": "deploy"}, "Service/svc-a": {"sent": "service"}},
    workloads=frozenset({"Deployment/svc-a"}),
)


class FakeNamespace:
    """Stands in for the kubectl calls of one reset: live returns each listing in turn, and every
    call is recorded in order."""

    def __init__(self, listings, eval_succeeds=True):
        self.listings = iter(listings)
        self.succeeds = eval_succeeds
        self.calls = []

    def reset(self, problem_id):
        reset_namespace(
            problem_id,
            CLEAN,
            recover=lambda: self.calls.append("recover"),
            live=lambda: next(self.listings),
            put=lambda verb, obj: self.calls.append((verb, obj)),
            rolled_out=lambda ref: self.calls.append(("rolled_out", ref)),
            wait_healthy=lambda: self.calls.append("wait_healthy"),
            eval_succeeds=lambda: self.calls.append("eval") or self.succeeds,
        )


SCALE_ID = "scale_pod_zero-synth"
REVOKE_ID = "revoke_auth-synth"


def test_a_reset_with_nothing_left_changed_puts_nothing_back():
    fake = FakeNamespace([CLEAN.objects, CLEAN.objects])
    fake.reset(SCALE_ID)
    assert fake.calls == ["recover", "wait_healthy"]


def test_a_reset_puts_back_what_a_command_changed_and_waits_for_deployments_only():
    left = {"Deployment/svc-a": {"spec": {"replicas": 3}}, "Service/svc-a": {"spec": {}}}
    fake = FakeNamespace([left, CLEAN.objects])
    fake.reset(SCALE_ID)
    assert fake.calls == [
        "recover",
        ("replace", {"sent": "deploy"}),
        ("rolled_out", ("Deployment", "svc-a")),
        ("replace", {"sent": "service"}),
        "wait_healthy",
    ]


@pytest.mark.parametrize(
    "live",
    [
        # an object a command added, and one it removed: neither is put back
        {**CLEAN.objects, "Deployment/svc-new": DEPLOY},
        {"Deployment/svc-a": DEPLOY},
    ],
)
def test_a_reset_stops_on_an_object_added_or_removed(live):
    fake = FakeNamespace([live, live])
    with pytest.raises(AssertionError, match="reset left the namespace changed"):
        fake.reset(SCALE_ID)
    assert not [c for c in fake.calls if isinstance(c, tuple)]


def test_a_reset_stops_when_the_put_back_leaves_an_object_changed():
    left = {**CLEAN.objects, "Service/svc-a": {"spec": {}}}
    fake = FakeNamespace([left, left])
    with pytest.raises(AssertionError, match=r"Service/svc-a"):
        fake.reset(SCALE_ID)
    assert ("replace", {"sent": "service"}) in fake.calls


def test_only_a_database_problem_runs_its_eval_after_the_reset():
    fake = FakeNamespace([CLEAN.objects, CLEAN.objects])
    fake.reset(REVOKE_ID)
    assert fake.calls == ["recover", "wait_healthy", "eval"]
    failing = FakeNamespace([CLEAN.objects, CLEAN.objects], eval_succeeds=False)
    with pytest.raises(AssertionError, match="eval fails after the reset"):
        failing.reset(REVOKE_ID)


# --- one slice per summary ----------------------------------------------------------------------

SLICES = {"p-a": Slice.POOL, "p-b": Slice.POOL, "d-a": Slice.DEVELOPMENT}


def test_one_slice_accepts_problems_of_one_slice():
    one_slice(["p-a", "p-b"], SLICES)
    one_slice(["d-a"], SLICES)


def test_one_slice_refuses_the_pool_with_a_dry_run():
    with pytest.raises(ValueError, match="mix slices: development, pool"):
        one_slice(["p-a", "d-a"], SLICES)


def test_one_slice_refuses_a_problem_outside_the_registry():
    with pytest.raises(ValueError, match="not in the registry: x"):
        one_slice(["p-a", "x"], SLICES)


def test_problem_fault_type_reads_the_singleton_stems_apart_from_the_pool():
    assert problem_fault_type("auth_miss_mongodb-mitigation-1") == "AuthMissMongoDB"
    assert problem_fault_type("misconfig_app_hotel_res-mitigation-1") == "MisconfigApp"
    assert problem_fault_type("wrong_bin_usage-mitigation-1") == "WrongBinUsage"
    assert problem_fault_type("conf-scale_pod_zero_social_net-mitigation-1") == "ScalePod"
    # the singletons not run have no type, and the runner refuses them before any touch
    for skipped in ("redeploy_without_PV-mitigation-1", "astronomy_shop_kafka_queue_problems-1"):
        with pytest.raises(ValueError):
            problem_fault_type(skipped)
