import pytest

from remvalgate.baselines.untuned import (
    CONFIGURATIONS,
    conflicting_flags,
    pool_untuned_retriever,
    untuned_retriever,
)
from remvalgate.harness.folds import fault_type, pool_folds, pool_ids
from remvalgate.harness.manifest import Registry, Slice
from remvalgate.retrieval import PastFix, RetrieverRefused, Source
from remvalgate.retrieval.configs import Dense, NoRetrieval
from remvalgate.retrieval.pool_corpus import RUNBOOK
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


# synthetic ids that carry fault type stems, so no pool problem is read; two per fold, so a
# retriever that held out only the problem itself would fail
SYNTH_POOL = [f"synth-{stem}-{i}" for stem in ("revoke_auth", "scale_pod_zero") for i in range(4)]
POOL_REGISTRY = Registry(
    slices={DEV: Slice.DEVELOPMENT} | dict.fromkeys(SYNTH_POOL, Slice.POOL), placeholder=None
)


@pytest.fixture
def synthetic_incidents(monkeypatch):
    def incident(problem_id):
        return PastFix(
            entry_id=problem_id,
            source=Source.INCIDENT,
            fault_type=fault_type(problem_id),
            target_service=f"demo-{problem_id}",
            summary=f"incident {problem_id} of a synthetic fault",
            fix_command=f"kubectl -n demo patch deployment demo-{problem_id} --type=json",
        )

    monkeypatch.setattr(
        "remvalgate.retrieval.fixed.pool_incidents",
        lambda registry: [incident(p) for p in pool_ids(registry)],
    )


def test_pool_3a_retrieves_nothing(synthetic_incidents):
    retriever = pool_untuned_retriever("3a", POOL_REGISTRY)
    assert type(retriever) is NoRetrieval
    assert all(retriever.retrieve("pods pending", p, 3) == [] for p in SYNTH_POOL)


@pytest.mark.parametrize("baseline", ["3b", "3c"])
def test_pool_3b_and_3c_serve_each_problem_from_the_fold_that_holds_it_out(
    synthetic_incidents, baseline
):
    retriever = pool_untuned_retriever(baseline, POOL_REGISTRY, models=MODELS)
    assignment = pool_folds(POOL_REGISTRY)
    runbook = {e.entry_id for e in RUNBOOK}
    for problem_id, fold in assignment.items():
        # a depth above the corpus size returns the whole corpus
        hits = retriever.retrieve("pods pending", problem_id, 100)
        training = {i for i, f in assignment.items() if f != fold}
        assert {h.entry.entry_id for h in hits} == training | runbook


@pytest.mark.parametrize("baseline", sorted(CONFIGURATIONS))
def test_every_pool_baseline_refuses_a_development_problem(synthetic_incidents, baseline):
    retriever = pool_untuned_retriever(baseline, POOL_REGISTRY, models=MODELS)
    with pytest.raises(RetrieverRefused):
        retriever.retrieve("pods pending", DEV, 3)


def test_pool_untuned_retriever_refuses_an_unknown_baseline():
    with pytest.raises(ValueError, match="baseline"):
        pool_untuned_retriever("3d", POOL_REGISTRY, models=MODELS)
