import ast
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

ROOT = Path(__file__).resolve().parents[2]
TOUCH_LOG = ROOT / "runs" / "touch_log.jsonl"
# touches that run no system: problem creation, the seal and the R3.7 pool queries
NOT_EVALUATION = ("create-", "seal-", "query-")
DEV, POOL = "p-synth-dev", "p-synth-pool"
REGISTRY = Registry(slices={DEV: Slice.DEVELOPMENT, POOL: Slice.POOL}, placeholder=None)
T1, T2 = "2026-11-02T19:01:00Z", "2026-11-04T09:00:00Z"
# deterministic fakes: every entry is equally close, and the reranker prefers longer texts
MODELS = (lambda texts: [[1.0, 0.0] for _ in texts], lambda q, texts: [len(t) for t in texts])


def _record(tmp_path, entries) -> Path:
    path = tmp_path / "fixed.json"
    path.write_text(json.dumps({"configuration": "iii", "entries": entries}))
    return path


def test_live_settings_match_the_fixed_record():
    check_fixed()


def test_first_fix_precedes_every_evaluation_touch():
    fixed = json.loads(FIXED_PATH.read_text(encoding="utf-8"))["entries"][0]["timestamp"]
    lines = TOUCH_LOG.read_text(encoding="utf-8").splitlines()
    touches = [json.loads(line) for line in lines if line]
    # both use the same UTC format, so the strings compare in time order
    early = [
        t["run_id"]
        for t in touches
        if not t["run_id"].startswith(NOT_EVALUATION) and t["timestamp"] <= fixed
    ]
    assert early == []


def test_changed_setting_is_refused_without_a_new_entry(tmp_path):
    changed = settings() | {"depth": 10}
    path = _record(tmp_path, [{"timestamp": T1, "reason": "fixed", "settings": changed}])
    with pytest.raises(ValueError, match=r"\['depth'\] differ"):
        check_fixed(path)


def test_new_entry_with_a_reason_is_accepted(tmp_path):
    old = settings() | {"depth": 10}
    path = _record(
        tmp_path,
        [
            {"timestamp": T1, "reason": "fixed", "settings": old},
            {"timestamp": T2, "reason": "the group agreed", "settings": settings()},
        ],
    )
    check_fixed(path)


@pytest.mark.parametrize(
    "change, match",
    [
        ({"reason": " "}, "no reason"),
        ({"timestamp": "2026-11-03"}, "is not like"),
        ({"timestamp": None}, "is not like"),
        ({"settings": None}, "not an object with settings"),
    ],
)
def test_bad_entry_is_refused(tmp_path, change, match):
    entry = {"timestamp": T1, "reason": "fixed", "settings": settings()} | change
    with pytest.raises(ValueError, match=match):
        check_fixed(_record(tmp_path, [entry]))


def test_entries_out_of_time_order_are_refused(tmp_path):
    entries = [
        {"timestamp": T2, "reason": "fixed", "settings": settings()},
        {"timestamp": T1, "reason": "again", "settings": settings()},
    ]
    with pytest.raises(ValueError, match="entry 2 is not later"):
        check_fixed(_record(tmp_path, entries))


@pytest.mark.parametrize("content", ["[]", "{}", '{"entries": []}', '{"entries": [1]}'])
def test_damaged_record_is_refused(tmp_path, content):
    path = tmp_path / "fixed.json"
    path.write_text(content)
    with pytest.raises(ValueError, match="no entry|not an object"):
        check_fixed(path)


def _names(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # Name, Attribute and import alias nodes, so an import, a call and a passed reference all count
    fields = ("id", "attr", "name")
    return {getattr(node, field, None) for node in ast.walk(tree) for field in fields}


def test_run_code_builds_iii_only_through_the_fixed_record():
    # smoke_retrieval.py loads the models and runs all four configurations on the stub corpus
    files = [*ROOT.glob("scripts/**/*.py"), *ROOT.glob("src/**/*.py")]
    direct = {p.relative_to(ROOT).as_posix() for p in files if "DenseRerank" in _names(p)}
    assert direct == {
        "scripts/smoke_retrieval.py",
        "src/remvalgate/retrieval/configs.py",
        "src/remvalgate/retrieval/fixed.py",
    }


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
