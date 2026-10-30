import json

import pytest

from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy
from remvalgate.harness.manifest import Registry, Slice
from remvalgate.harness.toolcall import DEFAULT_SCHEMAS_PATH, load_schemas
from remvalgate.planner.chat import Completion
from remvalgate.planner.loop import FAULT_TYPES, Stop, plan
from remvalgate.retrieval import RetrieverRefused
from remvalgate.retrieval.stub import DevStubRetriever

NS = "test-social-network"
DEV, POOL = "p-synth-dev", "p-synth-pool"
POLICY = load_policy(DEFAULT_POLICY_PATH)
SCHEMAS = load_schemas(DEFAULT_SCHEMAS_PATH)
RETRIEVER = DevStubRetriever(
    Registry(slices={DEV: Slice.DEVELOPMENT, POOL: Slice.POOL}, placeholder=None)
)
FIX = f"kubectl -n {NS} scale deployment media-service --replicas=1"


def tool(name, **arguments):
    return json.dumps({"tool": name, "arguments": arguments})


def final(**overrides):
    body = {
        "fault_type": "ScalePod",
        "service": "media-service",
        "reason": "zero replicas",
        "command": FIX,
    }
    return json.dumps({"final": body | overrides})


class FakeChat:
    def __init__(self, replies):
        self._replies = iter(replies)
        self.seen = []

    def complete(self, messages):
        self.seen.append(list(messages))
        return Completion(next(self._replies), 10, 0.5)


class FakeTools:
    def __init__(self, output="ok"):
        self.calls, self._output = [], output

    def __call__(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return self._output


def run(replies, fake=None, problem=DEV, observation="media-service has no pods", **kwargs):
    fake = fake or FakeTools()
    chat = FakeChat(replies)
    result = plan(
        "users report errors",
        observation,
        problem,
        NS,
        chat=chat,
        run_tool=fake,
        retriever=RETRIEVER,
        policy=POLICY,
        schemas=SCHEMAS,
        **kwargs,
    )
    return result, fake, chat


def test_diagnoses_with_a_read_only_call_then_returns_one_command():
    result, tools, _ = run([tool("exec_shell", command=f"kubectl -n {NS} get pods"), final()])
    assert result.stop is Stop.FINAL
    assert result.diagnosis.fault_type == "ScalePod" and result.diagnosis.service == "media-service"
    assert result.command == FIX and result.verdict.admitted
    assert tools.calls == [("exec_shell", {"command": f"kubectl -n {NS} get pods"})]
    assert [s.turn for s in result.steps] == [1, 2]


def test_a_write_during_diagnosis_is_refused_and_never_run():
    result, tools, chat = run([tool("exec_shell", command=FIX), final()])
    assert tools.calls == []
    assert "get and describe only" in result.steps[0].observation
    assert result.stop is Stop.FINAL
    assert chat.seen[1][-1]["content"].startswith("Refused")


@pytest.mark.parametrize(
    "reply",
    [
        "not json",
        tool("kubectl_apply", command="x"),
        tool("get_logs", namespace=NS),
        tool("get_logs", namespace=NS, service=3),
        tool("get_logs", namespace="default", service="media-service"),
        json.dumps({"tool": ["list"], "arguments": {}}),
        json.dumps({"final": "done"}),
        final(fault_type="Gremlins"),
        final(command=" "),
    ],
)
def test_a_malformed_turn_is_refused_without_a_tool_call(reply):
    result, tools, _ = run([reply, final()])
    assert tools.calls == []
    assert result.steps[0].observation.startswith("Refused")
    assert result.stop is Stop.FINAL


def test_an_inadmissible_final_command_is_returned_with_its_verdict_and_not_run():
    result, tools, _ = run([final(command="kubectl delete namespace kube-system")])
    assert result.command == "kubectl delete namespace kube-system"
    assert not result.verdict.admitted
    assert tools.calls == []


def test_the_turn_cap_ends_the_loop_with_no_command():
    replies = [tool("get_logs", namespace=NS, service="media-service")] * 3
    result, tools, chat = run(replies, max_turns=3)
    assert result.stop is Stop.TURN_CAP and result.command is None and result.diagnosis is None
    assert len(tools.calls) == 3
    assert "last turn" in chat.seen[2][-1]["content"]
    assert "last turn" not in chat.seen[1][-1]["content"]


def test_long_output_is_cut_and_the_end_of_logs_is_kept():
    tools = FakeTools("a" * 100 + "END")
    result, _, _ = run(
        [tool("get_logs", namespace=NS, service="s"), final()], fake=tools, max_observation_chars=10
    )
    assert result.steps[0].observation.endswith("aaaaaaaEND")
    assert "truncated, 103 characters" in result.steps[0].observation


def test_the_prompt_carries_the_namespace_the_fault_types_and_retrieved_fixes():
    _, _, chat = run([final()])
    system, user = chat.seen[0][0]["content"], chat.seen[0][1]["content"]
    assert NS in system and all(t in system for t in FAULT_TYPES)
    assert "Fix: kubectl" in user and "media-service has no pods" in user


def test_a_problem_outside_development_is_refused_before_any_model_call():
    with pytest.raises(RetrieverRefused):
        run([final()], problem=POOL)


def test_the_tool_set_is_a_parameter():
    result, tools, _ = run(
        [tool("get_logs", namespace=NS, service="s"), final()], tools=frozenset({"exec_shell"})
    )
    assert tools.calls == []
    assert "unknown tool" in result.steps[0].observation


def test_retrieval_follows_the_observation_and_is_recorded():
    scaled, _, _ = run([final()], observation="deployment scaled to zero replicas, no pod")
    pending, _, _ = run([final()], observation="pod Pending, node selector names a missing node")
    assert scaled.retrieved[0] == ("stub-002", 1)
    assert pending.retrieved[0] == ("stub-003", 1)
    assert "Pending" in pending.system_prompt or NS in pending.system_prompt
