"""The planner loop: retrieve once, diagnose with read-only tools, return one candidate command.

The model emits one JSON object per turn, either a tool call or a final answer. A tool call
runs through run_tool, which the caller binds to the agent-cloud interface. exec_shell is read
only during diagnosis (get and describe, admitted by the allow-list), so no candidate reaches the
cluster before the gate sees it. The final command is checked by the allow-list and returned,
never run. Sampling K candidates is #72, so one command is returned here.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from remvalgate.harness.allowlist import (
    Policy,
    Verdict,
    check,
    parse_args,
    split_verb,
    tokenize,
)
from remvalgate.harness.toolcall import Schemas
from remvalgate.planner.chat import Chat
from remvalgate.retrieval import Retriever

# the fault types of the pool, as the retrieval corpus names them
# the fault types of the pool with the one-line descriptions of the AIOpsLab paper, Table 2
FAULT_TYPES = {
    "TargetPortMisconfig": "a service targetPort does not match the container port",
    "ScalePod": "a deployment was scaled to zero pods",
    "AssignNonExistentNode": "a pod stays Pending, assigned to a node that does not exist",
    "RevokeAuth": "revoked authentication causes database connection failures",
    "UserUnregistered": "the database user was unregistered, so access fails",
}
READ_ONLY_VERBS = frozenset({"get", "describe"})
DEFAULT_TOOLS = frozenset({"exec_shell", "get_logs"})

_TOOL_HELP = {
    "exec_shell": (
        '{"tool": "exec_shell", "arguments": {"command": "kubectl -n NS get pods"}}: '
        "read only here, so kubectl get or describe only, with -n NS, no pipes or shell syntax "
        "and no --context flag"
    ),
    "get_logs": '{"tool": "get_logs", "arguments": {"namespace": "NS", "service": "SERVICE"}}',
}


class Stop(StrEnum):
    FINAL = "final"
    TURN_CAP = "turn_cap"


@dataclass(frozen=True)
class Diagnosis:
    fault_type: str
    service: str
    reason: str


@dataclass(frozen=True)
class Step:
    turn: int
    reply: str
    tool: str | None
    arguments: Mapping[str, object] | None
    # the tool output, or the reason the turn was refused, as the model saw it
    observation: str
    completion_tokens: int
    seconds: float


@dataclass(frozen=True)
class Plan:
    diagnosis: Diagnosis | None
    command: str | None
    # the allow-list verdict on the command; an inadmissible command is still returned
    verdict: Verdict | None
    stop: Stop
    steps: tuple[Step, ...]
    # the retrieved entries as (entry_id, rank), so a run can be audited
    retrieved: tuple[tuple[str, int], ...]
    system_prompt: str


RunTool = Callable[[str, Mapping[str, object]], str]


def _system_prompt(namespace: str, tools: frozenset[str], max_turns: int) -> str:
    tool_lines = "\n".join(f"- {_TOOL_HELP[t].replace('NS', namespace)}" for t in sorted(tools))
    fault_lines = "\n".join(f"- {name}: {text}" for name, text in FAULT_TYPES.items())
    return (
        f"You are an on-call engineer diagnosing a fault in the Kubernetes namespace {namespace}.\n"
        "Each turn, reply with exactly one JSON object and nothing else.\n"
        f"Tools:\n{tool_lines}\n"
        f"Fault types:\n{fault_lines}\n"
        "Start with kubectl get deployments and pods to find a workload that is missing, not "
        "ready or Pending, then describe it. Logs are noisy and slow, so read them last, for one "
        "service at a time.\n"
        "To finish, reply with "
        '{"final": {"fault_type": F, "service": S, "reason": R, "command": C}} where F is one of '
        "the fault types above, S is the exact name of the faulty service, R is one sentence, "
        f"and C is one kubectl command that fixes the fault and starts with kubectl -n {namespace}."
        " C is not run during diagnosis.\n"
        f"You have at most {max_turns} turns. Gather evidence first, then finish."
    )


def _task_prompt(task: str, observation: str, past: str) -> str:
    return f"{task}\n\nCluster state:\n{observation}\n\nPast fixes that may be similar:\n{past}"


def _clip(text: str, tool: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    note = f"[truncated, {len(text)} characters]"
    # the end of a log is the part that matters, the start of a listing is
    return f"{note}\n{text[-limit:]}" if tool == "get_logs" else f"{text[:limit]}\n{note}"


def _json_object(reply: str) -> dict:
    start = reply.find("{")
    if start < 0:
        raise ValueError("no JSON object in the reply")
    # the first object only: a model sometimes adds a stray closing brace after it
    obj, _ = json.JSONDecoder().raw_decode(reply[start:])
    if not isinstance(obj, dict):
        raise ValueError("the reply is not a JSON object")
    return obj


def _tool_error(
    tool: object, arguments: object, tools: frozenset[str], schemas: Schemas
) -> str | None:
    if not isinstance(tool, str) or tool not in tools:
        return f"unknown tool {tool!r}; use one of {sorted(tools)}"
    if not isinstance(arguments, dict):
        return "arguments must be a JSON object"
    allowed, required = schemas.tools[tool], schemas.required[tool]
    if not required <= arguments.keys() or not arguments.keys() <= allowed.keys():
        return f"{tool} takes {sorted(allowed)} and needs {sorted(required)}"
    if any(type(v) is not allowed[k] for k, v in arguments.items()):
        return f"wrong argument types for {tool}"
    return None


def _read_only_error(command: str, policy: Policy, schemas: Schemas, namespace: str) -> str | None:
    verdict = check(command, policy, namespace)
    if not verdict.admitted:
        return f"command rejected: {verdict.reason}"
    argv = tokenize(command)
    verb = split_verb(parse_args(argv[1:], policy, schemas.value_flags).positionals, policy)
    if verb is None or verb[0] not in READ_ONLY_VERBS:
        return "diagnosis allows kubectl get and describe only; give a fix in the final answer"
    return None


def _final(obj: dict) -> tuple[Diagnosis, str] | str:
    final = obj["final"]
    if not isinstance(final, dict):
        return "final must be a JSON object"
    fields = ("fault_type", "service", "reason", "command")
    if not all(isinstance(final.get(f), str) and final[f].strip() for f in fields):
        return f"final needs non-empty strings for {list(fields)}"
    if final["fault_type"] not in FAULT_TYPES:
        return f"fault_type must be one of {list(FAULT_TYPES)}"
    diagnosis = Diagnosis(final["fault_type"], final["service"], final["reason"])
    return diagnosis, final["command"].strip()


def plan(
    task: str,
    observation: str,
    problem_id: str,
    namespace: str,
    *,
    chat: Chat,
    run_tool: RunTool,
    retriever: Retriever,
    policy: Policy,
    schemas: Schemas,
    tools: frozenset[str] = DEFAULT_TOOLS,
    max_turns: int = 8,
    k: int = 3,
    max_observation_chars: int = 4000,
) -> Plan:
    """Run the loop. Raises RetrieverRefused for a problem the retriever does not serve.

    observation is one read-only look at the cluster after the injection, taken by the caller.
    The model is shown it, and it is the retrieval query, so retrieval depends on the problem.
    """
    hits = retriever.retrieve(observation, problem_id, k)
    past = "\n".join(
        f"- {h.entry.fault_type} on {h.entry.target_service}: {h.entry.summary}. "
        f"Fix: {h.entry.fix_command}"
        for h in hits
    )
    retrieved = tuple((h.entry.entry_id, h.rank) for h in hits)
    system = _system_prompt(namespace, tools, max_turns)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _task_prompt(task, observation, past)},
    ]
    steps: list[Step] = []
    for turn in range(1, max_turns + 1):
        completion = chat.complete(messages)
        messages.append({"role": "assistant", "content": completion.text})
        tool = arguments = None
        try:
            obj = _json_object(completion.text)
            if "final" in obj:
                result = _final(obj)
                if not isinstance(result, str):
                    diagnosis, command = result
                    steps.append(
                        Step(turn, completion.text, None, None, "final", *_cost(completion))
                    )
                    verdict = check(command, policy, namespace)
                    return Plan(
                        diagnosis, command, verdict, Stop.FINAL, tuple(steps), retrieved, system
                    )
                observation = f"Refused: {result}"
            else:
                tool, arguments = obj.get("tool"), obj.get("arguments")
                observation = _tool_error(tool, arguments, tools, schemas)
                if observation is None and tool == "exec_shell":
                    observation = _read_only_error(arguments["command"], policy, schemas, namespace)
                if observation is None and tool == "get_logs":
                    if arguments["namespace"] != namespace:
                        observation = f"get_logs works in namespace {namespace} only"
                if observation is None:
                    observation = _clip(run_tool(tool, arguments), tool, max_observation_chars)
                else:
                    observation = f"Refused: {observation}"
        except ValueError as error:
            observation = f"Refused: {error}. Reply with one JSON object."
        steps.append(Step(turn, completion.text, tool, arguments, observation, *_cost(completion)))
        if turn == max_turns - 1:
            observation += "\nThis is your last turn. Reply with the final answer now."
        messages.append({"role": "user", "content": observation})
    return Plan(None, None, None, Stop.TURN_CAP, tuple(steps), retrieved, system)


def _cost(completion) -> tuple[int, float]:
    return completion.completion_tokens, completion.seconds
