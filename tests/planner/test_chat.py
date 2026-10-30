import io
import json

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
    completion = OpenAIChat(model="m").complete([{"role": "user", "content": "hi"}])
    assert sent[0]["reasoning_effort"] == "none" and sent[0]["seed"] == 0
    assert completion.text == '{"a": 1}' and completion.completion_tokens == 7


def test_thinking_on_leaves_reasoning_effort_out(monkeypatch):
    sent = []
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(sent, "x"))
    OpenAIChat(model="m", thinking=True).complete([{"role": "user", "content": "hi"}])
    assert "reasoning_effort" not in sent[0]
