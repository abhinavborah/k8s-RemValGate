"""Chat client for an OpenAI-compatible endpoint, so the host is a configuration choice.

Standard library only: the planner needs one POST and no dependency. The thinking switch is
sent as reasoning_effort "none", the field LM Studio honours at the time of writing. It ignored
chat_template_kwargs and a top-level enable_thinking (checked on 2026-10-30), so another server
may need another field.

A call can ask for logprobs (sampling does, the diagnosis loop does not). A server that returns
them gives the completion the sum of its content tokens' log-probabilities, which ranks
candidates by the planner's own confidence. LM Studio's chat completions page lists no logprobs
parameter (read 2026-10-31), so it likely returns none and the sum is None. The OpenAI reference
gives -9999.0 for a token outside the top 20, a value that is not a log-probability, so a reply
with such a token also gets None. Whether a thinking model's sum covers its thinking was not
checked.
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
    # None when the server returns no logprobs or marks a token's value unknown
    logprob: float | None = None
    # the request's length in tokens as the server counted it, None when it reports none
    prompt_tokens: int | None = None


class Chat(Protocol):
    # seed overrides the client's own seed for one call, so repeated draws can differ
    def complete(
        self, messages: Sequence[Message], seed: int | None = None, logprobs: bool = False
    ) -> Completion: ...


@dataclass(frozen=True)
class OpenAIChat:
    model: str
    base_url: str = "http://localhost:1234/v1"
    # on by default, one member's choice with the caveats in docs/compute.md; off sends
    # reasoning_effort none
    thinking: bool = True
    # the Qwen3.5 card's non-thinking settings, used in both modes so the modes differ in one
    # thing
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    # sent explicitly (#161): the requests used to leave it out, meant as no penalty since repeated
    # JSON keys and tool names are legitimate here. On the run host LM Studio then applied the
    # hub model.yaml default of qwen/qwen3.5-4b, 1.5 (enabled there; one prompt at temperature 0
    # on 2026-11-12 matched 1.5, not 0), so its LM Studio runs ran with 1.5. Every server now gets
    # 1.5: one whose own default is 0, such as llama-server, behaves differently from before.
    presence_penalty: float = 1.5
    seed: int = 0
    # a thinking turn can use most of this before any content appears
    max_tokens: int = 4096
    timeout: float = 300.0

    def request_body(
        self, messages: Sequence[Message], seed: int | None = None, logprobs: bool = False
    ) -> dict:
        body = {
            "model": self.model,
            "messages": list(messages),
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "presence_penalty": self.presence_penalty,
            "seed": self.seed if seed is None else seed,
            "max_tokens": self.max_tokens,
        }
        if logprobs:
            body["logprobs"] = True
        if not self.thinking:
            body["reasoning_effort"] = "none"
        return body

    def complete(
        self, messages: Sequence[Message], seed: int | None = None, logprobs: bool = False
    ) -> Completion:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            json.dumps(self.request_body(messages, seed, logprobs)).encode(),
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
        tokens = (data["choices"][0].get("logprobs") or {}).get("content") or ()
        values = [t["logprob"] for t in tokens]
        # -9999.0 stands for a token outside the top 20, so the sum would be meaningless
        logprob = sum(values) if values and -9999.0 not in values else None
        usage = data["usage"]
        return Completion(
            text, usage["completion_tokens"], seconds, finish, logprob, usage.get("prompt_tokens")
        )
