"""Load the retrieval models and run the four configurations once on the stub corpus (E4-S2).

Not part of the tests: it downloads both models. The corpus is the synthetic development stub, so
the run touches no evaluation problem. Runs on CPU.

    uv sync --group retrieval
    uv run python scripts/smoke_retrieval.py
"""

import sys
import time

import sentence_transformers
import torch

from remvalgate.retrieval.configs import (
    DENSE_MODEL,
    RERANKER_MODEL,
    Dense,
    DenseRerank,
    HybridRerank,
    NoRetrieval,
    load_models,
)
from remvalgate.retrieval.stub import STUB_CORPUS

QUERY = "pods pending, node selector names a node that does not exist"
EXPECTED = "AssignNonExistentNode"


def main() -> int:
    print(f"torch {torch.__version__}, sentence-transformers {sentence_transformers.__version__}")
    start = time.time()
    encode, score = load_models(device="cpu")
    print(f"loaded {DENSE_MODEL} and {RERANKER_MODEL} in {time.time() - start:.1f} s")

    (vector,) = encode(["a probe"])
    norm = sum(x * x for x in vector) ** 0.5
    print(f"embedding dimension {len(vector)}, norm {norm:.4f}")
    scores = score("a probe", ["a readiness probe", "a database user"])
    print(f"reranker scores {scores}")
    ok = abs(norm - 1) < 1e-3 and len(scores) == 2

    # the development corpus is served under a key of its own
    assignment, corpora = {"dev-1": -1}, {-1: list(STUB_CORPUS)}
    for cls, kwargs in [
        (NoRetrieval, {}),
        (Dense, {"encode": encode}),
        (DenseRerank, {"encode": encode, "score": score, "depth": 3}),
        (HybridRerank, {"encode": encode, "score": score, "depth": 3}),
    ]:
        hits = cls(assignment, corpora, **kwargs).retrieve(QUERY, "dev-1", 3)
        print(cls.__name__, [(h.entry.entry_id, round(h.score, 3)) for h in hits])
        if cls is NoRetrieval:
            ok = ok and hits == []
        else:
            ok = ok and bool(hits) and hits[0].entry.fault_type == EXPECTED
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
