import json
from pathlib import Path

import pytest

from remvalgate.harness.manifest import Registry, Slice
from remvalgate.retrieval import RetrieverRefused
from remvalgate.retrieval.configs import DenseRerank
from remvalgate.retrieval.fixed import (
    FIXED_PATH,
    check_fixed,
    configuration_iii,
    development_retriever,
    settings,
)
from remvalgate.retrieval.stub import STUB_CORPUS

TOUCH_LOG = Path(__file__).resolve().parents[2] / "runs" / "touch_log.jsonl"
# touches that run no system: problem creation, the seal and the R3.7 pool queries
NOT_EVALUATION = ("create-", "seal-", "query-")
DEV, POOL = "p-synth-dev", "p-synth-pool"
REGISTRY = Registry(slices={DEV: Slice.DEVELOPMENT, POOL: Slice.POOL}, placeholder=None)
# deterministic fakes: every entry is equally close, and the reranker prefers longer texts
MODELS = (lambda texts: [[1.0, 0.0] for _ in texts], lambda q, texts: [len(t) for t in texts])


def _record(tmp_path, entries) -> Path:
    path = tmp_path / "fixed.json"
    path.write_text(json.dumps({"configuration": "iii", "entries": entries}))
    return path


def test_live_settings_match_the_fixed_record():
    check_fixed()


def test_first_fix_precedes_every_evaluation_touch():
    fixed = json.loads(FIXED_PATH.read_text())["entries"][0]["date"]
    touches = [json.loads(line) for line in TOUCH_LOG.read_text().splitlines() if line]
    early = [
        t["run_id"]
        for t in touches
        if not t["run_id"].startswith(NOT_EVALUATION) and t["timestamp"][:10] < fixed
    ]
    assert early == []


def test_changed_setting_is_refused_without_a_new_entry(tmp_path):
    changed = settings() | {"depth": 10}
    path = _record(tmp_path, [{"date": "2026-11-03", "reason": "fixed", "settings": changed}])
    with pytest.raises(ValueError, match=r"\['depth'\] differ"):
        check_fixed(path)


def test_new_entry_with_a_reason_is_accepted(tmp_path):
    old = settings() | {"depth": 10}
    path = _record(
        tmp_path,
        [
            {"date": "2026-11-03", "reason": "fixed", "settings": old},
            {"date": "2026-11-04", "reason": "the group agreed", "settings": settings()},
        ],
    )
    check_fixed(path)


@pytest.mark.parametrize("missing", ["date", "reason"])
def test_entry_without_date_or_reason_is_refused(tmp_path, missing):
    entry = {"date": "2026-11-03", "reason": "fixed", "settings": settings()} | {missing: " "}
    with pytest.raises(ValueError, match="no date or no reason"):
        check_fixed(_record(tmp_path, [entry]))


def test_empty_record_is_refused(tmp_path):
    with pytest.raises(ValueError, match="no entry"):
        check_fixed(_record(tmp_path, []))


def test_configuration_iii_is_dense_rerank_at_the_fixed_depth():
    retriever = configuration_iii({DEV: -1}, {-1: STUB_CORPUS}, models=MODELS)
    assert type(retriever) is DenseRerank
    assert retriever._depth == settings()["depth"]


def test_development_retriever_serves_development_problems_only():
    retriever = development_retriever(REGISTRY, models=MODELS)
    hits = retriever.retrieve("pods pending", DEV, 3)
    assert [h.rank for h in hits] == [1, 2, 3]
    assert {h.entry.entry_id for h in hits} <= {e.entry_id for e in STUB_CORPUS}
    with pytest.raises(RetrieverRefused):
        retriever.retrieve("pods pending", POOL, 3)


def test_development_retriever_needs_a_development_problem():
    with pytest.raises(ValueError, match="no development problem"):
        development_retriever(Registry(slices={POOL: Slice.POOL}, placeholder=None), models=MODELS)
