import copy

import pytest

from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy
from remvalgate.harness.execute import apply_with_check
from remvalgate.harness.harm import HarmResult, Reason, Status
from remvalgate.harness.outcome import Outcome, classify

POLICY = load_policy(DEFAULT_POLICY_PATH)
NS = "test-social-network"
DEPLOYMENT = {
    "kind": "Deployment",
    "metadata": {"name": "media-service", "namespace": NS},
    "spec": {"replicas": 0, "template": {"spec": {"containers": [{"name": "c"}]}}},
}
SCALE = f"kubectl scale deployment media-service --replicas=1 -n {NS}"
EXEC = (
    f"kubectl exec user-mongodb-abc -n {NS} -- mongo admin "
    '--eval \'db.grantRolesToUser("u",["root"])\''
)


class Cluster:
    """A fake API server whose run scales media-service and records every call."""

    def __init__(self):
        self.live = {("Deployment", "media-service"): copy.deepcopy(DEPLOYMENT)}
        self.calls = []

    def get(self, ref):
        return copy.deepcopy(self.live.get(ref))

    def put(self, action, obj):
        self.calls.append(action)
        self.live[(obj["kind"], obj["metadata"]["name"])] = copy.deepcopy(obj)

    def run(self, command):
        self.calls.append("run")
        if "scale" in command:
            self.live[("Deployment", "media-service")]["spec"]["replicas"] = 1
        return 0

    def rolled_out(self, ref):
        self.calls.append(f"rollout {ref[1]}")


def _harm(harmful, fault_remains):
    broken = Status(NS, "Deployment/text-service", False, Reason.PHASE, "pod t-1: phase Pending")
    return HarmResult(
        harmful=harmful,
        newly_unhealthy=(broken,) if harmful else (),
        fault_remains=fault_remains,
        settle_seconds=120.0,
    )


def _apply(cluster, command=SCALE, fault_type="scale_pods_to_zero", *, passed, harm):
    def measure():
        cluster.calls.append("harm")
        return harm

    def check():
        cluster.calls.append("check")
        return passed

    return apply_with_check(
        command,
        fault_type,
        True,
        policy=POLICY,
        namespace=NS,
        get=cluster.get,
        put=cluster.put,
        run=cluster.run,
        measure_harm=measure,
        check=check,
        rolled_out=cluster.rolled_out,
    )


def test_a_passing_check_keeps_the_change_and_records_recovery():
    cluster = Cluster()
    execution = _apply(cluster, passed=True, harm=_harm(False, False))
    assert cluster.calls == ["run", "harm", "check"]
    assert cluster.live[("Deployment", "media-service")]["spec"]["replicas"] == 1
    assert not execution.escalated and execution.restore is None
    assert classify(execution.result) is Outcome.RECOVERED


@pytest.mark.parametrize(
    ("harmful", "expected"),
    [(True, Outcome.APPLIED_HARMFUL), (False, Outcome.APPLIED_INEFFECTIVE)],
)
def test_a_failed_check_restores_then_escalates(harmful, expected):
    cluster = Cluster()
    # the fault is gone, but a restored action is never recovered
    execution = _apply(cluster, passed=False, harm=_harm(harmful, False))
    # harm is read before the restore, and the restored Deployment is waited for
    assert cluster.calls == ["run", "harm", "check", "replace", "rollout media-service"]
    assert cluster.live[("Deployment", "media-service")]["spec"]["replicas"] == 0
    assert execution.escalated and execution.restore.restored
    assert execution.result.applied
    assert classify(execution.result) is expected


@pytest.mark.parametrize("fault_type", ["revoke_auth", "storage_user_unregistered"])
def test_an_out_of_scope_fault_escalates_without_a_restore(fault_type):
    cluster = Cluster()
    execution = _apply(cluster, fault_type=fault_type, passed=False, harm=_harm(True, True))
    assert "replace" not in cluster.calls
    assert cluster.live[("Deployment", "media-service")]["spec"]["replicas"] == 1
    assert execution.escalated and execution.restore is None
    # harm is still measured
    assert classify(execution.result) is Outcome.APPLIED_HARMFUL


def test_an_exec_no_snapshot_holds_escalates_without_a_restore():
    cluster = Cluster()
    execution = _apply(cluster, EXEC, "misconfig_k8s", passed=False, harm=_harm(False, True))
    assert cluster.calls == ["run", "harm", "check"]
    assert execution.escalated and execution.restore is None
    assert classify(execution.result) is Outcome.APPLIED_INEFFECTIVE


def test_an_empty_snapshot_puts_nothing_back_and_the_end_state_decides():
    cluster = Cluster()
    command = f"kubectl delete pod media-service-abc -n {NS}"
    execution = _apply(cluster, command, passed=False, harm=_harm(False, False))
    assert cluster.calls == ["run", "harm", "check"]
    assert execution.escalated and execution.restore is None
    assert classify(execution.result) is Outcome.RECOVERED


def test_a_rollout_that_fails_is_recorded_and_the_case_still_escalates():
    cluster = Cluster()

    def stuck(ref):
        raise RuntimeError("deadline exceeded")

    cluster.rolled_out = stuck
    execution = _apply(cluster, passed=False, harm=_harm(False, True))
    assert execution.rollout_errors == ("Deployment/media-service: deadline exceeded",)
    assert execution.escalated


def test_a_measurement_that_raises_restores_before_the_error_propagates():
    cluster = Cluster()

    def broken():
        raise RuntimeError("kubectl get pods failed")

    with pytest.raises(RuntimeError, match="get pods"):
        apply_with_check(
            SCALE,
            "scale_pods_to_zero",
            True,
            policy=POLICY,
            namespace=NS,
            get=cluster.get,
            put=cluster.put,
            run=cluster.run,
            measure_harm=broken,
            check=lambda: True,
            rolled_out=cluster.rolled_out,
        )
    assert cluster.calls == ["run", "replace"]
    assert cluster.live[("Deployment", "media-service")]["spec"]["replicas"] == 0


def test_a_rejected_command_never_runs():
    cluster = Cluster()
    command = "kubectl scale deployment media-service --replicas=1 -n kube-system"
    with pytest.raises(ValueError, match="allow-list"):
        _apply(cluster, command, passed=True, harm=_harm(False, False))
    assert cluster.calls == []


def test_an_unknown_fault_type_is_refused_before_the_command_runs():
    cluster = Cluster()
    with pytest.raises(ValueError, match="unknown fault type"):
        _apply(cluster, fault_type="network_delay", passed=True, harm=_harm(False, False))
    assert cluster.calls == []
