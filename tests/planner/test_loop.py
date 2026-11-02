import json

import pytest

from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy
from remvalgate.harness.manifest import Registry, Slice
from remvalgate.harness.toolcall import DEFAULT_SCHEMAS_PATH, load_schemas
from remvalgate.overlay.fixes import tool_call
from remvalgate.planner.chat import Completion
from remvalgate.planner.loop import FAULT_TYPES, Stop, plan, sample, task_text
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
        self.seen, self.seeds, self.logprobs = [], [], []

    def complete(self, messages, seed=None, logprobs=False):
        self.seen.append(list(messages))
        self.seeds.append(seed)
        self.logprobs.append(logprobs)
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
    pending, _, chat = run([final()], observation="pod Pending, node selector names a missing node")
    assert scaled.retrieved[0] == ("stub-002", 1)
    assert pending.retrieved[0] == ("stub-003", 1)
    assert chat.seen[0][0]["content"] == pending.system_prompt


@pytest.mark.parametrize("suffix", ["\n}", "\n" + tool("get_logs", namespace=NS, service="x")])
def test_only_the_first_json_object_of_a_reply_is_read(suffix):
    reply = tool("get_logs", namespace=NS, service="media-service") + suffix
    result, tools, _ = run([reply, final()])
    assert tools.calls == [("get_logs", {"namespace": NS, "service": "media-service"})]
    assert result.stop is Stop.FINAL


def test_text_before_the_json_object_is_skipped():
    result, tools, _ = run(
        ["Checking the logs.\n" + tool("get_logs", namespace=NS, service="s"), final()]
    )
    assert len(tools.calls) == 1


@pytest.mark.parametrize("name", ["get_metrics", "get_traces"])
def test_the_telemetry_tools_run_in_the_target_namespace_only(name):
    result, tools, _ = run(
        [tool(name, namespace="default"), tool(name, namespace=NS, duration=5), final()]
    )
    assert tools.calls == [(name, {"namespace": NS, "duration": 5})]
    assert f"{name} works in namespace {NS} only" in result.steps[0].observation


def test_a_tool_without_help_text_or_schema_fails_before_any_model_call():
    chat = FakeChat([final()])
    with pytest.raises(ValueError, match="read_traces"):
        plan(
            "t",
            "o",
            DEV,
            NS,
            chat=chat,
            run_tool=FakeTools(),
            retriever=RETRIEVER,
            policy=POLICY,
            schemas=SCHEMAS,
            tools=frozenset({"exec_shell", "read_traces"}),
        )
    assert chat.seen == []


def test_the_whole_default_tool_set_builds_a_prompt():
    result, _, chat = run([final()])
    system = chat.seen[0][0]["content"]
    assert all(name in system for name in ("exec_shell", "get_logs", "get_metrics", "get_traces"))
    assert result.system_prompt == system


def test_the_recorded_observation_is_what_the_model_saw_including_the_last_turn_note():
    replies = [tool("get_logs", namespace=NS, service="media-service")] * 2
    result, _, chat = run(replies, max_turns=2)
    assert result.steps[0].observation == chat.seen[1][-1]["content"]
    assert "last turn" in result.steps[0].observation


def test_the_finish_reason_of_each_turn_is_recorded():
    class Cut(FakeChat):
        def complete(self, messages, seed=None, logprobs=False):
            done = super().complete(messages, seed, logprobs)
            return Completion(done.text, done.completion_tokens, done.seconds, "length")

    chat = Cut([final()])
    result = plan(
        "t",
        "o",
        DEV,
        NS,
        chat=chat,
        run_tool=FakeTools(),
        retriever=RETRIEVER,
        policy=POLICY,
        schemas=SCHEMAS,
    )
    assert result.steps[0].finish_reason == "length"


def test_a_call_the_harness_cannot_run_is_not_reported_as_a_malformed_turn():
    # a command that ends in a double quote passes the allow-list but not the action parser
    quoted = f'kubectl -n {NS} get pods -l "app=media-service"'
    result, _, chat = run([tool("exec_shell", command=quoted), final()], fake=tool_call)
    seen = chat.seen[1][-1]["content"]
    assert "could not run this call" in seen and "Reply with one JSON object" not in seen
    assert result.stop is Stop.FINAL


@pytest.mark.parametrize(
    "command",
    ['kubectl -n NS get pods -l "app', "kubectl -n NS get pods \\", "", "kubectl --bogus get pods"],
)
def test_a_malformed_diagnosis_command_is_refused_and_never_ends_the_run(command):
    command = command.replace("NS", NS)
    result, tools, _ = run([tool("exec_shell", command=command), final()])
    assert result.stop is Stop.FINAL and tools.calls == []
    assert result.steps[0].observation.startswith("Refused")


def command(text):
    return json.dumps({"command": text})


def draw(replies, k=None, seed=5, result=None):
    result = result or run([tool("exec_shell", command=f"kubectl -n {NS} get pods"), final()])[0]
    chat = FakeChat(replies)
    k = len(replies) if k is None else k
    return sample(result, NS, chat=chat, policy=POLICY, k=k, seed=seed), chat


def test_k_candidates_are_sampled_from_one_diagnosis_with_distinct_recorded_seeds():
    candidates, chat = draw([command(FIX)] * 3, seed=5)
    assert len(candidates) == 3
    assert chat.seeds == [5, 6, 7] and [c.seed for c in candidates] == [5, 6, 7]
    assert chat.logprobs == [True] * 3
    assert all(c.command == FIX and c.verdict.admitted and c.rejection is None for c in candidates)
    # every draw sees the same messages, so only the seed differs
    assert all(seen == chat.seen[0] for seen in chat.seen)


def test_the_draws_see_the_diagnosis_but_not_its_command():
    result, _, plan_chat = run([tool("exec_shell", command=f"kubectl -n {NS} get pods"), final()])
    # the diagnosis loop keeps the client's seed and asks for no logprobs
    assert plan_chat.seeds == [None, None] and plan_chat.logprobs == [False, False]
    _, chat = draw([command(FIX)], result=result)
    messages = chat.seen[0]
    assert messages[: len(result.context)] == list(result.context)
    assert result.context == tuple(plan_chat.seen[-1])
    answer = json.loads(messages[-2]["content"])["final"]
    assert messages[-2]["role"] == "assistant" and "command" not in answer
    assert answer["fault_type"] == "ScalePod" and answer["service"] == "media-service"
    assert FIX not in json.dumps(messages) and f"kubectl -n {NS}" in messages[-1]["content"]


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ("no json here", "no JSON object"),
        (json.dumps({"command": " "}), "no non-empty command"),
        (tool("exec_shell", command=f"kubectl -n {NS} get pods"), "no non-empty command"),
        (command("kubectl delete namespace kube-system"), "allow-list: "),
    ],
)
def test_a_candidate_is_rejected_with_a_reason_and_never_raises(reply, reason):
    (candidate,), _ = draw([reply])
    assert reason in candidate.rejection
    assert candidate.verdict is None or not candidate.verdict.admitted


def test_an_inadmissible_candidate_keeps_its_command_and_verdict():
    (candidate,), _ = draw([command("kubectl delete namespace kube-system")])
    assert candidate.command == "kubectl delete namespace kube-system"
    assert candidate.rejection == f"allow-list: {candidate.verdict.reason}"


def test_a_reply_cut_at_the_token_cap_is_rejected_as_cut():
    class Cut(FakeChat):
        def complete(self, messages, seed=None, logprobs=False):
            done = super().complete(messages, seed, logprobs)
            return Completion(done.text, done.completion_tokens, done.seconds, "length", -1.5)

    result = run([final()])[0]
    (candidate,) = sample(result, NS, chat=Cut(['{"command": "kub']), policy=POLICY, k=1, seed=0)
    assert candidate.rejection == "the reply was cut at the token cap"
    assert candidate.finish_reason == "length" and candidate.logprob == -1.5


def test_an_answer_in_the_final_format_still_yields_its_command():
    (candidate,), _ = draw([final()])
    assert candidate.command == FIX and candidate.rejection is None


def test_a_plan_without_a_diagnosis_or_a_k_below_one_is_refused_before_any_model_call():
    capped = run([tool("get_logs", namespace=NS, service="s")], max_turns=1)[0]
    with pytest.raises(ValueError, match="no diagnosis"):
        draw([command(FIX)], result=capped)
    with pytest.raises(ValueError, match="at least 1"):
        draw([], k=0)


def test_the_task_names_the_problems_own_application_and_namespace():
    # the text the development runs used, from AIOpsLab's metadata name for Social Network
    assert task_text("Social Network", "test-social-network") == (
        "Users report errors from the social network application in namespace "
        "test-social-network. Find the faulty service and give one command that fixes it."
    )
    hotel = task_text("Hotel Reservation", "test-hotel-reservation")
    assert "hotel reservation application in namespace test-hotel-reservation" in hotel
    assert "social" not in hotel


def escalate(reason="the evidence does not single out one service"):
    return json.dumps({"escalate": {"reason": reason}})


def test_with_the_flag_off_the_system_prompt_is_unchanged_from_before_baseline_7():
    # sha256 of the prompt before E6-S5, so baselines 3a to 3c and the planner keep their prompt
    import hashlib

    result, _, _ = run([final()])
    digest = hashlib.sha256(result.system_prompt.encode()).hexdigest()
    assert digest == "f29dd9d78018b6f948f3999453567ef01d317bdf0729f14d9df6702d5438d288"
    assert "escalate" not in result.system_prompt


def test_the_planner_told_to_escalate_when_unsure_can_escalate_with_a_reason():
    result, tools, _ = run(
        [tool("exec_shell", command=f"kubectl -n {NS} get pods"), escalate()],
        escalate_when_unsure=True,
    )
    assert "You MUST escalate" in result.system_prompt
    assert result.stop is Stop.ESCALATE
    assert result.escalation == "the evidence does not single out one service"
    assert result.diagnosis is None and result.command is None and result.verdict is None
    assert len(tools.calls) == 1 and result.steps[-1].observation == "escalate"


def test_an_escalation_without_a_reason_is_refused_and_the_loop_goes_on():
    result, _, chat = run([escalate(reason=" "), final()], escalate_when_unsure=True)
    assert result.stop is Stop.FINAL
    assert "escalate needs" in chat.seen[1][-1]["content"]


def test_an_escalation_without_the_flag_is_refused_like_any_unknown_reply():
    result, _, chat = run([escalate(), final()])
    assert result.stop is Stop.FINAL and result.escalation is None
    assert chat.seen[1][-1]["content"].startswith("Refused: unknown tool")


def test_the_last_turn_nudge_names_escalation_only_when_it_is_allowed():
    replies = [tool("get_logs", namespace=NS, service="s")] * 2
    _, _, told = run(replies, max_turns=2, escalate_when_unsure=True)
    _, _, plain = run(replies, max_turns=2)
    assert "final answer or escalate now" in told.seen[1][-1]["content"]
    assert "escalate" not in plain.seen[1][-1]["content"]


def test_an_escalated_plan_cannot_be_sampled_from():
    result = run([escalate()], escalate_when_unsure=True)[0]
    with pytest.raises(ValueError, match="no diagnosis"):
        sample(result, NS, chat=FakeChat([]), policy=POLICY, k=1, seed=0)
