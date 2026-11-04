"""Baselines 1 and 2 (E6-S1): the simplest alternatives to the planner and the gate.

Both read the cluster through observe and return one BaselineAction. A baseline never runs a
command: the harness applies the allow-list to the command and executes it, as for every system.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BaselineAction:
    # one kubectl command for exec_shell; None means escalate instead of acting
    command: str | None
    reason: str


def escalate(reason: str) -> BaselineAction:
    return BaselineAction(command=None, reason=reason)
