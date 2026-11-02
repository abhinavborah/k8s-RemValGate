import math

import pytest

from remvalgate.harness.folds import assign_folds, fault_type
from remvalgate.retrieval import PastFix, Retriever, RetrieverRefused, Source
from remvalgate.retrieval.configs import (
    Dense,
    DenseRerank,
    HybridRerank,
    NoRetrieval,
    _LuceneBM25,
    entry_text,
)
from remvalgate.retrieval.corpus import build_corpus

# synthetic ids that carry the stems of the fault types but name no registered problem
PROBLEMS = [
    *(f"synth-k8s_target_port-misconfig-{i}" for i in range(1, 7)),
    *(f"synth-revoke_auth-{i}" for i in range(1, 3)),
    *(f"synth-user_unregistered-{i}" for i in range(1, 3)),
    *(f"synth-scale_pod_zero-{i}" for i in range(1, 4)),
    *(f"synth-assign_to_non_existent_node-{i}" for i in range(1, 4)),
]
WORDS = {
    "TargetPortMisconfig": "port refused connection",
    "RevokeAuth": "credentials revoked denied",
    "UserUnregistered": "login user missing",
    "ScalePod": "replicas zero scaled",
    "AssignNonExistentNode": "pending node selector",
}
ASSIGNMENT = assign_folds(PROBLEMS)
INCIDENTS = [
    PastFix(
        entry_id=p,
        source=Source.INCIDENT,
        fault_type=fault_type(p),
        target_service=f"svc{n}",
        summary=f"{WORDS[fault_type(p)]} after change {n}",
        fix_command=f"kubectl patch deployment svc{n}",
    )
    for n, p in enumerate(PROBLEMS)
]
RUNBOOK = [
    PastFix(
        entry_id=f"runbook-{t}",
        source=Source.RUNBOOK,
        fault_type=t,
        target_service="<service>",
        summary=f"general steps when {w}",
        fix_command=f"kubectl rollout restart deployment/<service> # {t}",
    )
    for t, w in WORDS.items()
]
CORPORA = {
    f: build_corpus(f, assignment=ASSIGNMENT, incidents=INCIDENTS, runbook=RUNBOOK)
    for f in set(ASSIGNMENT.values())
}
VOCAB = sorted({w for text in WORDS.values() for w in text.split()})


def fake_encode(texts):
    """Bag of words over VOCAB, normalised; deterministic and model free."""
    out = []
    for t in texts:
        words = t.lower().split()
        v = [float(words.count(w)) for w in VOCAB]
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def fake_score(query, texts):
    """Reverse the order it is given, so a rerank visibly changes the first stage's order."""
    return [float(i) for i in range(len(texts))]


def configs(**kw):
    return {
        "none": NoRetrieval(ASSIGNMENT, CORPORA),
        "dense": Dense(ASSIGNMENT, CORPORA, encode=fake_encode),
        "dense_rerank": DenseRerank(
            ASSIGNMENT, CORPORA, encode=fake_encode, score=fake_score, **kw
        ),
        "hybrid_rerank": HybridRerank(
            ASSIGNMENT, CORPORA, encode=fake_encode, score=fake_score, **kw
        ),
    }


ALL = list(configs())
PROBLEM = PROBLEMS[0]
QUERY = "connection refused on the port"


@pytest.mark.parametrize("name", ALL)
def test_meets_the_retriever_protocol(name):
    assert isinstance(configs()[name], Retriever)


@pytest.mark.parametrize("name", ALL)
def test_refuses_a_problem_outside_the_assignment(name):
    with pytest.raises(RetrieverRefused):
        configs()[name].retrieve(QUERY, "pool-unknown-1", 3)


@pytest.mark.parametrize("name", ALL)
def test_k_below_one_is_an_error(name):
    with pytest.raises(ValueError):
        configs()[name].retrieve(QUERY, PROBLEM, 0)


def test_no_retrieval_returns_nothing():
    assert configs()["none"].retrieve(QUERY, PROBLEM, 3) == []


@pytest.mark.parametrize("name", ALL[1:])
@pytest.mark.parametrize("problem", PROBLEMS)
def test_ranked_hits_from_the_problems_own_fold_only(name, problem):
    hits = configs()[name].retrieve(QUERY, problem, 3)
    assert [h.rank for h in hits] == [1, 2, 3]
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    fold_ids = {e.entry_id for e in CORPORA[ASSIGNMENT[problem]]}
    assert {h.entry.entry_id for h in hits} <= fold_ids
    held_out = {p for p, f in ASSIGNMENT.items() if f == ASSIGNMENT[problem]}
    assert not {h.entry.entry_id for h in hits} & held_out


def test_dense_returns_the_whole_corpus_at_large_k_and_ranks_the_matching_type_first():
    hits = configs()["dense"].retrieve(QUERY, PROBLEM, 100)
    assert len(hits) == len(CORPORA[ASSIGNMENT[PROBLEM]])
    assert hits[0].entry.fault_type == "TargetPortMisconfig"


@pytest.mark.parametrize("name", ["dense_rerank", "hybrid_rerank"])
def test_rerank_ranks_the_whole_corpus(name):
    hits = configs(depth=4)[name].retrieve(QUERY, PROBLEM, 100)
    assert len(hits) == len(CORPORA[ASSIGNMENT[PROBLEM]])


def test_rerank_reorders_the_top_depth_and_keeps_the_rest_in_first_stage_order():
    c = configs(depth=4)
    dense = [h.entry.entry_id for h in c["dense"].retrieve(QUERY, PROBLEM, 100)]
    reranked = [h.entry.entry_id for h in c["dense_rerank"].retrieve(QUERY, PROBLEM, 100)]
    # fake_score reverses the order it is given
    assert reranked == [*reversed(dense[:4]), *dense[4:]]


def test_rerank_reorders_the_dense_top_depth():
    c = configs(depth=5)
    dense = [h.entry.entry_id for h in c["dense"].retrieve(QUERY, PROBLEM, 5)]
    reranked = [h.entry.entry_id for h in c["dense_rerank"].retrieve(QUERY, PROBLEM, 5)]
    assert reranked == list(reversed(dense))


def test_hybrid_and_dense_rerank_differ_when_bm25_disagrees_with_dense():
    # the encoder sees only VOCAB words, so it cannot tell services apart; BM25 can
    query = "svc1 svc2 svc3 svc4 svc5 svc6"
    c = configs(depth=3)
    first = [h.entry.entry_id for h in c["dense_rerank"].retrieve(query, PROBLEM, 3)]
    hybrid = [h.entry.entry_id for h in c["hybrid_rerank"].retrieve(query, PROBLEM, 3)]
    assert set(first) != set(hybrid)


def test_order_does_not_depend_on_the_corpus_order():
    reversed_corpora = {f: list(reversed(c)) for f, c in CORPORA.items()}
    for cls in (Dense, DenseRerank, HybridRerank):
        kw = (
            {"encode": fake_encode}
            if cls is Dense
            else {"encode": fake_encode, "score": fake_score}
        )
        a = cls(ASSIGNMENT, CORPORA, **kw).retrieve(QUERY, PROBLEM, 5)
        b = cls(ASSIGNMENT, reversed_corpora, **kw).retrieve(QUERY, PROBLEM, 5)
        assert [h.entry.entry_id for h in a] == [h.entry.entry_id for h in b]


def test_a_fold_with_no_corpus_is_an_error():
    with pytest.raises(ValueError, match="no corpus"):
        NoRetrieval(ASSIGNMENT, {0: CORPORA[0]})


def test_duplicate_entry_ids_are_an_error():
    with pytest.raises(ValueError, match="duplicate"):
        NoRetrieval(ASSIGNMENT, {**CORPORA, 0: [*CORPORA[0], CORPORA[0][0]]})


def test_depth_below_one_is_an_error():
    with pytest.raises(ValueError, match="depth"):
        DenseRerank(ASSIGNMENT, CORPORA, encode=fake_encode, score=fake_score, depth=0)


def test_entry_text_leaves_out_the_fix():
    entry = INCIDENTS[0]
    assert entry.fix_command not in entry_text(entry)
    assert entry.summary in entry_text(entry)


def test_a_development_problem_is_served_through_a_key_of_its_own():
    dev = PastFix(
        entry_id="dev-entry",
        source=Source.RUNBOOK,
        fault_type="ScalePod",
        target_service="demo",
        summary="replicas zero scaled",
        fix_command="kubectl scale deployment demo --replicas=1",
    )
    retriever = DenseRerank(
        {**ASSIGNMENT, "dev-problem-1": -1},
        {**CORPORA, -1: [dev]},
        encode=fake_encode,
        score=fake_score,
    )
    hits = retriever.retrieve("replicas zero", "dev-problem-1", 3)
    assert [h.entry.entry_id for h in hits] == ["dev-entry"]


def test_bm25_leaves_out_entries_that_score_zero():
    # an entry BM25 does not match would otherwise get a fusion rank from its entry id alone
    hybrid = configs()["hybrid_rerank"]
    fold = ASSIGNMENT[PROBLEM]
    assert hybrid._sparse("zzz unknown", fold) == {}
    matched = hybrid._sparse("port refused", fold)
    assert matched and all(s > 0 for s in matched.values())
    assert len(matched) < len(CORPORA[fold])


def test_fusion_sums_reciprocal_ranks_with_the_constant():
    # dense ranks A, B, C, E, D; BM25 ranks D, B, E, A and leaves C unmatched. With k = 60:
    # B 0.03226, A 0.03202, D 0.03178, E 0.03150, C 0.01587. Using 1 / rank, fusing BM25 alone,
    # max in place of sum, or the reversed order each gives another order.
    hybrid = configs()["hybrid_rerank"]
    dense = {"A": 0.9, "B": 0.8, "C": 0.7, "E": 0.6, "D": 0.5}
    sparse = {"D": 4.0, "B": 3.0, "E": 2.0, "A": 1.0}
    hybrid._dense = lambda query, fold: dense
    hybrid._sparse = lambda query, fold: sparse
    assert hybrid._first_stage(QUERY, 0) == ["B", "A", "D", "E", "C"]


def test_bm25_keeps_a_word_that_is_in_exactly_half_the_entries():
    # rank-bm25's own idf is 0 for such a word, so every match would score 0 and be dropped
    entries = [
        PastFix(
            entry_id=f"runbook-{n}",
            source=Source.RUNBOOK,
            fault_type="ScalePod",
            target_service="demo",
            summary=("shared " if n < 5 else "") + f"word{n}",
            fix_command=f"fix {n}",
        )
        for n in range(10)
    ]
    hybrid = HybridRerank({"dev-1": -1}, {-1: entries}, encode=fake_encode, score=fake_score)
    assert sorted(hybrid._sparse("shared", -1)) == [f"runbook-{n}" for n in range(5)]


def test_bm25_idf_falls_as_more_entries_share_the_word():
    entries = [[f"w{k}" for k in range(10) if n < k] for n in range(10)]
    idf = _LuceneBM25(entries).idf
    # w4 is in 4 of the 10 entries, w5 in 5, w6 in 6
    assert idf["w4"] > idf["w5"] > idf["w6"] > 0
