"""Compare the cluster objects before an injection and after a harness reset (story E1-S3, R1.3).

Pure functions on the JSON of `kubectl get svc,deploy -o json`, so CI can test them without a
cluster. R3.5 checks a reset against the state after the injection, which `changed` can also
compare once a snapshot is taken then. Only the spec and labels of each object are compared:
status, annotations and the fields the API server rewrites on every change do not tell a reset
from a leftover fault. Annotations on the pod template sit in the spec and are compared.
"""

# fields the API server sets or bumps on its own
VOLATILE = frozenset(
    {"resourceVersion", "managedFields", "generation", "uid", "creationTimestamp", "selfLink"}
)


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def normalise(listing: dict) -> dict:
    """Map `kind/name` to the labels and spec of each item of a kubectl list."""
    state = {}
    for item in listing["items"]:
        key = f"{item['kind']}/{item['metadata']['name']}"
        state[key] = _strip(
            {"labels": item["metadata"].get("labels", {}), "spec": item.get("spec", {})}
        )
    return state


def changed(before: dict, after: dict) -> list[str]:
    """Return the sorted `kind/name` keys that differ, were added or were removed."""
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
