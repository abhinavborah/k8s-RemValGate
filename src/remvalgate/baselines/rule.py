"""Baseline 1: a deterministic lookup from the observed fault to its fix, with no model."""

import json
import shlex

from remvalgate.baselines import BaselineAction, escalate
from remvalgate.baselines.observe import Kind, Observation
from remvalgate.harness.allowlist import validate_namespace


def _command(namespace: str, *args: str) -> str:
    return shlex.join(["kubectl", "-n", namespace, *args])


def _fix(o: Observation, namespace: str) -> str:
    if o.kind is Kind.SCALED_TO_ZERO:
        return _command(namespace, "scale", "deployment", o.service, "--replicas=1")
    if o.kind is Kind.UNSCHEDULABLE_SELECTOR:
        patch = [{"op": "remove", "path": "/spec/template/spec/nodeSelector"}]
        return _command(
            namespace, "patch", "deployment", o.service, "--type=json", "-p", json.dumps(patch)
        )
    patch = [{"op": "replace", "path": f"/spec/ports/{o.port_index}/targetPort", "value": o.port}]
    return _command(
        namespace, "patch", "service", o.service, "--type=json", "-p", json.dumps(patch)
    )


def fault_type_rule(observations: list[Observation], namespace: str) -> BaselineAction:
    """Act on the first observation in priority order, or escalate when there is none.

    Raises ValueError if the namespace is not a sandbox namespace.
    """
    validate_namespace(namespace)
    if not observations:
        return escalate("no rule matched the observed state")
    first = observations[0]
    return BaselineAction(_fix(first, namespace), f"{first.kind.name} on {first.service}")
