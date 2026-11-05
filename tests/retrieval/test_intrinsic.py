"""Tests of the intrinsic retrieval metrics (E4-S3). Fake models and synthetic queries only: no
recorded pool query is read here."""

import json
import re

import pytest

from remvalgate.harness.folds import pool_folds
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, load_registry
from remvalgate.retrieval import PastFix, Source
from remvalgate.retrieval.intrinsic import (
    CONFIGURATIONS,
    evaluate,
    query_lengths,
    read_queries,
    same_type,
    same_type_and_service,
    saturation,
    score,
)
from remvalgate.retrieval.pool_corpus import pool_incidents

REGISTRY = load_registry(DEFAULT_REGISTRY_PATH)
_WORD = re.compile(r"[a-z0-9]+")


def _encode(texts):
    # a bag of hashed words, normalised, so a shared word raises the cosine
    vectors = []
    for text in texts:
        v = [0.0] * 32
        for word in _WORD.findall(text.lower()):
            v[sum(word.encode()) % 32] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        vectors.append([x / norm for x in v])
    return vectors


def _rerank(query, texts):
    words = set(_WORD.findall(query.lower()))
    return [len(words & set(_WORD.findall(t.lower()))) for t in texts]


MODELS = (_encode, _rerank)


def _entry(entry_id, type_="A", service="s"):
    return PastFix(entry_id, Source.INCIDENT, type_, service, "summary", "fix")


def _report(queries=None):
    records = pool_incidents(REGISTRY)
    return evaluate(
        queries or {r.entry_id: f"{r.fault_type} {r.summary}" for r in records}, REGISTRY, MODELS
    )


def test_score_is_right_on_a_hand_worked_example():
    q1, q2 = _entry("q1"), _entry("q2")
    hit, miss = _entry("hit"), _entry("miss", type_="B")
    # q1 finds its first relevant entry at rank 1 and q2 at rank 3; each has 2 relevant in the
    # corpus, and q1 finds both in the first 3
    results = [(q1, [hit, miss, hit]), (q2, [miss, miss, hit])]
    report = score(results, same_type, lambda q: 2)
    assert report["queries"] == 2
    assert report["hit_at_1"] == {"count": 1, "rate": 0.5}
    assert report["hit_at_3"] == {"count": 2, "rate": 1.0}
    assert report["mrr"] == pytest.approx((1 + 1 / 3) / 2)
    assert report["recall_at_3"]["found"] == 3
    assert report["recall_at_3"]["relevant"] == 4
    assert report["recall_at_3"]["rate"] == pytest.approx(0.75)
    assert report["recall_at_3"]["ceiling"] == 1.0


def test_recall_ceiling_falls_below_one_past_three_relevant():
    q = _entry("q")
    report = score([(q, [q, q, q])], same_type, lambda q: 6)
    assert report["recall_at_3"]["rate"] == pytest.approx(0.5)
    assert report["recall_at_3"]["ceiling"] == pytest.approx(0.5)


def test_query_with_no_relevant_entry_has_no_recall_and_counts_zero():
    q = _entry("q")
    report = score([(q, [_entry("x", type_="B")])], same_type, lambda q: 0)
    assert report["queries_with_relevant"] == 0
    assert report["recall_at_3"] is None
    assert report["hit_at_3"]["count"] == 0
    assert report["mrr"] == 0


def test_strict_label_needs_the_service_too():
    q = _entry("q", service="s")
    assert same_type_and_service(q, _entry("e", service="s"))
    assert not same_type_and_service(q, _entry("e", service="other"))
    assert not same_type_and_service(q, _entry("e", type_="B", service="s"))


def _configs(ii, iii, iv, n=16):
    return {
        c: {"fault_type": {"all": {"queries": n, "hit_at_3": {"count": count}}}}
        for c, count in {"ii": ii, "iii": iii, "iv": iv}.items()
    }


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ((12, 12, 12), True),  # all tie
        ((12, 16, 13), True),  # one reaches every query
        ((12, 13, 14), False),
        ((12, 12, 13), False),  # two tie, the third separates
    ],
)
def test_saturation_rule(counts, expected):
    assert saturation(_configs(*counts))["saturated"] is expected


def test_evaluate_reports_every_configuration_label_and_group():
    report = _report()
    assert tuple(report["configurations"]) == CONFIGURATIONS
    for by_label in report["configurations"].values():
        assert set(by_label) == {"fault_type", "type_and_service"}
        for by_group in by_label.values():
            assert {g: m["queries"] for g, m in by_group.items()} == {
                "all": 16,
                "database": 4,
                "target_port": 6,
            }


def test_no_retrieval_scores_zero_with_every_query_counted():
    metrics = _report()["configurations"]["i"]["fault_type"]["all"]
    assert metrics["hit_at_3"]["count"] == 0
    assert metrics["mrr"] == 0
    assert metrics["recall_at_3"]["rate"] == 0
    assert metrics["queries_with_relevant"] == 16


def test_no_query_retrieves_itself():
    report = _report()
    for problem_id, row in report["per_query"].items():
        for top in row["top_3"].values():
            assert problem_id not in top


def test_the_strict_label_has_no_relevant_entry_in_the_pool():
    # within a fault type every instance has its own service, and runbook entries name <service>,
    # so the check the BRD proposes cannot separate the configurations on this pool
    report = _report()
    assert all(r["relevant_entries"]["type_and_service"] == 0 for r in report["per_query"].values())
    strict = report["configurations"]["iii"]["type_and_service"]["all"]
    assert strict["queries_with_relevant"] == 0
    assert strict["recall_at_3"] is None


def test_every_fault_type_query_has_a_relevant_entry():
    report = _report()
    assert all(r["relevant_entries"]["fault_type"] >= 1 for r in report["per_query"].values())


def test_evaluate_refuses_a_missing_or_extra_query():
    queries = {problem_id: "q" for problem_id in pool_folds(REGISTRY)}
    missing = dict(queries)
    missing.popitem()
    with pytest.raises(ValueError, match="differ"):
        evaluate(missing, REGISTRY, MODELS)
    with pytest.raises(ValueError, match="differ"):
        evaluate({**queries, "other": "q"}, REGISTRY, MODELS)


def test_read_queries_joins_files_and_refuses_a_repeat(tmp_path):
    def write(name, rows):
        path = tmp_path / name
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return path

    one = write("a.jsonl", [{"problem_id": "p1", "query": "one", "symptom_query": ""}])
    two = write("b.jsonl", [{"problem_id": "p2", "query": "two"}])
    assert read_queries([one, two]) == {"p1": "one", "p2": "two"}
    again = write("c.jsonl", [{"problem_id": "p1", "query": "other"}])
    with pytest.raises(ValueError, match="more than one"):
        read_queries([one, again])


def test_query_lengths_sees_a_service_cut_off_by_the_limit():
    def tokenize(text):
        return text.split()

    def decode(ids):
        # splits the hyphen as a real tokenizer does
        return " ".join(ids).replace("-", " - ")

    queries = {"early": "user-service a b c", "late": "a b c user-service", "none": "a b"}
    services = dict.fromkeys(queries, "user-service")
    report = query_lengths(queries, services, tokenize, decode, limit=3)
    assert report["early"]["truncated"] is True
    assert report["early"]["service_in_read_part"] is True
    assert report["late"]["service_in_query"] is True
    assert report["late"]["service_in_read_part"] is False
    assert report["none"]["truncated"] is False
    assert report["none"]["service_in_query"] is False
