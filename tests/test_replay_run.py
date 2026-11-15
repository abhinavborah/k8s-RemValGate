"""Checks of the replay script on made-up files: the order of the parts, a refused candidate, an
escalation, and the cut of a batch recording to one run."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "replay_run.py"
spec = importlib.util.spec_from_file_location("replay_run", SCRIPT)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)

MANIFEST = {
    "run_id": "oracle-dev-scale_pod_zero_social_net-mitigation-1-x",
    "problem_id": "dev-scale_pod_zero_social_net-mitigation-1",
    "timestamp": "2026-11-10T14:00:10Z",
    "k": 2,
    "r": 2,
    "seed": 1000,
    "sampling": {"baseline": "3c"},
}


def test_parts_in_order_with_refusal():
    transcript = {
        "first_observation": "NAME READY\nuser-service 0/0",
        "diagnosis": {"fault_type": "ScalePod", "service": "user-service", "reason": "0 replicas"},
        "stop": "final",
    }
    labels = {
        "n": 2,
        "c": 1,
        "candidates": [
            {"seed": 0, "command": "kubectl scale ...", "rejection": None, "outcome": "RECOVERED"},
            {"seed": 1, "command": "kubectl delete ns x", "rejection": "allow-list: FLAG_DENIED"},
            {"seed": 2, "command": None, "rejection": "Expecting ',' delimiter"},
        ],
        "plan": {"command": "kubectl scale ...", "outcome": "RECOVERED", "rejection": None},
    }
    parts = replay.stages(MANIFEST, transcript, labels, {})
    assert [title[0] for title, _ in parts] == list("123456")
    text = dict(parts)
    assert "ScalePod" in text["1. Injected fault"] and "seed 1000" in text["1. Injected fault"]
    assert "[refused: FLAG_DENIED]" in text["4. Candidates"]
    assert "[reply not parsed: Expecting ',' delimiter]" in text["4. Candidates"]
    assert text["4. Candidates"].endswith("3 candidates, 1 refused by the allow-list, 1 not parsed")
    assert "kubectl scale" in text["5. Chosen action or escalation"]
    assert "RECOVERED" in text["6. Outcome"] and "n = 2, c = 1" in text["6. Outcome"]

    # a plan refused by the allow-list shows its reason once, without the prefix
    refused = labels | {"plan": {"command": "kubectl x", "rejection": "allow-list: FLAG_DENIED"}}
    chosen = dict(replay.stages(MANIFEST, transcript, refused, {}))[
        "5. Chosen action or escalation"
    ]
    assert "allow-list: FLAG_DENIED (stop: final)" in chosen
    assert "allow-list: allow-list" not in chosen

    # a run of the fine-tuned arm names its fold and the served model file
    ft = MANIFEST | {
        "sampling": {"baseline": "3cft", "fold": 2},
        "model_build": {"server": "llama-server", "model_file_name": "planner-fold2.gguf"},
    }
    fault = dict(replay.stages(ft, transcript, labels, {}))["1. Injected fault"]
    assert "fold 2" in fault and "served by llama-server, model planner-fold2.gguf" in fault


def test_escalation_from_a_result_row():
    result = {"command": None, "stop": "escalate", "escalation": "unsure", "outcome": "NOT_SENT"}
    text = dict(replay.stages(MANIFEST, {"diagnosis": None, "stop": "escalate"}, {}, result))
    assert "escalated to a human" in text["5. Chosen action or escalation"]
    assert "unsure" in text["5. Chosen action or escalation"]
    assert "NOT_SENT" in text["6. Outcome"]

    # a turn cap and a reply that held no command are not escalations
    capped = {"command": None, "stop": "turn_cap", "outcome": "NOT_SENT"}
    chosen = dict(replay.stages(MANIFEST, {"stop": "turn_cap"}, {}, capped))
    assert (
        chosen["5. Chosen action or escalation"] == "no command, nothing applied (stop: turn_cap)"
    )
    labels = {"plan": {"command": None, "rejection": "Expecting ',' delimiter"}}
    chosen = dict(replay.stages(MANIFEST, {"stop": "final"}, labels, {}))
    assert "escalated" not in chosen["5. Chosen action or escalation"]
    assert "reply not parsed: Expecting ',' delimiter" in chosen["5. Chosen action or escalation"]


def test_admission_comes_from_the_step_that_sent_the_command():
    # the planner's own check refused an "&&" chain that the runner then admitted and executed
    transcript = {"stop": "final", "verdict": {"admitted": False, "reason": "SHELL_METACHAR"}}
    result = {"command": "a && b", "stop": "final", "allow_list": "admitted"}
    chosen = dict(replay.stages(MANIFEST, transcript, {}, result))["5. Chosen action or escalation"]
    assert "allow-list: admitted (stop: final)" in chosen
    labels = {"plan": {"command": "a && b", "rejection": None}}
    chosen = dict(replay.stages(MANIFEST, transcript, labels, {}))["5. Chosen action or escalation"]
    assert "allow-list: admitted (stop: final)" in chosen
    # with neither file, the transcript verdict is all there is
    alone = transcript | {"command": "a && b"}
    chosen = dict(replay.stages(MANIFEST, alone, {}, {}))["5. Chosen action or escalation"]
    assert "allow-list: SHELL_METACHAR" in chosen


def test_cut_of_a_batch_recording(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(MANIFEST))
    (tmp_path / "a.transcript.json").write_text("{}")
    (tmp_path / "b.json").write_text(json.dumps(MANIFEST | {"timestamp": "2026-11-10T14:00:20Z"}))
    start, end, ender = replay.window(tmp_path / "a.json")
    assert end - start == 10 and ender == "b"
    assert replay.window(tmp_path / "b.json")[1:] == (float("inf"), None)

    # the capture starts at 14:00:00; version 3 times are intervals: events at 5, 12, 15 and 25 s
    cast = tmp_path / "batch.cast"
    events = [[5.0, "o", "before"], [7.0, "o", "one"], [3.0, "o", "two"], [10.0, "o", "after"]]
    header = {"version": 3, "timestamp": int(start) - 10}
    cast.write_text("\n".join(json.dumps(x) for x in [header, *events]) + "\n")
    assert replay.slice_cast(cast, start, end, tmp_path / "a.cast") == 2
    lines = [json.loads(x) for x in (tmp_path / "a.cast").read_text().splitlines()]
    assert lines[0]["timestamp"] == int(start)
    assert lines[1:] == [[2.0, "o", "one"], [3.0, "o", "two"]]

    pods = tmp_path / "batch.pods.log"
    pods.write_text(
        "2026-11-10T14:00:05Z ADDED a\n2026-11-10T14:00:12Z MODIFIED b\n"
        "not a stamp\n2026-11-10T14:00:20Z DELETED c\n"
    )
    assert replay.slice_pods(pods, start, end, tmp_path / "a.pods.log") == 1
    assert (tmp_path / "a.pods.log").read_text() == "2026-11-10T14:00:12Z MODIFIED b\n"

    # a pod watch that ends before the run covers none of it: no file is written
    pods.write_text("2026-11-10T14:00:05Z ADDED a\n")
    assert replay.slice_pods(pods, start, end, tmp_path / "none.pods.log") == 0
    assert not (tmp_path / "none.pods.log").exists()


def test_cut_of_a_version_2_recording(tmp_path):
    # version 2 times are seconds since the start of the capture
    cast = tmp_path / "batch.cast"
    header = {"version": 2, "timestamp": 1000, "duration": 30.0}
    events = [[5.0, "o", "before"], [12.0, "o", "one"], [15.0, "o", "two"], [25.0, "o", "after"]]
    cast.write_text("\n".join(json.dumps(x) for x in [header, *events]) + "\n")
    assert replay.slice_cast(cast, 1010, 1020, tmp_path / "a.cast") == 2
    lines = [json.loads(x) for x in (tmp_path / "a.cast").read_text().splitlines()]
    assert lines[0] == {"version": 2, "timestamp": 1010}
    assert lines[1:] == [[2.0, "o", "one"], [5.0, "o", "two"]]
