"""Eval checks used by the overlay problems. Imports nothing from aiopslab, so CI tests them."""

import time
from collections.abc import Callable


def has_available_replica(deployment) -> bool:
    """True when the deployment exists and at least one replica is available."""
    status = getattr(deployment, "status", None)
    return bool(status and status.available_replicas)


def poll_until(
    check: Callable[[], bool],
    deadline: float,
    poll_interval: float = 5.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Call check until it is True or the clock passes deadline; check runs at least once."""
    while True:
        if check():
            return True
        if clock() >= deadline:
            return False
        sleep(poll_interval)
