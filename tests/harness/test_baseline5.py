"""Tests of baseline 5's held-out export and summary on synthetic labels and transcripts only.

Kept beside test_training_data.py, whose synthetic oracle runs it reuses.
"""

import hashlib
import json

import pytest
from test_training_data import ASSIGNMENT, NOW, PIN, REGISTRY, STAMP, _files, _repo, _run, _sum

from remvalgate.baselines.baseline5 import (
    HELDOUT_FORMAT,
    SCORES_FORMAT,
    export_heldout,
    heldout_problem,
    problem_result,
    summarize,
)
from remvalgate.harness.manifest import read_touches
from remvalgate.harness.training_data import MASK, sampling_messages

PROBLEM = sorted(ASSIGNMENT)[0]


def test_a_held_out_problem_keeps_all_k_candidates_and_the_unmasked_sampling_messages():
    run = _run(PROBLEM, retrieved=[sorted(ASSIGNMENT)[1]])
    problem = heldout_problem(run)
    seeds = [c["seed"] for c in problem["candidates"]]
    assert seeds == [0, 1, 2, 3, 4]
    # works and its merged duplicate recovered; the failure, no command and unsendable do not
    assert [c["works"] for c in problem["candidates"]] == [True, True, False, False, False]
    assert problem["candidates"][3]["outcome"] is None
    assert problem["candidates"][1]["completion"] == run.transcript["candidates"][1]["reply"]
    namespace = run.manifest["cluster"]["namespace"]
    expected = sampling_messages(run.transcript["context"], run.transcript["diagnosis"], namespace)
    assert problem["messages"] == expected
    assert MASK not in json.dumps(problem["messages"])
    assert problem["baseline3c_solved"] is False


def _export(tmp_path, paths, repo):
    return export_heldout(
        paths,
        tmp_path / "out",
        registry=REGISTRY,
        log_path=repo / "runs" / "touch_log.jsonl",
        repo=repo,
        aiopslab_commit=PIN,
        now=NOW,
        checksum=_sum,
    )


def test_export_logs_one_touch_per_pool_problem_and_writes_each_fold_its_held_out_problems(
    tmp_path,
):
    repo = _repo(tmp_path)
    written = _export(tmp_path, _files(tmp_path, sorted(ASSIGNMENT)), repo)
    assert len(written) == 4
    touches = [
        t for t in read_touches(repo / "runs" / "touch_log.jsonl") if t["run_id"][:5] != "seal-"
    ]
    assert len(touches) == len(ASSIGNMENT)
    assert all(t["run_id"].startswith("b5-heldout-") and t["slice"] == "pool" for t in touches)
    for fold, path in enumerate(written):
        body = json.loads(path.read_text())
        held = sorted(p for p, f in ASSIGNMENT.items() if f == fold)
        assert path.name == f"heldout-baseline5-fold{fold}-{STAMP}.json"
        assert body["format"] == HELDOUT_FORMAT and body["fold"] == fold
        assert [p["problem_id"] for p in body["problems"]] == held
        assert sorted(body["labels_files"]) == held and len(body["touches"]) == len(held)


def test_export_refuses_an_incomplete_pool_before_any_touch(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(ValueError, match="no labels file"):
        _export(tmp_path, _files(tmp_path, sorted(ASSIGNMENT)[1:]), repo)
    assert all(t["run_id"][:5] == "seal-" for t in read_touches(repo / "runs" / "touch_log.jsonl"))


def _problem(works, solved=False):
    return {
        "problem_id": "p",
        "baseline3c_solved": solved,
        "candidates": [{"id": f"p/{s}", "seed": s, "works": w} for s, w in enumerate(works)],
    }


def test_the_ranking_is_by_log_probability_with_ties_in_draw_order_and_a_coverage_curve():
    problem = _problem([False, True, False, True])
    scores = {"p/0": -5.0, "p/1": -1.0, "p/2": -1.0, "p/3": -9.0}
    result = problem_result(problem, scores)
    assert [r["seed"] for r in result["ranking"]] == [1, 2, 0, 3]
    # seeds 1 and 2 tie at the top, one of them works
    assert result["top1"] == 0.5
    assert [c["risk"] for c in result["coverage_curve"]] == [0.5, 0.5, 2 / 3, 0.5]
    assert result["working"] == 2 and result["candidates"] == 4


def test_a_problem_with_no_candidates_is_not_solved():
    result = problem_result(_problem([]), {})
    assert result["top1"] == 0.0 and result["coverage_curve"] == [] and result["aurc"] is None


def _write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(json.dumps(body))
    return path


def test_summarize_totals_the_folds_and_refuses_a_scores_file_of_another_held_out_file(tmp_path):
    heldout = _write(
        tmp_path,
        "h.json",
        {"format": HELDOUT_FORMAT, "fold": 0, "problems": [_problem([False, True], solved=True)]},
    )
    sha = hashlib.sha256(heldout.read_bytes()).hexdigest()
    scores = {
        "format": SCORES_FORMAT,
        "fold": 0,
        "heldout": {"file": "h.json", "sha256": sha},
        "model": "m",
        "scores": {"p/0": -3.0, "p/1": -2.0},
    }
    summary = summarize([(heldout, _write(tmp_path, "s.json", scores))])
    assert summary["baseline5_solved"] == 1.0 and summary["baseline3c_solved"] == 1
    assert summary["runs"] == [{k: v for k, v in scores.items() if k != "scores"}]
    other = _write(tmp_path, "o.json", scores | {"heldout": {"file": "h.json", "sha256": "0"}})
    with pytest.raises(ValueError, match="another held-out file"):
        summarize([(heldout, other)])
    missing = _write(tmp_path, "m.json", scores | {"scores": {"p/0": -1.0}})
    with pytest.raises(ValueError, match="exactly the held-out candidates"):
        summarize([(heldout, missing)])
