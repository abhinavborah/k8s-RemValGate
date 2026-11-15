import io
import json
import urllib.error

import pytest

from remvalgate.planner.chat import OpenAIChat


def fake_urlopen(captured, content, usage=None, **choice):
    def urlopen(request, timeout):
        captured.append(json.loads(request.data))
        body = {
            "choices": [{"message": {"content": content}, **choice}],
            "usage": usage or {"completion_tokens": 7},
        }
        return io.BytesIO(json.dumps(body).encode())

    return urlopen


def test_thinking_off_sends_reasoning_effort_none_and_think_blocks_are_stripped(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "urllib.request.urlopen", fake_urlopen(sent, '<think>\nplan\n</think>\n\n{"a": 1}')
    )
    completion = OpenAIChat(model="m", thinking=False).complete([{"role": "user", "content": "hi"}])
    assert sent[0]["reasoning_effort"] == "none" and sent[0]["seed"] == 0
    assert completion.text == '{"a": 1}' and completion.completion_tokens == 7


def test_the_prompt_tokens_the_server_counted_are_kept_and_none_when_absent(monkeypatch):
    counted = {"completion_tokens": 7, "prompt_tokens": 1234}
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen([], "x", usage=counted))
    assert OpenAIChat(model="m").complete([{"role": "user", "content": "hi"}]).prompt_tokens == 1234
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen([], "x"))
    assert OpenAIChat(model="m").complete([{"role": "user", "content": "hi"}]).prompt_tokens is None


def test_thinking_is_on_by_default_and_leaves_reasoning_effort_out(monkeypatch):
    sent = []
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(sent, "x"))
    OpenAIChat(model="m").complete([{"role": "user", "content": "hi"}])
    assert "reasoning_effort" not in sent[0]


def test_a_refused_request_reports_the_server_text(monkeypatch):
    def refuse(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", {}, io.BytesIO(b"context full")
        )

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    with pytest.raises(RuntimeError, match="context full"):
        OpenAIChat(model="m").complete([{"role": "user", "content": "hi"}])


def test_a_seed_given_to_one_call_overrides_the_client_seed_for_that_call_only(monkeypatch):
    sent = []
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(sent, "x"))
    chat = OpenAIChat(model="m", seed=3)
    chat.complete([{"role": "user", "content": "hi"}], seed=11)
    chat.complete([{"role": "user", "content": "hi"}])
    assert [body["seed"] for body in sent] == [11, 3]


def test_the_log_probability_is_the_sum_over_content_tokens_or_none(monkeypatch):
    sent = []
    tokens = {"content": [{"token": "a", "logprob": -0.5}, {"token": "b", "logprob": -0.25}]}
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(sent, "ab", logprobs=tokens))
    hi = [{"role": "user", "content": "hi"}]
    assert OpenAIChat(model="m").complete(hi, logprobs=True).logprob == -0.75
    assert sent[0]["logprobs"] is True
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(sent, "ab", logprobs=None))
    assert OpenAIChat(model="m").complete(hi, logprobs=True).logprob is None
    # -9999.0 marks a token outside the top 20, whose value is unknown
    unknown = {"content": [{"token": "a", "logprob": -0.5}, {"token": "b", "logprob": -9999.0}]}
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(sent, "ab", logprobs=unknown))
    assert OpenAIChat(model="m").complete(hi, logprobs=True).logprob is None


def test_logprobs_are_asked_for_only_when_a_call_wants_them(monkeypatch):
    sent = []
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(sent, "x"))
    OpenAIChat(model="m").complete([{"role": "user", "content": "hi"}])
    assert "logprobs" not in sent[0]


def test_the_presence_penalty_is_sent_so_the_server_default_never_decides_it(monkeypatch):
    # LM Studio applies the model's hub default (1.5) to a request that leaves it out (#161)
    sent = []
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(sent, "x"))
    OpenAIChat(model="m").complete([{"role": "user", "content": "hi"}])
    assert sent[0]["presence_penalty"] == 1.5
    assert {"temperature", "top_p", "top_k", "presence_penalty"} <= sent[0].keys()


@pytest.mark.parametrize(
    "script", ["run_baselines_pool.py", "run_oracle_labels.py", "run_planner_dev.py"]
)
def test_every_runner_records_the_presence_penalty_with_the_sampling(script):
    # read as a syntax tree: the scripts need an AIOpsLab config file to import
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / script
    names = [
        {e.value for e in n.elts}
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(n, ast.Tuple)
        and n.elts
        and all(isinstance(e, ast.Constant) for e in n.elts)
        and "temperature" in {e.value for e in n.elts}
    ]
    assert names, f"{script} records no sampling settings"
    assert all("presence_penalty" in n for n in names)


def _flaky(failures, sent, content="x"):
    """urlopen that raises each of failures in turn, then answers."""
    good = fake_urlopen(sent, content)
    queue = list(failures)

    def urlopen(request, timeout):
        sent.append(dict(request.header_items()))
        if queue:
            raise queue.pop(0)
        return good(request, timeout)

    return urlopen


def test_a_dropped_connection_or_5xx_is_retried_with_the_same_body(monkeypatch, capsys):
    monkeypatch.setattr("remvalgate.planner.chat.BACKOFF", (0.0, 0.0))
    sent = []
    failures = [
        urllib.error.URLError(ConnectionResetError("reset")),
        urllib.error.HTTPError("u", 524, "timeout", {}, io.BytesIO(b"")),
    ]
    monkeypatch.setattr("urllib.request.urlopen", _flaky(failures, sent, "ok"))
    completion = OpenAIChat(model="m").complete([{"role": "user", "content": "hi"}], seed=3)
    assert completion.text == "ok"
    assert completion.attempts == 3
    bodies = [s for s in sent if "messages" in s]
    assert len(bodies) == 1 and bodies[0]["seed"] == 3
    assert capsys.readouterr().err.count("planner request retry") == 2


def test_a_4xx_is_not_retried(monkeypatch):
    monkeypatch.setattr("remvalgate.planner.chat.BACKOFF", (0.0, 0.0))
    sent = []
    failures = [urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b"bad key"))]
    monkeypatch.setattr("urllib.request.urlopen", _flaky(failures, sent))
    with pytest.raises(RuntimeError, match="bad key"):
        OpenAIChat(model="m").complete([{"role": "user", "content": "hi"}])
    assert len([s for s in sent if "messages" not in s]) == 1


def test_the_last_failure_is_raised_after_all_attempts(monkeypatch):
    monkeypatch.setattr("remvalgate.planner.chat.BACKOFF", (0.0, 0.0))
    failures = [TimeoutError("t")] * 3
    monkeypatch.setattr("urllib.request.urlopen", _flaky(failures, []))
    with pytest.raises(TimeoutError):
        OpenAIChat(model="m").complete([{"role": "user", "content": "hi"}])


def test_the_api_key_is_sent_only_when_set(monkeypatch):
    sent = []
    monkeypatch.setattr("urllib.request.urlopen", _flaky([], sent))
    monkeypatch.delenv("REMVALGATE_PLANNER_API_KEY", raising=False)
    OpenAIChat(model="m").complete([{"role": "user", "content": "hi"}])
    monkeypatch.setenv("REMVALGATE_PLANNER_API_KEY", "k123")
    OpenAIChat(model="m").complete([{"role": "user", "content": "hi"}])
    headers = [s for s in sent if "messages" not in s]
    assert "Authorization" not in headers[0]
    assert headers[1]["Authorization"] == "Bearer k123"
