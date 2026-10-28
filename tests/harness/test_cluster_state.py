import copy

from remvalgate.harness.cluster_state import changed, normalise

SERVICE = {
    "kind": "Service",
    "metadata": {
        "name": "user-mention-service",
        "labels": {"service": "user-mention-service"},
        "resourceVersion": "100",
        "managedFields": [{"manager": "kubectl"}],
    },
    "spec": {"ports": [{"port": 9090, "targetPort": 9090}], "clusterIP": "10.0.0.1"},
    "status": {"loadBalancer": {}},
}
DEPLOYMENT = {
    "kind": "Deployment",
    "metadata": {"name": "media-service", "generation": 3},
    "spec": {"replicas": 1, "template": {"metadata": {"creationTimestamp": None}}},
}


def _listing(*items):
    return {"items": [copy.deepcopy(i) for i in items]}


def test_equal_state_has_no_changes():
    state = normalise(_listing(SERVICE, DEPLOYMENT))
    assert changed(state, normalise(_listing(SERVICE, DEPLOYMENT))) == []


def test_a_changed_target_port_is_reported():
    faulted = copy.deepcopy(SERVICE)
    faulted["spec"]["ports"][0]["targetPort"] = 9999
    before, after = normalise(_listing(SERVICE)), normalise(_listing(faulted))
    assert changed(before, after) == ["Service/user-mention-service"]


def test_volatile_fields_and_status_are_ignored():
    bumped = copy.deepcopy(SERVICE)
    bumped["metadata"]["resourceVersion"] = "999"
    bumped["metadata"]["managedFields"] = []
    bumped["status"] = {"loadBalancer": {"ingress": []}}
    redeployed = copy.deepcopy(DEPLOYMENT)
    redeployed["metadata"]["generation"] = 4
    redeployed["spec"]["template"]["metadata"]["creationTimestamp"] = "2026-10-29T00:00:00Z"
    before = normalise(_listing(SERVICE, DEPLOYMENT))
    assert changed(before, normalise(_listing(bumped, redeployed))) == []


def test_a_deleted_or_added_object_is_reported():
    before = normalise(_listing(SERVICE, DEPLOYMENT))
    assert changed(before, normalise(_listing(SERVICE))) == ["Deployment/media-service"]
    assert changed(normalise(_listing(SERVICE)), before) == ["Deployment/media-service"]
