import io
import json
import urllib.error

import pytest

from remvalgate.planner.chat import OpenAIChat


def fake_urlopen(captured, content):
    def urlopen(request, timeout):
        captured.append(json.loads(request.data))
        body = {"choices": [{"message": {"content": content}}], "usage": {"completion_tokens": 7}}
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
