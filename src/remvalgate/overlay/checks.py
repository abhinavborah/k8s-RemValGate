"""Eval checks used by the overlay problems. Imports nothing from aiopslab, so CI tests them."""


def has_available_replica(deployment) -> bool:
    """True when the deployment exists and at least one replica is available."""
    status = getattr(deployment, "status", None)
    return bool(status and status.available_replicas)
