import dataclasses

import pytest
from hypothesis import given
from hypothesis import strategies as st

from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, Registry, Slice, load_registry
from remvalgate.retrieval import Hit, PastFix, Retriever, RetrieverRefused, Source
from remvalgate.retrieval.stub import STUB_CORPUS, DevStubRetriever

POOL, CONF, DEV, SINGLE = "p-synth-pool", "p-synth-conf", "p-synth-dev", "p-synth-single"
REGISTRY = Registry(
    slices={
        POOL: Slice.POOL,
        CONF: Slice.CONFIRMATION,
        DEV: Slice.DEVELOPMENT,
        SINGLE: Slice.SINGLETON,
    },
    placeholder=None,
)


def _fix(entry_id: str, summary: str, fault_type: str = "ft", service: str = "svc") -> PastFix:
    return PastFix(
        entry_id=entry_id,
        source=Source.INCIDENT,
        fault_type=fault_type,
        target_service=service,
        summary=summary,
        fix_command="kubectl rollout restart deployment/svc",
    )


CORPUS = (
    _fix("e-a", "service port mismatch causes connection refused", "port", "alpha"),
    _fix("e-b", "deployment scaled to zero replicas", "scale", "beta"),
    _fix("e-c", "pod pending on a node that does not exist", "node", "gamma"),
)


def _stub(corpus=CORPUS) -> DevStubRetriever:
    return DevStubRetriever(registry=REGISTRY, corpus=corpus)


def test_stub_satisfies_the_retriever_protocol():
    assert isinstance(_stub(), Retriever)


def test_hits_are_ranked_best_first_with_one_based_ranks():
    hits = _stub().retrieve("deployment scaled to zero", DEV, k=3)
    assert [h.rank for h in hits] == [1, 2, 3]
    assert [h.entry.entry_id for h in hits][0] == "e-b"
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_hits_have_the_documented_shape():
    (hit,) = _stub().retrieve("port connection refused", DEV, k=1)
    assert isinstance(hit, Hit)
    assert isinstance(hit.entry, PastFix)
    assert isinstance(hit.score, float)
    assert {f.name for f in dataclasses.fields(hit.entry)} == {
        "entry_id",
        "source",
        "fault_type",
        "target_service",
        "summary",
        "fix_command",
    }


def test_k_caps_the_result_and_a_large_k_returns_the_whole_corpus():
    assert len(_stub().retrieve("port", DEV, k=2)) == 2
    assert len(_stub().retrieve("port", DEV, k=50)) == len(CORPUS)


def test_ties_break_by_entry_id_so_the_order_is_stable():
    corpus = (_fix("e-z", "same words"), _fix("e-m", "same words"), _fix("e-a", "same words"))
    hits = _stub(corpus).retrieve("same words", DEV, k=3)
    assert [h.entry.entry_id for h in hits] == ["e-a", "e-m", "e-z"]


def test_a_query_that_matches_nothing_still_returns_k_entries_in_id_order():
    hits = _stub().retrieve("zzzz qqqq", DEV, k=3)
    assert [h.entry.entry_id for h in hits] == ["e-a", "e-b", "e-c"]
    assert all(h.score == 0.0 for h in hits)


def test_matching_ignores_case_and_punctuation():
    lower = _stub().retrieve("scaled to zero", DEV, k=3)
    messy = _stub().retrieve("SCALED, to... Zero!", DEV, k=3)
    assert lower == messy


@pytest.mark.parametrize("k", [0, -1, -100])
def test_k_below_one_is_refused(k):
    with pytest.raises(ValueError, match="k"):
        _stub().retrieve("port", DEV, k=k)


@pytest.mark.parametrize("problem_id", [POOL, CONF, SINGLE])
def test_pool_confirmation_and_singleton_problems_are_refused(problem_id):
    with pytest.raises(RetrieverRefused, match="development"):
        _stub().retrieve("port", problem_id, k=1)


@pytest.mark.parametrize("problem_id", ["p-unknown", "", "P-SYNTH-DEV", " p-synth-dev"])
def test_unknown_problem_ids_are_refused(problem_id):
    with pytest.raises(RetrieverRefused, match="slice registry"):
        _stub().retrieve("port", problem_id, k=1)


def test_a_refused_call_returns_nothing_and_leaves_the_stub_usable():
    stub = _stub()
    with pytest.raises(RetrieverRefused):
        stub.retrieve("port", POOL, k=1)
    assert stub.retrieve("port", DEV, k=1)


def test_the_committed_development_problems_are_accepted():
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    dev = [i for i, s in registry.slices.items() if s is Slice.DEVELOPMENT]
    assert dev
    stub = DevStubRetriever(registry=registry)
    for problem_id in dev:
        assert stub.retrieve("port", problem_id, k=1)


def test_every_committed_non_development_problem_is_refused():
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    stub = DevStubRetriever(registry=registry)
    others = [i for i, s in registry.slices.items() if s is not Slice.DEVELOPMENT]
    assert others
    for problem_id in others:
        with pytest.raises(RetrieverRefused):
            stub.retrieve("port", problem_id, k=1)


def test_the_shipped_corpus_is_synthetic_and_names_no_evaluation_problem():
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    ids = {e.entry_id for e in STUB_CORPUS}
    assert len(ids) == len(STUB_CORPUS) > 0
    for entry in STUB_CORPUS:
        text = " ".join(
            [entry.entry_id, entry.summary, entry.fix_command, entry.target_service]
        ).lower()
        assert entry.entry_id.startswith("stub-")
        # a real service name here would reuse a pool problem's service (BRD 7.2)
        assert entry.target_service.startswith("demo-")
        for problem_id in registry.slices:
            assert problem_id.lower() not in text


def test_a_duplicate_entry_id_in_the_corpus_is_refused():
    with pytest.raises(ValueError, match="duplicate"):
        _stub((_fix("e-a", "one"), _fix("e-a", "two")))


def test_an_empty_corpus_is_refused():
    with pytest.raises(ValueError, match="corpus"):
        _stub(())


@given(
    query=st.text(max_size=80),
    k=st.integers(min_value=1, max_value=10),
)
def test_any_query_gives_ranked_deterministic_hits(query, k):
    first = _stub().retrieve(query, DEV, k=k)
    second = _stub().retrieve(query, DEV, k=k)
    assert first == second
    assert len(first) == min(k, len(CORPUS))
    assert [h.rank for h in first] == list(range(1, len(first) + 1))
    scores = [h.score for h in first]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert len({h.entry.entry_id for h in first}) == len(first)
