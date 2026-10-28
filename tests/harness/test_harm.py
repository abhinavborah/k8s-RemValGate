import json

import pytest

from remvalgate.harness.harm import (
    Reason,
    Status,
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
