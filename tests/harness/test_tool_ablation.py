from remvalgate.harness.tool_ablation import diagnostics, error_lines, noisiest_services

# synthetic log lines and transcripts only: no pool problem is read


def test_error_lines_counts_lines_with_an_error_word_ignoring_case():
    log = "\n".join(
        [
            "Failed to connect user-mention-service",
            "connect: Connection refused (user-mention-service:9090)",
            "rpc TIMEOUT after 5s",
            "ERROR: auth failed for user admin",
            "got 3 posts",
            "",
        ]
    )
    assert error_lines(log) == 4
    assert error_lines("") == 0


def test_error_lines_counts_zerolog_level_marks_and_auth_messages_through_colour_codes():
    # zerolog's coloured console format, with a made-up service and database
    fatal = (
        "\x1b[90m2026-01-01T00:00:00Z\x1b[0m \x1b[31mFTL\x1b[0m \x1b[1mcmd/demo/db.go:35\x1b[0m"
        "\x1b[36m >\x1b[0m \x1b[1mnot authorized on demo-db to execute command\x1b[0m"
    )
    err = "\x1b[90m2026-01-01T00:00:01Z\x1b[0m \x1b[31mERR\x1b[0m lookup demo-cache"
    info = "\x1b[90m2026-01-01T00:00:02Z\x1b[0m \x1b[32mINF\x1b[0m Initializing DB connection..."
    assert error_lines("\n".join([fatal, err, info])) == 2
    for line in ("panic: runtime error", "permission denied", "401 Unauthorized", "fatal: x"):
        assert error_lines(line) == 1
    # a word that only contains ERR or FTL is not a level mark
    assert error_lines("TERRA FTLX ready") == 0


def test_noisiest_services_ranks_by_error_lines_and_leaves_out_quiet_ones():
    counts = {"user-service": 2, "text-service": 9, "geo": 0, "compose-post-service": 2}
    # ties by name; a Deployment with no error line is never chosen
    assert noisiest_services(counts) == ["text-service", "compose-post-service", "user-service"]
    assert noisiest_services(counts, limit=1) == ["text-service"]
    assert noisiest_services({"geo": 0, "rate": 0}) == []
    assert noisiest_services({}) == []


def test_noisiest_services_keeps_at_most_the_cap():
    counts = {f"svc-{c}": 1 for c in "edcba"}
    assert noisiest_services(counts) == ["svc-a", "svc-b", "svc-c"]


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
