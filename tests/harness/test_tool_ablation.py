from remvalgate.harness.harm import Reason, Status
from remvalgate.harness.tool_ablation import diagnostics, unready_services

# synthetic workloads and transcripts only: no pool problem is read


def _status(key: str, healthy: bool) -> Status:
    # Status needs a reason and a detail exactly when unhealthy
    if healthy:
        return Status("ns", key, True, None, "")
    return Status("ns", key, False, Reason.NOT_READY, f"{key}: not ready")


def _vector(*entries: tuple[str, bool]) -> dict[str, Status]:
    return {key: _status(key, healthy) for key, healthy in entries}


def test_unready_services_lists_unhealthy_deployments_only_sorted():
    status = _vector(
        ("Deployment/user-service", False),
        ("Deployment/geo", False),
        ("Deployment/text-service", True),
        ("StatefulSet/mongodb", False),
        ("Job/wrk2-job", False),
        ("Pod/debug", False),
    )
    assert unready_services(status) == ["geo", "user-service"]


def test_unready_services_keeps_the_first_names_up_to_the_cap():
    status = _vector(*((f"Deployment/svc-{c}", False) for c in "edcba"))
    assert unready_services(status) == ["svc-a", "svc-b", "svc-c"]
    assert unready_services(status, limit=1) == ["svc-a"]


def test_unready_services_is_empty_when_every_workload_is_healthy():
    assert unready_services(_vector(("Deployment/geo", True), ("Deployment/rate", True))) == []
    assert unready_services({}) == []


def _step(turn: int, tool: object, observation: str, tokens: int, seconds: float) -> dict:
    return {
        "turn": turn,
        "reply": "{}",
        "tool": tool,
        "arguments": None if tool is None else {"namespace": "ns"},
        "observation": observation,
        "completion_tokens": tokens,
        "seconds": seconds,
        "finish_reason": "stop",
    }


def _transcript() -> dict:
    steps = [
        _step(
            1, "get_logs", "Refused: unknown tool 'get_logs'; use one of ['exec_shell']", 40, 0.1
        ),
        _step(2, "exec_shell", "NAME READY STATUS", 60, 0.2),
        _step(3, None, "Refused: no JSON object. Reply with one JSON object.", 30, 0.0),
        # a tool that is not a str is refused and is not counted by name
        _step(4, ["get_logs"], "Refused: unknown tool ['get_logs']", 20, 0.0),
        # a valid call the harness could not run is not a refused turn
        _step(5, "exec_shell", "exec_shell could not run this call: timeout", 50, 0.0),
        _step(
            6,
            "get_traces",
            "Refused: unknown tool 'get_traces'\nThis is your last turn. Reply with the final "
            "answer now.",
            10,
            0.0,
        ),
        _step(7, None, "final", 80, 0.0),
    ]
    return {"steps": steps, "stop": "final", "diagnosis": None}


def test_diagnostics_of_a_run_without_a_bundle():
    out = diagnostics(_transcript())
    assert out == {
        "stop": "final",
        "turns": 7,
        "refused_turns": 4,
        "tool_calls": {"exec_shell": 2, "get_logs": 1, "get_traces": 1},
        "completion_tokens": 290,
        "model_seconds": 0.3,
        # these steps carry no prompt_tokens, as the transcripts of 3c's run do not
        "max_prompt_tokens": None,
        "bundle_parts": 0,
        "bundle_chars": 0,
    }
    # sorted by name, though get_logs was called first
    assert list(out["tool_calls"]) == sorted(out["tool_calls"])


def test_diagnostics_reports_the_largest_prompt_the_server_counted():
    transcript = _transcript()
    for step, prompt in zip(transcript["steps"], [900, None, 4100, 2500], strict=False):
        step["prompt_tokens"] = prompt
    assert diagnostics(transcript)["max_prompt_tokens"] == 4100


def test_diagnostics_counts_the_bundle():
    bundle = [
        {"tool": "get_metrics", "arguments": {"namespace": "ns"}, "output": "cpu 1"},
        {"tool": "get_traces", "arguments": {"namespace": "ns"}, "output": "no spans"},
        {"tool": "get_logs", "arguments": {"namespace": "ns", "service": "geo"}, "output": ""},
    ]
    out = diagnostics(_transcript() | {"stop": "turn_cap", "bundle": bundle})
    assert (out["stop"], out["bundle_parts"], out["bundle_chars"]) == ("turn_cap", 3, 13)
    assert out["tool_calls"] == diagnostics(_transcript())["tool_calls"]
