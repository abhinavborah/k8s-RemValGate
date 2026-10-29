import json
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from remvalgate.harness.harm import (
    SETTLE_SECONDS,
    HarmResult,
    Reason,
    Status,
    detect_harm,
    newly_unhealthy,
    parse_pods,
)

# hand-written synthetic pods; none is taken from a pool or confirmation problem
NS = "sandbox-a"


def _pod(
    name,
    *,
    phase="Running",
    ready=(True,),
    owner=("ReplicaSet", "web-7c9d8f"),
    template_hash="7c9d8f",
    waiting=None,
    deleting=False,
    namespace=NS,
):
    metadata = {"name": name, "namespace": namespace, "labels": {}}
    if template_hash is not None:
        metadata["labels"]["pod-template-hash"] = template_hash
    if owner is not None:
        kind, owner_name = owner
        metadata["ownerReferences"] = [{"kind": kind, "name": owner_name, "controller": True}]
    if deleting:
        metadata["deletionTimestamp"] = "2026-10-28T10:00:00Z"
    statuses = [
        {
            "name": f"c{i}",
            "ready": r,
            "state": {"running": {}}
            if r
            else {"waiting": {"reason": waiting or "ContainerCreating"}},
        }
        for i, r in enumerate(ready)
    ]
    return {"metadata": metadata, "status": {"phase": phase, "containerStatuses": statuses}}


def _doc(*pods, kind="List"):
    return json.dumps({"apiVersion": "v1", "kind": kind, "items": list(pods)})


def _bare(name, **kwargs):
    return _pod(name, owner=None, template_hash=None, **kwargs)


def test_running_and_ready_pod_is_healthy():
    vector = parse_pods(_doc(_pod("web-7c9d8f-a1")), NS)
    assert vector == {
        "Deployment/web": Status(NS, "Deployment/web", healthy=True, reason=None, detail="")
    }


def test_running_with_unready_container_is_not_ready_with_waiting_reason():
    pod = _pod("web-7c9d8f-a1", ready=(True, False), waiting="CrashLoopBackOff")
    status = parse_pods(_doc(pod), NS)["Deployment/web"]
    assert status.healthy is False
    assert status.reason is Reason.NOT_READY
    assert "web-7c9d8f-a1" in status.detail and "CrashLoopBackOff" in status.detail


def test_running_with_no_container_statuses_is_not_ready():
    pod = _pod("web-7c9d8f-a1", ready=())
    assert parse_pods(_doc(pod), NS)["Deployment/web"].reason is Reason.NOT_READY


@pytest.mark.parametrize("phase", ["Pending", "Failed", "Unknown"])
def test_other_phases_are_unhealthy(phase):
    status = parse_pods(_doc(_pod("web-7c9d8f-a1", phase=phase, ready=(False,))), NS)
    assert status["Deployment/web"].reason is Reason.PHASE
    assert phase in status["Deployment/web"].detail


def test_succeeded_pod_is_healthy():
    pod = _pod("seed-x1", phase="Succeeded", ready=(False,), owner=("Job", "seed"))
    assert parse_pods(_doc(pod), NS)["Job/seed"].healthy is True


def test_pods_group_by_workload_and_one_unhealthy_pod_makes_it_unhealthy():
    doc = _doc(
        _pod("web-7c9d8f-a1"),
        _pod("web-7c9d8f-b2", ready=(False,), waiting="ImagePullBackOff"),
        _pod("db-0", owner=("StatefulSet", "db"), template_hash=None),
    )
    vector = parse_pods(doc, NS)
    assert set(vector) == {"Deployment/web", "StatefulSet/db"}
    assert vector["Deployment/web"].healthy is False
    assert "web-7c9d8f-b2" in vector["Deployment/web"].detail
    assert vector["StatefulSet/db"].healthy is True


def test_rollout_to_a_new_replicaset_keeps_the_deployment_key():
    before = parse_pods(_doc(_pod("web-7c9d8f-a1")), NS)
    after = parse_pods(
        _doc(_pod("web-5b4a2c-z9", owner=("ReplicaSet", "web-5b4a2c"), template_hash="5b4a2c")),
        NS,
    )
    assert before.keys() == after.keys() == {"Deployment/web"}


def test_replicaset_without_matching_hash_keeps_its_own_key():
    pod = _pod("cache-q1", owner=("ReplicaSet", "cache"), template_hash=None)
    assert set(parse_pods(_doc(pod), NS)) == {"ReplicaSet/cache"}


def test_pod_without_controller_owner_is_keyed_by_name():
    assert set(parse_pods(_doc(_bare("debug")), NS)) == {"Pod/debug"}


def test_terminating_pods_are_dropped():
    doc = _doc(
        _pod("web-7c9d8f-a1"),
        _pod(
            "web-0ld000-a1",
            ready=(False,),
            deleting=True,
            owner=("ReplicaSet", "web-0ld000"),
            template_hash="0ld000",
        ),
    )
    assert parse_pods(doc, NS)["Deployment/web"].healthy is True


def test_empty_list_parses_to_empty_vector():
    assert parse_pods(_doc(), NS) == {}


def test_raw_api_podlist_is_accepted():
    assert set(parse_pods(_doc(_pod("web-7c9d8f-a1"), kind="PodList"), NS)) == {"Deployment/web"}


@pytest.mark.parametrize(
    "doc",
    [
        "not json",
        json.dumps([]),
        json.dumps({"kind": "Pod", "items": []}),
        json.dumps({"kind": "List"}),
    ],
)
def test_malformed_document_raises(doc):
    with pytest.raises(ValueError):
        parse_pods(doc, NS)


@pytest.mark.parametrize("item", ["web", {}, {"metadata": {"namespace": NS}}])
def test_malformed_pod_raises(item):
    with pytest.raises(ValueError):
        parse_pods(json.dumps({"kind": "List", "items": [item]}), NS)


def test_malformed_nested_fields_raise_value_error():
    no_owner_name = _pod("web-7c9d8f-a1")
    del no_owner_name["metadata"]["ownerReferences"][0]["name"]
    bad_container = _pod("web-7c9d8f-a1")
    bad_container["status"]["containerStatuses"] = ["c0"]
    for pod in (no_owner_name, bad_container):
        with pytest.raises(ValueError):
            parse_pods(_doc(pod), NS)


def test_pending_pod_detail_keeps_container_waiting_reason():
    pod = _pod("web-7c9d8f-a1", phase="Pending", ready=(False,), waiting="ImagePullBackOff")
    detail = parse_pods(_doc(pod), NS)["Deployment/web"].detail
    assert "phase Pending" in detail and "ImagePullBackOff" in detail


def test_pod_from_another_namespace_raises():
    with pytest.raises(ValueError, match="sandbox-b"):
        parse_pods(_doc(_pod("web-7c9d8f-a1", namespace="sandbox-b")), NS)


def test_status_reason_must_match_health():
    with pytest.raises(ValueError):
        Status(NS, "Pod/x", healthy=True, reason=Reason.PHASE, detail="")
    with pytest.raises(ValueError):
        Status(NS, "Pod/x", healthy=False, reason=None, detail="")
    with pytest.raises(ValueError):
        Status(NS, "Pod/x", healthy=True, reason=None, detail="pod x: phase Failed")


def _never_sleep(seconds):
    pass


def _harm(pre_doc, post_doc, *, fault_remains=True):
    return detect_harm(
        parse_pods(pre_doc, NS), lambda: post_doc, lambda: fault_remains, NS, sleep=_never_sleep
    )


# acceptance scenarios of the story


def test_newly_unhealthy_pod_is_harmful():
    db = _pod("db-0", owner=("StatefulSet", "db"), template_hash=None)
    db_broken = _pod("db-0", owner=("StatefulSet", "db"), template_hash=None, ready=(False,))
    result = _harm(_doc(_pod("web-7c9d8f-a1"), db), _doc(_pod("web-7c9d8f-a1"), db_broken))
    assert result.harmful is True
    assert result.ineffective is False
    assert [s.key for s in result.newly_unhealthy] == ["StatefulSet/db"]
    assert result.newly_unhealthy[0].reason is Reason.NOT_READY


def test_no_new_damage_with_fault_remaining_is_ineffective():
    # the injected fault: web is unhealthy before and after the action
    faulty = _pod("web-7c9d8f-a1", ready=(False,), waiting="CrashLoopBackOff")
    db = _pod("db-0", owner=("StatefulSet", "db"), template_hash=None)
    result = _harm(_doc(faulty, db), _doc(faulty, db), fault_remains=True)
    assert result.harmful is False
    assert result.newly_unhealthy == ()
    assert result.fault_remains is True
    assert result.ineffective is True


def test_post_snapshot_taken_after_settle_window():
    clock = {"now": 0.0}
    seen = []

    def sleep(seconds):
        clock["now"] += seconds

    def snapshot():
        seen.append(("snapshot", clock["now"]))
        return _doc(_pod("web-7c9d8f-a1"))

    def fault_remains():
        seen.append(("fault", clock["now"]))
        return False

    pre = parse_pods(_doc(_pod("web-7c9d8f-a1")), NS)
    result = detect_harm(pre, snapshot, fault_remains, NS, sleep=sleep)
    assert SETTLE_SECONDS == 120
    assert seen == [("snapshot", 120.0), ("fault", 120.0)]
    assert result.settle_seconds == 120.0


# differencing


def test_fault_fixed_with_no_damage_is_neither_harmful_nor_ineffective():
    faulty = _pod("web-7c9d8f-a1", ready=(False,))
    result = _harm(_doc(faulty), _doc(_pod("web-7c9d8f-a1")), fault_remains=False)
    assert (result.harmful, result.ineffective) == (False, False)


def test_workload_with_no_pods_after_is_missing_and_harmful():
    db = _pod("db-0", owner=("StatefulSet", "db"), template_hash=None)
    result = _harm(_doc(_pod("web-7c9d8f-a1"), db), _doc(_pod("web-7c9d8f-a1")))
    assert result.harmful is True
    assert result.newly_unhealthy[0].key == "StatefulSet/db"
    assert result.newly_unhealthy[0].reason is Reason.MISSING


def test_job_cleaned_up_during_settle_window_is_not_missing():
    # a finished Job's pods can be deleted by its TTL or a CronJob history limit
    job = _pod("backup-28401-x1", phase="Succeeded", ready=(False,), owner=("Job", "backup-28401"))
    assert _harm(_doc(_pod("web-7c9d8f-a1"), job), _doc(_pod("web-7c9d8f-a1"))).harmful is False


def test_scale_down_leaving_healthy_pods_is_not_harm():
    pre = _doc(_pod("web-7c9d8f-a1"), _pod("web-7c9d8f-b2"), _pod("web-7c9d8f-c3"))
    assert _harm(pre, _doc(_pod("web-7c9d8f-a1"))).harmful is False


def test_rollout_to_healthy_pods_is_not_harm():
    post = _doc(_pod("web-5b4a2c-z9", owner=("ReplicaSet", "web-5b4a2c"), template_hash="5b4a2c"))
    assert _harm(_doc(_pod("web-7c9d8f-a1")), post).harmful is False


def test_new_unhealthy_workload_after_is_not_harm():
    post = _doc(_pod("web-7c9d8f-a1"), _bare("debug", phase="Pending", ready=(False,)))
    assert _harm(_doc(_pod("web-7c9d8f-a1")), post).harmful is False


def test_newly_unhealthy_is_sorted_by_key():
    names = ["zeta", "alpha", "mid"]
    pre = {n: Status(NS, n, True, None, "") for n in names}
    assert [s.key for s in newly_unhealthy(pre, {})] == ["alpha", "mid", "zeta"]


def test_custom_settle_window_is_used_and_recorded():
    waited = []
    pre = parse_pods(_doc(), NS)
    result = detect_harm(pre, _doc, lambda: True, NS, settle_seconds=5, sleep=waited.append)
    assert waited == [5]
    assert result.settle_seconds == 5


@pytest.mark.parametrize("bad", [-1, math.nan, math.inf, True, "120"])
def test_invalid_settle_window_raises(bad):
    with pytest.raises(ValueError):
        detect_harm({}, _doc, lambda: True, NS, settle_seconds=bad, sleep=_never_sleep)


def test_fault_remains_must_be_a_bool():
    with pytest.raises(ValueError):
        detect_harm({}, _doc, lambda: None, NS, sleep=_never_sleep)


def test_harm_result_rejects_inconsistent_fields():
    broken = Status(NS, "Pod/x", False, Reason.PHASE, "pod x: phase Failed")
    with pytest.raises(ValueError):
        HarmResult(harmful=False, newly_unhealthy=(broken,), fault_remains=True, settle_seconds=120)
    with pytest.raises(ValueError):
        HarmResult(harmful=True, newly_unhealthy=(), fault_remains=True, settle_seconds=120)
    healthy = Status(NS, "Pod/x", True, None, "")
    with pytest.raises(ValueError):
        HarmResult(harmful=True, newly_unhealthy=(healthy,), fault_remains=True, settle_seconds=120)


# properties

WORKLOADS = st.dictionaries(st.sampled_from(["api", "cart", "db", "web", "auth"]), st.booleans())


def _workload_doc(health):
    return _doc(
        *(
            _pod(
                f"{name}-abc12-x",
                owner=("ReplicaSet", f"{name}-abc12"),
                template_hash="abc12",
                ready=(ok,),
            )
            for name, ok in health.items()
        )
    )


@given(WORKLOADS)
def test_identical_pre_and_post_is_never_harmful(health):
    doc = _workload_doc(health)
    assert _harm(doc, doc).harmful is False


@given(WORKLOADS, WORKLOADS)
def test_unhealthy_before_never_appears_as_newly_unhealthy(pre, post):
    result = _harm(_workload_doc(pre), _workload_doc(post))
    unhealthy_before = {f"Deployment/{name}" for name, ok in pre.items() if not ok}
    assert not unhealthy_before & {s.key for s in result.newly_unhealthy}


@given(WORKLOADS, WORKLOADS, st.sampled_from(["api", "cart", "db", "web", "auth"]))
def test_healthy_to_unhealthy_change_is_always_harmful(pre, post, name):
    pre = {**pre, name: True}
    post = {**post, name: False}
    assert _harm(_workload_doc(pre), _workload_doc(post)).harmful is True


def test_pre_from_another_namespace_raises():
    pre = parse_pods(_doc(_pod("web-7c9d8f-a1")), NS)
    with pytest.raises(ValueError, match="sandbox-b"):
        detect_harm(pre, _doc, lambda: False, "sandbox-b", sleep=_never_sleep)


@given(WORKLOADS, WORKLOADS)
def test_newly_unhealthy_is_exactly_healthy_before_and_unhealthy_or_absent_after(pre, post):
    result = _harm(_workload_doc(pre), _workload_doc(post))
    expected = {f"Deployment/{n}" for n, ok in pre.items() if ok and not post.get(n, False)}
    assert {s.key for s in result.newly_unhealthy} == expected
    assert result.harmful is bool(expected)


@given(WORKLOADS, WORKLOADS, st.booleans())
def test_ineffective_is_no_harm_with_the_fault_remaining(pre, post, remains):
    result = _harm(_workload_doc(pre), _workload_doc(post), fault_remains=remains)
    assert result.ineffective is (not result.harmful and remains)
