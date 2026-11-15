import copy
import json

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


def _apply(cluster, command=SCALE, fault_type="scale_pods_to_zero", *, passed, harm, mongo=None):
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
        mongo=mongo,
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


def test_an_empty_snapshot_puts_nothing_back_and_the_escalation_is_not_recovered():
    cluster = Cluster()
    command = f"kubectl delete pod media-service-abc -n {NS}"
    # the fault is gone, but an escalation counts as not recovered
    execution = _apply(cluster, command, passed=False, harm=_harm(False, False))
    assert cluster.calls == ["run", "harm", "check"]
    assert execution.escalated and execution.restore is None
    assert classify(execution.result) is Outcome.APPLIED_INEFFECTIVE


def test_a_service_only_snapshot_is_restored_with_no_rollout_wait():
    cluster = Cluster()
    service = {
        "kind": "Service",
        "metadata": {"name": "user-mention-service", "namespace": NS},
        "spec": {"ports": [{"port": 9090, "targetPort": 9090}]},
    }
    cluster.live[("Service", "user-mention-service")] = copy.deepcopy(service)
    command = f"kubectl patch svc user-mention-service -p '{{}}' -n {NS}"
    execution = _apply(cluster, command, "misconfig_k8s", passed=False, harm=_harm(False, True))
    assert cluster.calls == ["run", "harm", "check", "replace"]
    assert execution.restore.restored and execution.rollout_errors == ()


def test_a_rollout_that_fails_is_recorded_and_the_case_still_escalates():
    cluster = Cluster()

    def stuck(ref):
        raise RuntimeError("deadline exceeded")

    cluster.rolled_out = stuck
    execution = _apply(cluster, passed=False, harm=_harm(False, True))
    assert execution.rollout_errors == ("Deployment/media-service: deadline exceeded",)
    assert execution.escalated


@pytest.mark.parametrize("stage", ["run", "measure_harm", "check"])
def test_a_step_that_raises_restores_and_waits_before_the_error_propagates(stage):
    cluster = Cluster()

    def broken(*_):
        raise RuntimeError(f"{stage} failed")

    steps = {"run": cluster.run, "measure_harm": lambda: _harm(False, False), "check": lambda: True}
    steps[stage] = broken
    with pytest.raises(RuntimeError, match=f"{stage} failed") as raised:
        apply_with_check(
            SCALE,
            "scale_pods_to_zero",
            True,
            policy=POLICY,
            namespace=NS,
            get=cluster.get,
            put=cluster.put,
            rolled_out=cluster.rolled_out,
            **steps,
        )
    assert cluster.calls[-2:] == ["replace", "rollout media-service"]
    assert cluster.live[("Deployment", "media-service")]["spec"]["replicas"] == 0
    # the restore result travels with the error, so a failed restore is not silent
    assert "restored True" in raised.value.__notes__[0]


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


class Users:
    """A MongoDB whose admin user loses its role when the command runs; a restore grants it."""

    SAVED = [{"role": "readWrite", "db": "geo-db"}]

    def __init__(self, calls):
        self.roles, self.calls = list(self.SAVED), calls

    def __call__(self, deployment, script):
        if "grantRolesToUser" in script:
            self.calls.append(f"mongo restore {deployment}")
            self.roles = list(self.SAVED)
            return ""
        user = {"_id": "admin.admin", "user": "admin", "db": "admin", "roles": self.roles}
        return "REMVALGATE_USER " + json.dumps(user) + "\n"


@pytest.mark.parametrize("fault_type", ["revoke_auth", "storage_user_unregistered"])
def test_a_database_fault_with_mongo_restores_the_users_after_a_failed_check(fault_type):
    cluster = Cluster()
    users = Users(cluster.calls)
    original = cluster.run

    def run(command):
        users.roles = []
        return original(command)

    cluster.run = run
    execution = _apply(
        cluster, fault_type=fault_type, passed=False, harm=_harm(True, True), mongo=users
    )
    assert execution.escalated and execution.restore.restored
    # harm is measured before the restore
    assert cluster.calls.index("harm") < cluster.calls.index("mongo restore mongodb-geo")
    assert users.roles == Users.SAVED
    assert classify(execution.result) is Outcome.APPLIED_HARMFUL


def test_a_database_fault_with_mongo_keeps_a_change_that_passes_the_check():
    cluster = Cluster()
    users = Users(cluster.calls)
    execution = _apply(
        cluster, fault_type="revoke_auth", passed=True, harm=_harm(False, False), mongo=users
    )
    assert execution.restore is None
    assert not any(c.startswith("mongo restore") for c in cluster.calls)
