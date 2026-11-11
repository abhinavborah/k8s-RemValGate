"""Synthetic text of the gate input's shape, to check length_proxy against a tokenizer (E7-S5).

The export (training_data.py) measures each gate input with length_proxy unless it is given a
tokenizer. Counting the gate inputs themselves would read pool data, an evaluation-set touch, so
these texts hold none: the first observation is a `kubectl get deployments,pods` listing of an
application's deployments with one Ready pod each, and the retrieved past fixes are entries of the
synthetic development corpus (retrieval/stub.py). scripts/check_length_proxy.py counts them with a
tokenizer, and docs/training_data.md (Length) records the result.

The listing is laid out as kubectl lays it out (cli-runtime printers/tabwriter.go: minimum width
6, padding 3, the last column unpadded). A pod is named as Kubernetes names it: the deployment, the
pod-template-hash (the decimal digits of a 32-bit hash, each mapped onto SUFFIX_CHARS by
rand.SafeEncodeString, controller_utils.go ComputeHash), then five random SUFFIX_CHARS
(apiserver names.SimpleNameGenerator). All three are read at Kubernetes v1.32.1.
"""

import json
import random
from collections.abc import Callable, Sequence

from remvalgate.harness.training_data import TOKEN_BUDGET, length_proxy
from remvalgate.planner.loop import task_text
from remvalgate.retrieval.stub import STUB_CORPUS

# k8s.io/apimachinery/pkg/util/rand alphanums: the characters of generated name suffixes
SUFFIX_CHARS = "bcdfghjklmnpqrstvwxz2456789"
# the namespace and the deployments of each application's chart at aiopslab-applications 8038be6,
# the commit AIOpsLab pins (socialNetwork/helm-chart/socialnetwork/charts and
# hotelReservation/helm-chart/hotelreservation/charts, packaged dependencies left out)
APPS = {
    "Social Network": (
        "test-social-network",
        (
            "compose-post-service",
            "home-timeline-redis",
            "home-timeline-service",
            "jaeger",
            "media-frontend",
            "media-memcached",
            "media-mongodb",
            "media-service",
            "nginx-thrift",
            "post-storage-memcached",
            "post-storage-mongodb",
            "post-storage-service",
            "social-graph-mongodb",
            "social-graph-redis",
            "social-graph-service",
            "text-service",
            "unique-id-service",
            "url-shorten-memcached",
            "url-shorten-mongodb",
            "url-shorten-service",
            "user-memcached",
            "user-mention-service",
            "user-mongodb",
            "user-service",
            "user-timeline-mongodb",
            "user-timeline-redis",
            "user-timeline-service",
        ),
    ),
    "Hotel Reservation": (
        "test-hotel-reservation",
        (
            "consul",
            "frontend",
            "geo",
            "jaeger",
            "memcached-profile",
            "memcached-rate",
            "memcached-reserve",
            "mongodb-geo",
            "mongodb-profile",
            "mongodb-rate",
            "mongodb-recommendation",
            "mongodb-reservation",
            "mongodb-user",
            "profile",
            "rate",
            "recommendation",
            "reservation",
            "search",
            "user",
        ),
    ),
}
SEEDS = range(20)


def _table(rows: Sequence[Sequence[str]]) -> str:
    # kubectl's tab writer: minimum width 6, padding 3
    widths = [max(6, max(len(row[i]) for row in rows) + 3) for i in range(len(rows[0]) - 1)]
    return "\n".join(
        "".join(cell.ljust(w) for cell, w in zip(row[:-1], widths, strict=True)) + row[-1]
        for row in rows
    )


def listing(names: Sequence[str], rng: random.Random) -> str:
    """The output of `kubectl get deployments,pods` with one Ready pod per deployment."""
    deployments = [("NAME", "READY", "UP-TO-DATE", "AVAILABLE", "AGE")]
    pods = [("NAME", "READY", "STATUS", "RESTARTS", "AGE")]
    for name in names:
        digits = str(rng.getrandbits(32))
        template = "".join(SUFFIX_CHARS[ord(d) % len(SUFFIX_CHARS)] for d in digits)
        suffix = "".join(rng.choice(SUFFIX_CHARS) for _ in range(5))
        deployments.append((f"deployment.apps/{name}", "1/1", "1", "1", "12m"))
        pods.append((f"pod/{name}-{template}-{suffix}", "1/1", "Running", "0", "12m"))
    return f"{_table(deployments)}\n\n{_table(pods)}\n"


def gate_input(app: str, observation: str, rng: random.Random) -> str:
    """A gate input in the export's form (training_data.problem_records): the task, the
    observation, a TargetPortMisconfig diagnosis of one deployment, three development corpus
    entries as plan() shows them, and a patch of that service as the candidate."""
    namespace, names = APPS[app]
    service = rng.choice(names)
    return json.dumps(
        {
            "task": task_text(app, namespace),
            "first_observation": observation,
            "diagnosis": {
                "fault_type": "TargetPortMisconfig",
                "service": service,
                "reason": f"Requests to {service} fail: its service sends traffic to a port "
                "that no container of its pod listens on.",
            },
            "retrieved": [
                f"{e.fault_type} on {e.target_service}: {e.summary}. Fix: {e.fix_command}"
                for e in rng.sample(STUB_CORPUS, 3)
            ],
            "candidate": f"kubectl -n {namespace} patch service {service} --type=json "
            '-p \'[{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 9090}]\'',
        },
        ensure_ascii=False,
    )


def samples(seed: int) -> list[tuple[str, str, str]]:
    """(application, kind, text) for each application: the listing alone, as baseline 5's prompt
    holds it, the pod names alone, and the gate input that holds the listing."""
    out = []
    for app, (_, names) in APPS.items():
        rng = random.Random(f"{seed}/{app}")
        text = listing(names, rng)
        pods = [line.split()[0] for line in text.splitlines() if line.startswith("pod/")]
        out += [
            (app, "listing", text),
            (app, "pod names", "\n".join(pods)),
            (app, "gate input", gate_input(app, text, rng)),
        ]
    return out


def compare(count: Callable[[str], int], seeds: Sequence[int] = SEEDS) -> list[dict]:
    """Per application and kind, over the seeds' samples: the largest proxy and count, the number
    of samples over TOKEN_BUDGET by each, and the lowest and highest ratio of count to proxy."""
    pairs: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for seed in seeds:
        for app, kind, text in samples(seed):
            pairs.setdefault((app, kind), []).append((length_proxy(text), count(text)))
    return [
        {
            "application": app,
            "kind": kind,
            "samples": len(p),
            "proxy_max": max(x for x, _ in p),
            "count_max": max(y for _, y in p),
            "proxy_over": sum(x > TOKEN_BUDGET for x, _ in p),
            "count_over": sum(y > TOKEN_BUDGET for _, y in p),
            "ratio_min": min(y / x for x, y in p),
            "ratio_max": max(y / x for x, y in p),
        }
        for (app, kind), p in pairs.items()
    ]
