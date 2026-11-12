import io
import json
import urllib.error

import pytest

from remvalgate.planner.chat import OpenAIChat


def fake_urlopen(captured, content, **choice):
    def urlopen(request, timeout):
        captured.append(json.loads(request.data))
        body = {
            "choices": [{"message": {"content": content}, **choice}],
            "usage": {"completion_tokens": 7},
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
