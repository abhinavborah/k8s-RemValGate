"""Apply one candidate with a snapshot, a check and a restore on failure (story E8-S2, R8.2).

apply_with_check snapshots the Deployments and Services the command touches, runs it, measures
harm, then runs the check: target-namespace readiness and the problem-specific probes, which is
AIOpsLab's mitigation eval. A passing check keeps the change. A failing one restores the snapshot
and escalates. For RevokeAuth and UserUnregistered, whose state is not a Kubernetes object, a
failing check restores the MongoDB users and any Deployment or Service the command touched when
a mongo exec is given (mongo_snapshot.py), and escalates without a restore when it is not. A
command no snapshot can undo escalates without a restore. Harm is measured in every case, before
any restore, since it describes what the action did.

The result is recorded with applied_result: the candidate ran, so it is applied even when it was
restored and escalated. An escalated candidate is never recovered, whether or not anything was
restored, since an escalation counts as not recovered.

The kubectl calls and the eval are injected, as in harm.py and snapshot.py, so CI tests the logic
without a cluster.
"""

import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace

from remvalgate.harness import mongo_snapshot
from remvalgate.harness.allowlist import Policy
from remvalgate.harness.harm import HarmResult
from remvalgate.harness.outcome import ProblemResult, applied_result
from remvalgate.harness.snapshot import (
    IN_SCOPE,
    OUT_OF_SCOPE,
    NotRestorable,
    ObjectRef,
    RestoreResult,
    restore_objects,
    take,
    touched,
)


@dataclass(frozen=True)
class Execution:
    command: str
    # exit code of the command; a failed command still counts as applied, since it may have
    # changed something before it failed
    exit_code: int
    harm: HarmResult
    # readiness and the problem-specific probes passed; the change is kept exactly when True
    check_passed: bool
    # None when no restore ran: the check passed, the fault is out of scope with no mongo exec,
    # or the snapshot was empty or impossible
    restore: RestoreResult | None
    # one message per restored Deployment that did not roll out
    rollout_errors: tuple[str, ...]
    result: ProblemResult

    @property
    def escalated(self) -> bool:
        """Handed to a human after the action, with or without a restore."""
        return not self.check_passed


def apply_with_check(
    command: str,
    fault_type: str,
    any_working_candidate: bool | None,
    *,
    policy: Policy,
    namespace: str,
    get: Callable[[ObjectRef], dict | None],
    put: Callable[[str, dict], None],
    run: Callable[[str], int],
    measure_harm: Callable[[], HarmResult],
    check: Callable[[], bool],
    rolled_out: Callable[[ObjectRef], None],
    mongo: mongo_snapshot.Exec | None = None,
) -> Execution:
    """Snapshot, apply, measure harm, check, and restore and escalate on a failed check.

    command must be admitted by the allow-list: touched raises ValueError otherwise, before
    anything runs. run returns the exit code. measure_harm waits the settle window and returns
    detect_harm's result, with pre taken before this call. check runs readiness and the probes
    with no further settle window. rolled_out waits for a restored Deployment and raises
    RuntimeError if it does not roll out. get and put are as in snapshot.restore. mongo, given
    for a database fault (RevokeAuth, UserUnregistered), snapshots and restores the MongoDB users
    (mongo_snapshot.py, #187) and any Deployment or Service the command touches; without it such a
    fault escalates with no restore.

    Raises ValueError for a fault type in neither scope list, before the command runs. If run,
    measure_harm or check raises, the snapshot is restored and its Deployments waited for before
    the error propagates, with the restore result added as a note on the error, so a failed
    measurement does not leave the action in place for the next candidate.
    """
    if fault_type not in IN_SCOPE | OUT_OF_SCOPE:
        raise ValueError(f"unknown fault type {fault_type!r}")
    objects, users = None, None
    try:
        refs = touched(command, policy, namespace)
    except NotRestorable:
        refs = None
    if fault_type in OUT_OF_SCOPE and mongo is not None:
        # a failed read raises here, before the command runs
        users = mongo_snapshot.take(namespace, mongo)
    # a database fault's command may also change a Deployment or Service, which a users-only
    # restore would leave in place while reporting it restored
    if refs and (fault_type in IN_SCOPE or users is not None):
        objects = take(namespace, refs, get)
    can_restore = users is not None or (objects is not None and bool(objects.objects))
    try:
        exit_code = run(command)
        harm = measure_harm()
        passed = check()
    except BaseException as error:
        if can_restore:
            restored, rollout_errors = _undo(
                fault_type, objects, users, get, put, rolled_out, mongo
            )
            error.add_note(
                f"snapshot restored before this error: restored {restored.restored}, "
                f"changed {list(restored.changed)}, errors {list(restored.errors)}, "
                f"rollout errors {rollout_errors}"
            )
        raise
    restored, rollout_errors = None, []
    if not passed and can_restore:
        restored, rollout_errors = _undo(fault_type, objects, users, get, put, rolled_out, mongo)
    result = applied_result(harm, any_working_candidate, restore_attempted=restored is not None)
    if not passed:
        # an escalation is never recovered, also when nothing was restored
        result = replace(result, recovered=False)
    return Execution(
        command=command,
        exit_code=exit_code,
        harm=harm,
        check_passed=passed,
        restore=restored,
        rollout_errors=tuple(rollout_errors),
        result=result,
    )


def _undo(
    fault_type, objects, users, get, put, rolled_out, mongo
) -> tuple[RestoreResult, list[str]]:
    """Restore the objects, then wait for each restored Deployment, recording any that fail,
    then restore the users. Either snapshot may be None; one result covers both."""
    results, rollout_errors = [], []
    if objects is not None and objects.objects:
        # the fault type was checked by apply_with_check: in scope, or a database fault with mongo
        results.append(restore_objects(objects, get, put))
        for ref in objects.objects:
            if ref[0] != "Deployment":
                continue
            try:
                rolled_out(ref)
            except RuntimeError as error:
                rollout_errors.append(f"{ref[0]}/{ref[1]}: {error}")
    if users is not None:
        # restarting the client pods, as AIOpsLab's recovery does, is left to the caller
        results.append(mongo_snapshot.restore(fault_type, users, mongo))
    if len(results) == 1:
        return results[0], rollout_errors
    return RestoreResult(
        attempted=all(r.attempted for r in results),
        changed=tuple(c for r in results for c in r.changed),
        recreated=tuple(c for r in results for c in r.recreated),
        errors=tuple(e for r in results for e in r.errors),
    ), rollout_errors


def kubectl_run(context: str | None = None, timeout: float = 300):
    """Return a run for apply_with_check: the command split with shlex, never through a shell.

    The context is put after kubectl. A timeout returns -1 and does not raise, so the check and
    the restore still run. Not unit tested and never run by CI; scripts/run_execution_check.py
    runs it on a cluster.
    """

    def run(command: str) -> int:
        args = shlex.split(command)
        if context:
            args[1:1] = ["--context", context]
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            return -1

    return run


def kubectl_rolled_out(namespace: str, context: str | None = None, timeout: int = 300):
    """Return a rolled_out for apply_with_check that runs kubectl rollout status."""
    base = ["kubectl", *(["--context", context] if context else []), "-n", namespace]

    def rolled_out(ref: ObjectRef) -> None:
        name = f"{ref[0].lower()}/{ref[1]}"
        try:
            run = subprocess.run(
                [*base, "rollout", "status", name, f"--timeout={timeout}s"],
                capture_output=True,
                text=True,
                timeout=timeout + 30,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"timed out after {error.timeout} s") from error
        if run.returncode != 0:
            raise RuntimeError(run.stderr.strip())

    return rolled_out
