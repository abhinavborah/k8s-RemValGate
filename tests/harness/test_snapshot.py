import copy

import pytest

from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy
from remvalgate.harness.snapshot import (
    OUT_OF_SCOPE,
    restore,
    strip,
    take,
    touched,
)

POLICY = load_policy(DEFAULT_POLICY_PATH)
NS = "test-social-network"
SERVICE = {
    "kind": "Service",
    "metadata": {
        "name": "user-mention-service",
        "namespace": NS,
        "labels": {"service": "user-mention-service"},
        "uid": "u1",
        "resourceVersion": "10",
        "managedFields": [{"manager": "kubectl"}],
    },
    "spec": {"ports": [{"port": 9090, "targetPort": 9090}], "clusterIP": "10.0.0.1"},
    "status": {"loadBalancer": {}},
}
DEPLOYMENT = {
    "kind": "Deployment",
    "metadata": {"name": "text-service", "namespace": NS, "generation": 2},
    "spec": {"replicas": 1, "template": {"spec": {"containers": [{"name": "c"}]}}},
    "status": {"readyReplicas": 1},
}


class Cluster:
    """A fake API server holding the live objects by (kind, name)."""

    def __init__(self, *objects):
        self.live = {(o["kind"], o["metadata"]["name"]): copy.deepcopy(o) for o in objects}
        self.writes = []

    def get(self, ref):
        return copy.deepcopy(self.live.get(ref))

    def put(self, action, obj):
        self.writes.append(action)
        self.live[(obj["kind"], obj["metadata"]["name"])] = copy.deepcopy(obj)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            f"kubectl scale deployment text-service --replicas=2 -n {NS}",
            [("Deployment", "text-service")],
        ),
        (
            f"kubectl scale deploy/a deploy/b --replicas=2 -n {NS}",
            [("Deployment", "a"), ("Deployment", "b")],
        ),
        (
            f"kubectl patch svc user-mention-service -p '{{}}' -n {NS}",
            [("Service", "user-mention-service")],
        ),
        (
            f"kubectl rollout restart deployment text-service -n {NS}",
            [("Deployment", "text-service")],
        ),
        (f"kubectl rollout undo deployment text-service -n {NS}", [("Deployment", "text-service")]),
        # no snapshot: read only, a pod the controller recreates, a ReplicaSet its Deployment resets
        (f"kubectl get deployment text-service -n {NS}", []),
        (f"kubectl delete pod text-service-abc -n {NS}", []),
        (f"kubectl scale rs text-service-abc --replicas=2 -n {NS}", []),
    ],
)
def test_touched_reads_the_targets_from_the_command(command, expected):
    assert touched(command, POLICY, NS) == expected


def test_touched_refuses_a_command_the_allow_list_rejects():
    with pytest.raises(ValueError, match="OUT_OF_SANDBOX"):
        touched("kubectl scale deployment text-service --replicas=2 -n kube-system", POLICY, NS)


def test_strip_removes_status_and_server_fields_and_keeps_the_rest():
    stripped = strip(SERVICE)
    assert "status" not in stripped
    assert stripped["metadata"] == {
        "name": "user-mention-service",
        "namespace": NS,
        "labels": {"service": "user-mention-service"},
    }
    assert stripped["spec"] == SERVICE["spec"]
    assert "uid" in SERVICE["metadata"], "the input is not changed"


def test_take_leaves_out_an_object_that_does_not_exist():
    cluster = Cluster(DEPLOYMENT)
    refs = [("Deployment", "text-service"), ("Deployment", "typo-service")]
    assert list(take(NS, refs, cluster.get).objects) == [("Deployment", "text-service")]


def test_restore_undoes_a_patch_that_added_a_field():
    cluster = Cluster(SERVICE, DEPLOYMENT)
    refs = [("Deployment", "text-service")]
    snapshot = take(NS, refs, cluster.get)
    # a patch adds a field that no earlier manager owned, and the generation moves
    cluster.live[refs[0]]["spec"]["template"]["spec"]["nodeSelector"] = {"absent": "x"}
    cluster.live[refs[0]]["metadata"]["generation"] = 3
    result = restore("scale_pods_to_zero", snapshot, cluster.get, cluster.put)
    assert result.restored
    assert cluster.writes == ["replace"]
    assert "nodeSelector" not in cluster.live[refs[0]]["spec"]["template"]["spec"]


def test_restore_creates_an_object_the_command_deleted_without_its_old_address():
    cluster = Cluster(SERVICE)
    ref = ("Service", "user-mention-service")
    snapshot = take(NS, [ref], cluster.get)
    del cluster.live[ref]
    result = restore("misconfig_k8s", snapshot, cluster.get, cluster.put)
    assert cluster.writes == ["create"]
    assert result.recreated == (ref,)
    assert "clusterIP" not in cluster.live[ref]["spec"]
    # the new address is not compared, so the created-again Service still counts as restored
    assert result.restored


def test_restore_reports_an_object_that_still_differs():
    cluster = Cluster(DEPLOYMENT)
    ref = ("Deployment", "text-service")
    snapshot = take(NS, [ref], cluster.get)

    def put(action, obj):
        # an admission webhook, say, rewrites the object after the write
        obj["spec"]["replicas"] = 5
        cluster.put(action, obj)

    cluster.live[ref]["spec"]["replicas"] = 3
    result = restore("scale_pods_to_zero", snapshot, cluster.get, put)
    assert not result.restored
    assert result.changed == ("Deployment/text-service",)


@pytest.mark.parametrize("fault_type", sorted(OUT_OF_SCOPE))
def test_out_of_scope_faults_are_not_restored_and_touch_nothing(fault_type):
    cluster = Cluster(DEPLOYMENT)
    calls = []

    def get(ref):
        calls.append(ref)
        return cluster.get(ref)

    snapshot = take(NS, [("Deployment", "text-service")], cluster.get)
    result = restore(fault_type, snapshot, get, cluster.put)
    assert not result.attempted
    assert not result.restored
    assert calls == []
    assert cluster.writes == []


def test_an_unknown_fault_type_is_refused():
    with pytest.raises(ValueError, match="made_up"):
        restore("made_up", take(NS, [], Cluster().get), Cluster().get, Cluster().put)
