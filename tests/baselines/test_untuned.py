import pytest

from remvalgate.baselines.untuned import CONFIGURATIONS, conflicting_flags, untuned_retriever
from remvalgate.harness.manifest import Registry, Slice
from remvalgate.retrieval import RetrieverRefused
from remvalgate.retrieval.configs import Dense, NoRetrieval
from remvalgate.retrieval.stub import STUB_CORPUS

DEV, POOL = "p-synth-dev", "p-synth-pool"
REGISTRY = Registry(slices={DEV: Slice.DEVELOPMENT, POOL: Slice.POOL}, placeholder=None)
# deterministic fakes: the embedder ranks by text length, the reranker reverses it
MODELS = (
    lambda texts: [[float(len(t)), 1.0] for t in texts],
    lambda q, texts: [-len(t) for t in texts],
)


def test_the_three_baselines_map_to_configurations_i_to_iii():
    assert CONFIGURATIONS == {"3a": "i", "3b": "ii", "3c": "iii"}


def test_3a_retrieves_nothing_and_loads_no_model():
    retriever = untuned_retriever("3a", REGISTRY)
    assert type(retriever) is NoRetrieval
    assert retriever.retrieve("pods pending", DEV, 3) == []


def test_3b_is_dense_retrieval_over_the_stub_corpus():
    retriever = untuned_retriever("3b", REGISTRY, models=MODELS)
    assert type(retriever) is Dense
    hits = retriever.retrieve("pods pending", DEV, 3)
    assert [h.rank for h in hits] == [1, 2, 3]
    assert {h.entry.entry_id for h in hits} <= {e.entry_id for e in STUB_CORPUS}


def test_3c_reranks_the_dense_order():
    dense = [
        h.entry.entry_id
        for h in untuned_retriever("3b", REGISTRY, models=MODELS).retrieve("q", DEV, 5)
    ]
    reranked = [
        h.entry.entry_id
        for h in untuned_retriever("3c", REGISTRY, models=MODELS).retrieve("q", DEV, 5)
    ]
    assert type(untuned_retriever("3c", REGISTRY, models=MODELS)) not in (Dense, NoRetrieval)
    assert sorted(reranked) == sorted(dense) and reranked != dense


@pytest.mark.parametrize("baseline", sorted(CONFIGURATIONS))
def test_every_baseline_refuses_a_problem_outside_development(baseline):
    retriever = untuned_retriever(baseline, REGISTRY, models=MODELS)
    with pytest.raises(RetrieverRefused):
        retriever.retrieve("pods pending", POOL, 3)


@pytest.mark.parametrize("baseline", sorted(CONFIGURATIONS))
def test_every_baseline_needs_a_development_problem(baseline):
    registry = Registry(slices={POOL: Slice.POOL}, placeholder=None)
    with pytest.raises(ValueError, match="no development problem"):
        untuned_retriever(baseline, registry, models=MODELS)


def test_no_conflicting_flag_with_the_defaults():
    assert conflicting_flags(judge=False, escalate_when_unsure=False, candidates=None) == []


@pytest.mark.parametrize(
    "flags, expected",
    [
        ({"judge": True}, ["--judge"]),
        ({"escalate_when_unsure": True}, ["--escalate-when-unsure"]),
        ({"candidates": 8}, ["--candidates"]),
        (
            {"judge": True, "escalate_when_unsure": True, "candidates": 1},
            ["--judge", "--escalate-when-unsure", "--candidates"],
        ),
    ],
)
def test_sampling_judging_and_escalation_flags_conflict(flags, expected):
    given = {"judge": False, "escalate_when_unsure": False, "candidates": None} | flags
    assert conflicting_flags(**given) == expected


def test_an_unknown_baseline_is_refused():
    with pytest.raises(ValueError, match="expected one of"):
        untuned_retriever("3d", REGISTRY, models=MODELS)
