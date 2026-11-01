"""The four retrieval configurations of the RAG ablation (E4-S2, R4.2).

(i) NoRetrieval returns nothing, so the planner sees telemetry only. (ii) Dense ranks by cosine
similarity of embeddings. (iii) DenseRerank takes the dense top `depth` and reorders them with a
cross-encoder reranker. (iv) HybridRerank fuses the dense and the BM25 rankings by reciprocal rank
fusion, takes the fused top `depth` and reorders them with the same reranker; it is scored on
retrieval metrics only.

Every configuration serves one fold corpus per problem: the corpus of the fold that holds the
problem out, as built by build_corpus, so no problem retrieves itself. A problem outside the fold
assignment is refused by every configuration, (i) included, so swapping configurations never
changes which problems run. The fold is only a key: a development or confirmation problem is
served by mapping its id to a key of its own, such as -1, with its own corpus. All four rank
the same entry text (entry_text) for the same query, so only the method differs.

A fold corpus has fewer than 20 entries. The reranker sees only the first stage's top `depth`
(default 5), never the whole corpus: if it scored every entry, (iii) and (iv) would rank alike and
the first stage would not matter. So (iii) and (iv) return at most `depth` hits.

The embedder and the reranker are passed in as functions, so the tests use deterministic fakes and
download no model. load_models returns the evaluation models at pinned revisions; it needs the
optional retrieval dependency group (uv sync --group retrieval).
"""

import math
import re
from collections.abc import Callable, Mapping, Sequence

from rank_bm25 import BM25Okapi

from remvalgate.retrieval import Hit, PastFix, RetrieverRefused

# texts to unit-length vectors, one per text
Encode = Callable[[Sequence[str]], Sequence[Sequence[float]]]
# (query, texts) to one relevance score per text, higher is closer
Score = Callable[[str, Sequence[str]], Sequence[float]]

DENSE_MODEL = ("BAAI/bge-small-en-v1.5", "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a")
RERANKER_MODEL = ("BAAI/bge-reranker-base", "2cfc18c9415c912f9d8155881c133215df768a70")
DEPTH = 5
RRF_K = 60

_WORD = re.compile(r"[a-z0-9]+")


def entry_text(entry: PastFix) -> str:
    """The text every configuration ranks. The fix command is left out: it is what is retrieved,
    not what describes the incident."""
    return f"{entry.fault_type} {entry.target_service} {entry.summary}"


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _rank_ids(scores: Mapping[str, float]) -> list[str]:
    # ties break by entry id, so the order never depends on the corpus order
    return sorted(scores, key=lambda i: (-scores[i], i))


class NoRetrieval:
    """Configuration (i): no retrieval. It checks the problem and k like the others."""

    def __init__(self, assignment: Mapping[str, int], corpora: Mapping[int, Sequence[PastFix]]):
        missing = set(assignment.values()) - corpora.keys()
        if missing:
            raise ValueError(f"no corpus for folds {sorted(missing)}")
        self._assignment = dict(assignment)
        self._corpora: dict[int, dict[str, PastFix]] = {}
        for fold, corpus in corpora.items():
            by_id = {e.entry_id: e for e in corpus}
            if not by_id:
                raise ValueError(f"fold {fold}: empty corpus")
            if len(by_id) != len(corpus):
                raise ValueError(f"fold {fold}: duplicate entry ids")
            self._corpora[fold] = by_id

    def retrieve(self, query: str, problem_id: str, k: int) -> list[Hit]:
        fold = self._assignment.get(problem_id)
        if fold is None:
            raise RetrieverRefused(f"problem {problem_id!r} is not in the fold assignment")
        if k < 1:
            raise ValueError(f"k: expected at least 1, got {k}")
        scores = self._scores(query, fold)
        corpus = self._corpora[fold]
        return [
            Hit(entry=corpus[i], score=float(scores[i]), rank=rank)
            for rank, i in enumerate(_rank_ids(scores)[:k], start=1)
        ]

    def _scores(self, query: str, fold: int) -> dict[str, float]:
        return {}


class Dense(NoRetrieval):
    """Configuration (ii): cosine similarity of query and entry embeddings, over the whole fold
    corpus."""

    def __init__(
        self,
        assignment: Mapping[str, int],
        corpora: Mapping[int, Sequence[PastFix]],
        *,
        encode: Encode,
    ):
        super().__init__(assignment, corpora)
        self._encode = encode
        # entries are encoded once per fold
        self._vectors = {
            fold: dict(zip(by_id, encode([entry_text(e) for e in by_id.values()]), strict=True))
            for fold, by_id in self._corpora.items()
        }

    def _dense(self, query: str, fold: int) -> dict[str, float]:
        (q,) = self._encode([query])
        return {
            i: math.fsum(a * b for a, b in zip(q, v, strict=True))
            for i, v in self._vectors[fold].items()
        }

    def _scores(self, query: str, fold: int) -> dict[str, float]:
        return self._dense(query, fold)


class DenseRerank(Dense):
    """Configuration (iii): the dense top `depth`, reordered by the cross-encoder. Fixed for the
    full system before any evaluation run (gate G5)."""

    def __init__(
        self,
        assignment: Mapping[str, int],
        corpora: Mapping[int, Sequence[PastFix]],
        *,
        encode: Encode,
        score: Score,
        depth: int = DEPTH,
    ):
        if depth < 1:
            raise ValueError(f"depth: expected at least 1, got {depth}")
        super().__init__(assignment, corpora, encode=encode)
        self._score = score
        self._depth = depth

    def _first_stage(self, query: str, fold: int) -> list[str]:
        return _rank_ids(self._dense(query, fold))

    def _scores(self, query: str, fold: int) -> dict[str, float]:
        ids = self._first_stage(query, fold)[: self._depth]
        corpus = self._corpora[fold]
        scores = self._score(query, [entry_text(corpus[i]) for i in ids])
        return dict(zip(ids, (float(s) for s in scores), strict=True))


class HybridRerank(DenseRerank):
    """Configuration (iv): dense and BM25 rankings fused by reciprocal rank fusion, the fused top
    `depth` reordered by the same cross-encoder. Scored on retrieval metrics only."""

    def __init__(
        self,
        assignment: Mapping[str, int],
        corpora: Mapping[int, Sequence[PastFix]],
        *,
        encode: Encode,
        score: Score,
        depth: int = DEPTH,
        rrf_k: int = RRF_K,
    ):
        super().__init__(assignment, corpora, encode=encode, score=score, depth=depth)
        self._rrf_k = rrf_k
        self._bm25 = {
            fold: (list(by_id), BM25Okapi([_tokens(entry_text(e)) for e in by_id.values()]))
            for fold, by_id in self._corpora.items()
        }

    def _sparse(self, query: str, fold: int) -> dict[str, float]:
        """BM25 scores of the entries that share a word with the query. An entry that scores 0
        is left out, so the entry id, which breaks ties, never ranks unmatched entries in the
        fusion."""
        ids, bm25 = self._bm25[fold]
        scores = (float(s) for s in bm25.get_scores(_tokens(query)))
        return {i: s for i, s in zip(ids, scores, strict=True) if s > 0}

    def _first_stage(self, query: str, fold: int) -> list[str]:
        fused: dict[str, float] = {}
        for ranking in (_rank_ids(self._dense(query, fold)), _rank_ids(self._sparse(query, fold))):
            for rank, i in enumerate(ranking, start=1):
                fused[i] = fused.get(i, 0.0) + 1 / (self._rrf_k + rank)
        return _rank_ids(fused)


def load_models(device: str | None = None) -> tuple[Encode, Score]:
    """Return the evaluation embedder and reranker at their pinned revisions.

    No query instruction is added. The model card suggests one for a query that searches passages,
    but says v1.5 improves retrieval without it, and the query here is a long observation, not a
    question. Embeddings are normalised, so a dot product is the cosine.
    Both models read at most 512 tokens, so a longer query is truncated.
    """
    from sentence_transformers import CrossEncoder, SentenceTransformer

    embedder = SentenceTransformer(DENSE_MODEL[0], revision=DENSE_MODEL[1], device=device)
    reranker = CrossEncoder(RERANKER_MODEL[0], revision=RERANKER_MODEL[1], device=device)

    def encode(texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return embedder.encode(list(texts), normalize_embeddings=True).tolist()

    def score(query: str, texts: Sequence[str]) -> Sequence[float]:
        return reranker.predict([(query, t) for t in texts]).tolist()

    return encode, score
