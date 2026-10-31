"""Chat client for an OpenAI-compatible endpoint, so the host is a configuration choice.

Standard library only: the planner needs one POST and no dependency. The thinking switch is
sent as reasoning_effort "none", the field LM Studio honours at the time of writing. It ignored
chat_template_kwargs and a top-level enable_thinking (checked on 2026-10-30), so another server
may need another field.
"""

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)

Message = Mapping[str, str]


@dataclass(frozen=True)
class Completion:
    text: str
    completion_tokens: int
    seconds: float
    finish_reason: str = ""


class Chat(Protocol):
    def complete(self, messages: Sequence[Message]) -> Completion: ...


@dataclass(frozen=True)
class OpenAIChat:
    model: str
    base_url: str = "http://localhost:1234/v1"
    # on by default, one member's choice with the caveats in docs/compute.md; off sends
    # reasoning_effort none
    thinking: bool = True
    # the Qwen3.5 card's non-thinking settings, used in both modes so the modes differ in one
    # thing, except presence_penalty: the card advises 1.5 against repetition, but repeated JSON
    # keys and tool names are legitimate here
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    seed: int = 0
    # a thinking turn can use most of this before any content appears
    max_tokens: int = 4096
    timeout: float = 300.0

    def request_body(self, messages: Sequence[Message]) -> dict:
        body = {
            "model": self.model,
            "messages": list(messages),
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "seed": self.seed,
            "max_tokens": self.max_tokens,
        }
        if not self.thinking:
            body["reasoning_effort"] = "none"
        return body

    def complete(self, messages: Sequence[Message]) -> Completion:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            json.dumps(self.request_body(messages)).encode(),
            {"Content-Type": "application/json"},
        )
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            # the status line alone does not say why a server refused the request
            raise RuntimeError(f"{error}: {error.read().decode(errors='replace')[:500]}") from error
        seconds = time.perf_counter() - start
        # a server may leave a think block in the content, and it never belongs in the history
        text = _THINK.sub("", data["choices"][0]["message"].get("content") or "").strip()
        finish = data["choices"][0].get("finish_reason") or ""
        return Completion(text, data["usage"]["completion_tokens"], seconds, finish)
