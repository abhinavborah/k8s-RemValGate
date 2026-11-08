"""Development stub retriever (E4-S7).

Ranks a small synthetic corpus by the share of query words that appear in an entry, so its scores
lie in [0, 1]. That range is a property of this stub, not of the interface. It refuses every
problem outside the development slice. The hand runs of the planner and baselines now retrieve
with configuration (iii) over STUB_CORPUS (fixed.py, #35); the stub stays as a retriever that
needs no model, for the unit tests.

The corpus is synthetic: an entry built from a pool, confirmation or singleton problem would
be an evaluation-set touch (BRD 7.2).
"""

import re
from collections.abc import Sequence

from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, Registry, Slice, load_registry
from remvalgate.retrieval import Hit, PastFix, RetrieverRefused, Source

_WORD = re.compile(r"[a-z0-9]+")

STUB_CORPUS: tuple[PastFix, ...] = (
    PastFix(
        entry_id="stub-001",
        source=Source.INCIDENT,
        fault_type="TargetPortMisconfig",
        target_service="demo-service-a",
        summary="service port does not match the container port, so requests are refused",
        fix_command="kubectl patch service demo-service-a --type=json "
        '-p \'[{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 8080}]\'',
    ),
    PastFix(
        entry_id="stub-002",
        source=Source.RUNBOOK,
        fault_type="ScalePod",
        target_service="demo-service-b",
        summary="deployment scaled to zero replicas, so no pod serves traffic",
        fix_command="kubectl scale deployment demo-service-b --replicas=1",
    ),
    PastFix(
        entry_id="stub-003",
        source=Source.INCIDENT,
        fault_type="AssignNonExistentNode",
        target_service="demo-service-c",
        summary="pod stays pending because its node selector names a node that does not exist",
        fix_command="kubectl patch deployment demo-service-c --type=json "
        '-p \'[{"op": "remove", "path": "/spec/template/spec/nodeSelector"}]\'',
    ),
    PastFix(
        entry_id="stub-004",
        source=Source.RUNBOOK,
        fault_type="RevokeAuth",
        target_service="demo-database",
        summary="database rejects the service because its credentials were revoked",
        fix_command="kubectl exec deploy/demo-database -- grant-access demo-service-a",
    ),
    PastFix(
        entry_id="stub-005",
        source=Source.INCIDENT,
        fault_type="UserUnregistered",
        target_service="demo-database",
        summary="login fails because the application user is missing from the database",
        fix_command="kubectl exec deploy/demo-database -- create-user demo-service-a",
    ),
)


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _score(query_words: set[str], entry: PastFix) -> float:
    if not query_words:
        return 0.0
    entry_words = _words(f"{entry.fault_type} {entry.target_service} {entry.summary}")
    return len(query_words & entry_words) / len(query_words)


class DevStubRetriever:
    def __init__(self, registry: Registry | None = None, corpus: Sequence[PastFix] = STUB_CORPUS):
        corpus = tuple(corpus)
        if not corpus:
            raise ValueError("corpus: expected at least one entry")
        ids = [e.entry_id for e in corpus]
        if len(set(ids)) != len(ids):
            raise ValueError("corpus: duplicate entry ids")
        self._registry = registry or load_registry(DEFAULT_REGISTRY_PATH)
        self._corpus = corpus

    def retrieve(self, query: str, problem_id: str, k: int) -> list[Hit]:
        slice_ = self._registry.slices.get(problem_id)
        if slice_ is None:
            raise RetrieverRefused(f"problem {problem_id!r} is not in the slice registry")
        if slice_ is not Slice.DEVELOPMENT:
            raise RetrieverRefused(
                f"the stub serves development problems only, {problem_id!r} is in {slice_.value}"
            )
        if k < 1:
            raise ValueError(f"k: expected at least 1, got {k}")
        words = _words(query)
        # ties break by entry id, so the order never depends on the corpus order
        ranked = sorted(self._corpus, key=lambda e: (-_score(words, e), e.entry_id))
        return [
            Hit(entry=e, score=_score(words, e), rank=rank)
            for rank, e in enumerate(ranked[:k], start=1)
        ]
