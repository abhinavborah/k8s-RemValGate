"""Read symptoms from the JSON of `kubectl get deploy,svc,pods -o json`, with no model.

Pure functions on the listing, so CI tests them without a cluster. Each rule looks for the
state one object-state fault type leaves behind (BRD R2.2), and is blind to the injection itself:

- SCALED_TO_ZERO: a Deployment with spec.replicas 0 (ScalePod).
- UNSCHEDULABLE_SELECTOR: a Deployment with a nodeSelector and a Pending pod of its own
  (AssignNonExistentNode).
- PORT_MISMATCH: a Service whose numeric targetPort no container of its Deployment listens on
  (TargetPortMisconfig).

A port mismatch is read against the containerPort values the Deployment declares, which
Kubernetes treats as informational: a Deployment that declares none is never reported, and a
healthy Service that targets an undeclared port is reported.

The database faults (RevokeAuth, UserUnregistered) leave no such state in these objects, so they
are not observed here.
"""

from dataclasses import dataclass
from enum import IntEnum


class Kind(IntEnum):
    # the order is the priority order of the rule baseline
    SCALED_TO_ZERO = 0
    UNSCHEDULABLE_SELECTOR = 1
    PORT_MISMATCH = 2


@dataclass(frozen=True)
class Observation:
    kind: Kind
    service: str
    # plain words for the retrieval query
    detail: str
    # PORT_MISMATCH only: the index of the Service port and a port the containers listen on
    port_index: int | None = None
    port: int | None = None


def _subset(small: dict, big: dict) -> bool:
    return bool(small) and all(big.get(k) == v for k, v in small.items())


def _template(deployment: dict) -> dict:
    return deployment.get("spec", {}).get("template", {})


def _container_ports(deployment: dict) -> set[int]:
    containers = _template(deployment).get("spec", {}).get("containers", [])
    return {p["containerPort"] for c in containers for p in c.get("ports", [])}


def _scaled_to_zero(deployments: list[dict]) -> list[Observation]:
    return [
        Observation(
            Kind.SCALED_TO_ZERO,
            d["metadata"]["name"],
            f"deployment {d['metadata']['name']} has 0 replicas",
        )
        for d in deployments
        if d.get("spec", {}).get("replicas") == 0
    ]


def _unschedulable(deployments: list[dict], pods: list[dict]) -> list[Observation]:
    found = []
    for d in deployments:
        if not _template(d).get("spec", {}).get("nodeSelector"):
            continue
        match = d["spec"].get("selector", {}).get("matchLabels", {})
        pending = any(
            p.get("status", {}).get("phase") == "Pending"
            and _subset(match, p["metadata"].get("labels", {}))
            for p in pods
        )
        if pending:
            name = d["metadata"]["name"]
            found.append(
                Observation(
                    Kind.UNSCHEDULABLE_SELECTOR,
                    name,
                    f"deployment {name} has a node selector and a pending pod",
                )
            )
    return found


def _port_mismatch(services: list[dict], deployments: list[dict]) -> list[Observation]:
    found = []
    for s in services:
        selector = s.get("spec", {}).get("selector", {})
        listening: set[int] = set()
        for d in deployments:
            if _subset(selector, _template(d).get("metadata", {}).get("labels", {})):
                listening |= _container_ports(d)
        if not listening:
            continue
        for index, port in enumerate(s["spec"].get("ports", [])):
            target = port.get("targetPort")
            if isinstance(target, int) and target not in listening:
                name = s["metadata"]["name"]
                found.append(
                    Observation(
                        Kind.PORT_MISMATCH,
                        name,
                        f"service {name} targetPort is not a declared container port",
                        index,
                        # shortcut: the lowest declared port; a metrics or admin port could be
                        # lower, which is fine while the development problems declare one port
                        min(listening),
                    )
                )
    return found


def observe(listing: dict) -> list[Observation]:
    """Return the observations in a stable order: by kind priority, then by service name."""
    items = listing["items"]
    by_kind = {k: [i for i in items if i["kind"] == k] for k in ("Deployment", "Service", "Pod")}
    deployments, services, pods = by_kind["Deployment"], by_kind["Service"], by_kind["Pod"]
    found = [
        *_scaled_to_zero(deployments),
        *_unschedulable(deployments, pods),
        *_port_mismatch(services, deployments),
    ]
    return sorted(found, key=lambda o: (o.kind, o.service, o.port_index or 0))


def symptom_query(observations: list[Observation]) -> str:
    """The retrieval query: the observed symptoms in plain words, with no fault-type label."""
    return "; ".join(o.detail for o in observations)
