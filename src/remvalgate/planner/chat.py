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

A server reached over a network (the GPU host of the fine-tuned planner, behind a tunnel) can
drop a connection or answer 5xx. Such a call is retried up to `attempts` times; the request
carries a fixed seed, so a retry asks for the same reply, and each retry is printed. A 4xx is
never retried. When REMVALGATE_PLANNER_API_KEY is set, it is sent as a bearer token; it is read
from the environment only, never stored.
"""

import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
# a dropped or cut connection; a 5xx is an HTTPError and handled apart
_DROPPED = (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError)

API_KEY_ENV = "REMVALGATE_PLANNER_API_KEY"
# seconds to wait before the second and later attempts
BACKOFF = (5.0, 15.0)

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
    attempts: int = 3

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
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(API_KEY_ENV)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        body = json.dumps(self.request_body(messages, seed, logprobs)).encode()
        for attempt in range(1, self.attempts + 1):
            request = urllib.request.Request(f"{self.base_url}/chat/completions", body, headers)
            start = time.perf_counter()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.load(response)
                break
            except urllib.error.HTTPError as error:
                text = error.read().decode(errors="replace")[:500]
                if error.code < 500 or attempt == self.attempts:
                    # the status line alone does not say why a server refused the request
                    raise RuntimeError(f"{error}: {text}") from error
                reason = f"{error.code}"
            except _DROPPED as error:
                if attempt == self.attempts:
                    raise
                reason = type(error).__name__
            wait = BACKOFF[min(attempt, len(BACKOFF)) - 1]
            print(f"planner request retry {attempt}/{self.attempts - 1}: {reason}", file=sys.stderr)
            time.sleep(wait)
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
