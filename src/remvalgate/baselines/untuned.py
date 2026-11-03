"""Baselines 3a, 3b and 3c (E6-S2, R6.2): the untuned planner in three retrieval configurations.

Each is the planner zero-shot with no gate, and its action is the planner's own command: no
candidates are sampled, nothing is resampled and it is not told to escalate (proposal X). The
three differ in retrieval only, which makes them the extrinsic RAG ablation (R4.4): 3a has none,
configuration (i); 3b has dense retrieval, (ii); 3c has dense retrieval with the reranker, (iii),
built through the fixed record. The pool results come from the baseline-only run (#53).

Here the development problems are served with the synthetic stub corpus. Its 5 entries are no more
than the rerank depth, so on development problems 3b and 3c differ only in the reranker's order.
"""

from remvalgate.harness.manifest import Registry, Slice
from remvalgate.retrieval import Retriever
from remvalgate.retrieval.configs import Dense, Encode, NoRetrieval, Score, load_models
from remvalgate.retrieval.fixed import DEV_KEY, development_retriever
from remvalgate.retrieval.stub import STUB_CORPUS

# baseline -> its retrieval configuration
CONFIGURATIONS = {"3a": "i", "3b": "ii", "3c": "iii"}


def conflicting_flags(
    *, judge: bool, escalate_when_unsure: bool, candidates: int | None
) -> list[str]:
    """The planner run flags a 3a to 3c run refuses: each would make it another baseline."""
    flags = {"--judge": judge, "--escalate-when-unsure": escalate_when_unsure}
    flags["--candidates"] = candidates is not None
    return [name for name, given in flags.items() if given]


def untuned_retriever(
    baseline: str,
    registry: Registry,
    *,
    models: tuple[Encode, Score] | None = None,
    device: str | None = None,
) -> Retriever:
    """The retriever of baseline 3a, 3b or 3c over the stub corpus, serving the registry's
    development problems only. models replaces the pinned models in tests; 3a loads none."""
    if baseline not in CONFIGURATIONS:
        raise ValueError(f"baseline: expected one of {sorted(CONFIGURATIONS)}, got {baseline!r}")
    if baseline == "3c":
        return development_retriever(registry, models=models, device=device)
    development = [i for i, s in registry.slices.items() if s is Slice.DEVELOPMENT]
    if not development:
        raise ValueError("the slice registry holds no development problem")
    assignment, corpora = dict.fromkeys(development, DEV_KEY), {DEV_KEY: STUB_CORPUS}
    if baseline == "3a":
        return NoRetrieval(assignment, corpora)
    encode, _ = models or load_models(device)
    return Dense(assignment, corpora, encode=encode)
