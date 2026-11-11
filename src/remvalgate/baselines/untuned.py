"""Baselines 3a, 3b and 3c (E6-S2, R6.2): the untuned planner in three retrieval configurations.

Each is the planner zero-shot with no gate, and its action is the planner's own command: no
candidates are sampled, nothing is resampled and it is not told to escalate (proposal X). The
three differ in retrieval only, which makes them the extrinsic RAG ablation (R4.4): 3a has none,
configuration (i); 3b has dense retrieval, (ii); 3c has dense retrieval with the reranker, (iii),
built through the fixed record. The pool results come from the baseline-only run (#53,
scripts/run_baselines_pool.py).

untuned_retriever serves the development problems with the synthetic stub corpus. Its 5 entries
are no more than the rerank depth, so on development problems 3b and 3c differ only in the
reranker's order. pool_untuned_retriever serves the pool problems from the fold corpora that
configuration (iii) uses, so the three differ in retrieval only there too.
"""

from collections.abc import Sequence

from remvalgate.harness.folds import pool_folds
from remvalgate.harness.manifest import Registry, Slice
from remvalgate.retrieval import PastFix, Retriever, fixed
from remvalgate.retrieval.configs import Dense, Encode, NoRetrieval, Score, load_models
from remvalgate.retrieval.corpus import build_corpus
from remvalgate.retrieval.fixed import DEV_KEY, development_retriever, pool_retriever
from remvalgate.retrieval.pool_corpus import RUNBOOK
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


def pool_corpora(registry: Registry) -> tuple[dict[str, int], dict[int, Sequence[PastFix]]]:
    """The pool's fold assignment and the corpus of each fold, built as pool_retriever builds
    them for (iii): each problem retrieves from the corpus of the fold that holds it out, so it
    never retrieves its own record. build_corpus refuses a fold with a leak."""
    assignment = pool_folds(registry)
    # looked up through fixed, as pool_retriever does, so 3a, 3b and 3c read one incident set
    incidents = fixed.pool_incidents(registry)
    corpora = {
        fold: build_corpus(fold, assignment=assignment, incidents=incidents, runbook=RUNBOOK)
        for fold in sorted(set(assignment.values()))
    }
    return assignment, corpora


def pool_untuned_retriever(
    baseline: str,
    registry: Registry,
    *,
    models: tuple[Encode, Score] | None = None,
    device: str | None = None,
) -> Retriever:
    """The retriever of baseline 3a, 3b or 3c over the pool's fold corpora, serving the
    registry's pool problems only, each from the fold that holds it out. models replaces the
    pinned models in tests; 3a loads none."""
    if baseline not in CONFIGURATIONS:
        raise ValueError(f"baseline: expected one of {sorted(CONFIGURATIONS)}, got {baseline!r}")
    if baseline == "3c":
        return pool_retriever(registry, models=models, device=device)
    assignment, corpora = pool_corpora(registry)
    if baseline == "3a":
        return NoRetrieval(assignment, corpora)
    encode, _ = models or load_models(device)
    return Dense(assignment, corpora, encode=encode)
