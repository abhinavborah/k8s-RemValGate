import json
import re

import pytest

from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy
from remvalgate.harness.manifest import Registry, Slice
from remvalgate.harness.toolcall import DEFAULT_SCHEMAS_PATH, load_schemas
from remvalgate.overlay.fixes import tool_call
from remvalgate.planner.chat import Completion
from remvalgate.planner.loop import (
    DEFAULT_TOOLS,
    FAULT_TYPES,
    TELEMETRY_TOOLS,
    Stop,
    _base_prompt,
    _system_prompt,
    draw_record,
    plan,
    resample_once,
    sample,
    task_text,
)
from remvalgate.retrieval import RetrieverRefused
from remvalgate.retrieval.configs import NoRetrieval
from remvalgate.retrieval.stub import STUB_CORPUS, DevStubRetriever

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


def run(
    replies,
    fake=None,
    problem=DEV,
    observation="media-service has no pods",
    retriever=RETRIEVER,
    **kwargs,
):
    fake = fake or FakeTools()
    chat = FakeChat(replies)
    result = plan(
        "users report errors",
        observation,
        problem,
        NS,
        chat=chat,
        run_tool=fake,
        retriever=retriever,
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


def test_the_fault_type_descriptions_state_what_each_injector_changes():
    # each text is checked against the pinned AIOpsLab injector named above FAULT_TYPES (#162)
    assert FAULT_TYPES["TargetPortMisconfig"].startswith("a Service's targetPort was changed")
    assert "scaled to zero replicas" in FAULT_TYPES["ScalePod"]
    assert "nodeSelector for a node that does not exist" in FAULT_TYPES["AssignNonExistentNode"]
    # revokeRolesFromUser: the login works and the queries are refused
    assert "lost its readWrite role" in FAULT_TYPES["RevokeAuth"]
    assert "can still log in" in FAULT_TYPES["RevokeAuth"]
    # dropUser: the login itself fails
    assert "was deleted" in FAULT_TYPES["UserUnregistered"]
    assert "fails authentication" in FAULT_TYPES["UserUnregistered"]
    # no service, database, port or node of a pool problem is named, as a whole word
    names = (
        "mongodb-geo mongodb-rate geo rate geo-db rate-db user-service text-service "
        "post-storage-service compose-post-service home-timeline-service user-timeline-service "
        "nginx-thrift media-service 9090 9999 extra-node"
    ).split()
    for text in FAULT_TYPES.values():
        assert not [n for n in names if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text)]
    # each effect clause separates its type: only TargetPortMisconfig keeps its pods Running
    assert "stay Running" in FAULT_TYPES["TargetPortMisconfig"]
    assert "refused" not in FAULT_TYPES["TargetPortMisconfig"]


def test_the_task_prompt_lists_the_retrieved_fixes_after_the_cluster_state():
    result, _, chat = run([final()])
    past = "\n".join(
        f"- {e.fault_type} on {e.target_service}: {e.summary}. Fix: {e.fix_command}"
        for e in (h.entry for h in RETRIEVER.retrieve("media-service has no pods", DEV, 3))
    )
    assert chat.seen[0][1]["content"] == (
        "users report errors\n\nCluster state:\nmedia-service has no pods\n\n"
        f"Past fixes that may be similar:\n{past}"
    )
    assert len(result.retrieved) == 3


def test_with_no_retrieval_the_task_prompt_has_no_past_fixes_section():
    none = NoRetrieval({DEV: -1}, {-1: STUB_CORPUS})
    result, _, chat = run([final()], retriever=none)
    assert chat.seen[0][1]["content"] == (
        "users report errors\n\nCluster state:\nmedia-service has no pods"
    )
    assert result.retrieved == ()


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


def test_each_turn_records_the_prompt_tokens_the_server_counted():
    class Counted(FakeChat):
        def complete(self, messages, seed=None, logprobs=False):
            done = super().complete(messages, seed, logprobs)
            return Completion(done.text, 10, 0.5, prompt_tokens=100 * len(messages))

    chat = Counted([tool("exec_shell", command=f"kubectl -n {NS} get pods"), final()])
    result = plan(
        "users report errors",
        "media-service has no pods",
        DEV,
        NS,
        chat=chat,
        run_tool=FakeTools(),
        retriever=RETRIEVER,
        policy=POLICY,
        schemas=SCHEMAS,
    )
    # the first request holds the system and task prompts, the second two more messages
    assert [s.prompt_tokens for s in result.steps] == [200, 400]
    assert run([final()])[0].steps[0].prompt_tokens is None


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


PATCH = (
    f"kubectl -n {NS} patch deployment unique-id-service --type=json "
    """-p '[{"op": "remove", "path": "/spec/template/spec/nodeSelector"}]'"""
)


# AC: the stray brace sampled JSON patch commands end with is stripped and recorded (#146)
def test_one_stray_brace_after_the_closing_quote_is_stripped_and_the_raw_command_kept():
    reply = command(PATCH + "}")
    (candidate,), _ = draw([reply])
    assert candidate.command == PATCH and candidate.raw_command == PATCH + "}"
    assert candidate.reply == reply
    assert candidate.verdict.admitted and candidate.rejection is None


@pytest.mark.parametrize(
    "text",
    [
        FIX,
        PATCH,
        # two braces: only exactly one stray brace is stripped
        PATCH + "}}",
        # a brace that does not follow a closing quote
        f"kubectl -n {NS} get pods}}",
        # the rest has an unbalanced brace
        f"""kubectl -n {NS} patch deployment a --type=json -p '[{{"op": "remove"]'}}""",
        # the rest has an open quote
        f"""kubectl -n {NS} patch deployment a -p "x'}}""",
    ],
)
def test_any_other_command_is_left_as_the_reply_held_it(text):
    (candidate,), _ = draw([command(text)])
    assert candidate.command == text and candidate.raw_command is None


def test_a_stripped_command_the_allow_list_rejects_stays_rejected():
    text = "kubectl delete namespace kube-system -o 'name'"
    (candidate,), _ = draw([command(text + "}")])
    assert candidate.command == text and candidate.raw_command == text + "}"
    assert not candidate.verdict.admitted
    assert candidate.rejection == f"allow-list: {candidate.verdict.reason}"


# AC: the plan's own command gets the same strip, so a system and its baselines are checked one way
def test_the_planners_own_command_is_stripped_the_same_way():
    result = run([final(command=PATCH + "}")])[0]
    assert result.command == PATCH and result.raw_command == PATCH + "}"
    assert result.verdict.admitted
    kept = run([final(command=PATCH)])[0]
    assert kept.command == PATCH and kept.raw_command is None


def test_a_plan_without_a_diagnosis_or_a_k_below_one_is_refused_before_any_model_call():
    capped = run([tool("get_logs", namespace=NS, service="s")], max_turns=1)[0]
    with pytest.raises(ValueError, match="no diagnosis"):
        draw([command(FIX)], result=capped)
    with pytest.raises(ValueError, match="at least 1"):
        draw([], k=0)


def test_the_task_names_the_problems_own_application_and_namespace_and_the_ticket_time():
    # AIOpsLab's metadata name for Social Network; the ticket time is the injection time (#163)
    opened = 1_794_475_589_000_000  # 2026-11-12T09:26:29Z
    assert task_text("Social Network", "test-social-network", opened) == (
        "Users report errors from the social network application in namespace "
        "test-social-network. The ticket opened at 2026-11-12T09:26:29Z. Find the faulty service "
        "and give one command that fixes it."
    )
    hotel = task_text("Hotel Reservation", "test-hotel-reservation", opened)
    assert "hotel reservation application in namespace test-hotel-reservation" in hotel
    assert "social" not in hotel


BAD = command("kubectl delete namespace kube-system")


DIAGNOSED = run([tool("exec_shell", command=f"kubectl -n {NS} get pods"), final()])[0]


def resample(replies, k=2, seed=5):
    chat = FakeChat(replies)
    return resample_once(DIAGNOSED, NS, chat=chat, policy=POLICY, k=k, seed=seed), chat


def test_one_admitted_candidate_means_no_second_draw():
    draws, chat = resample([BAD, command(FIX)])
    assert len(draws.draws) == 1 and chat.seeds == [5, 6]
    assert draws.candidates == draws.draws[0] and not draws.escalated


# AC: every candidate of the first draw rejected, so a second draw is made exactly once
def test_a_fully_rejected_first_draw_is_resampled_once_with_new_seeds():
    draws, chat = resample([BAD, "no json", BAD, command(FIX)])
    assert len(draws.draws) == 2 and chat.seeds == [5, 6, 7, 8]
    assert draws.candidates == draws.draws[1] and not draws.escalated
    # both draws see the same messages: only the seed differs
    assert all(seen == chat.seen[0] for seen in chat.seen)


# AC: if the second draw also fails the case is escalated, with no third draw
def test_a_second_fully_rejected_draw_escalates_without_a_third():
    draws, chat = resample([BAD] * 4 + [command(FIX)] * 2)
    assert len(draws.draws) == 2 and chat.seeds == [5, 6, 7, 8]
    assert draws.escalated and all(c.rejection for d in draws.draws for c in d)


def test_the_record_lists_both_draws_in_order_with_their_count_and_escalation():
    draws, _ = resample([BAD] * 4)
    record = draw_record(DIAGNOSED, draws)
    assert [c["seed"] for c in record["candidates"]] == [5, 6, 7, 8]
    assert [c["draw"] for c in record["candidates"]] == [1, 1, 2, 2]
    assert record["draws"] == 2 and record["escalated"] is True
    one, _ = resample([command(FIX)] * 2)
    assert draw_record(DIAGNOSED, one)["draws"] == 1
    assert draw_record(DIAGNOSED, one)["escalated"] is False


def test_a_plan_with_no_diagnosis_is_recorded_as_escalated_with_nothing_drawn():
    capped = run([tool("get_logs", namespace=NS, service="s")], max_turns=1)[0]
    assert draw_record(capped, None) == {"candidates": [], "draws": 0, "escalated": True}
    # a diagnosis left unsampled on purpose, as baseline 7 does, is not an escalation
    assert draw_record(DIAGNOSED, None)["escalated"] is False


def escalate(reason="the evidence does not single out one service"):
    return json.dumps({"escalate": {"reason": reason}})


# the fault-type texts the runs before #162 used
FAULT_TYPES_BEFORE_162 = {
    "TargetPortMisconfig": "a service targetPort does not match the container port",
    "ScalePod": "a deployment was scaled to zero pods",
    "AssignNonExistentNode": "a pod stays Pending, assigned to a node that does not exist",
    "RevokeAuth": "revoked authentication causes database connection failures",
    "UserUnregistered": "the database user was unregistered, so access fails",
}


def test_with_the_flag_off_the_system_prompt_is_unchanged_from_before_baseline_7():
    # sha256 of the prompt before E6-S5, so baselines 3a to 3c and the planner keep their prompt;
    # updated for the turn cap of 16 (from 8), the fault-type texts of #162 and the telemetry advice
    # removed by #163, the only
    # changes, as the older hashes show
    import hashlib

    result, _, _ = run([final()])
    digest = hashlib.sha256(result.system_prompt.encode()).hexdigest()
    assert digest == "494dab2abe3e69e8b98f64d3d9ba2e46c0a9745f3db778387d6739cfa3fbec9e"
    # the telemetry advice that #163 removed, put back where it stood
    before_163 = result.system_prompt.replace(
        "then describe it.\n",
        "then describe it. Logs, metrics and traces are noisy and slow, so use them after "
        "kubectl, and read logs for one service at a time.\n",
    )
    assert (
        hashlib.sha256(before_163.encode()).hexdigest()
        == "2661acd2927641094ce4b11940457ca467fdc6865a9fb292bcd63f8116f8b94e"
    )
    before_162 = before_163
    for name, text in FAULT_TYPES.items():
        before_162 = before_162.replace(
            f"- {name}: {text}", f"- {name}: {FAULT_TYPES_BEFORE_162[name]}"
        )
    assert (
        hashlib.sha256(before_162.encode()).hexdigest()
        == "ca0c96959d3505bf4c75f69387b14c086391dfa442b04568313947494bffea04"
    )
    old = before_162.replace("at most 16 turns", "at most 8 turns")
    assert (
        hashlib.sha256(old.encode()).hexdigest()
        == "f29dd9d78018b6f948f3999453567ef01d317bdf0729f14d9df6702d5438d288"
    )
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


def test_only_a_namespace_whose_mongodb_enforces_login_gets_the_login_note():
    hotel = _base_prompt("test-hotel-reservation", DEFAULT_TOOLS, 16)
    assert "ROOT_PASSWORD_REF" in hotel and "-n test-hotel-reservation exec deploy/" in hotel
    assert "PASSWORD_REF" not in _base_prompt("test-social-network", DEFAULT_TOOLS, 16)


# the default prompts of the reruns (#165), with the fault-type texts of #162 and no telemetry
# advice (#163); the 3c reference run at f1c10ef had ca0c9695... and 559e3504..., which the hash
# test above traces back to
@pytest.mark.parametrize(
    ("namespace", "digest"),
    [
        ("test-social-network", "494dab2abe3e69e8b98f64d3d9ba2e46c0a9745f3db778387d6739cfa3fbec9e"),
        # this one also carries the MongoDB login note
        (
            "test-hotel-reservation",
            "e2353d400ffe98b3587bc443bf84b64c4efc4b1fa24bdacd0eebd16736c082a5",
        ),
    ],
)
def test_the_default_system_prompts_are_those_of_the_reruns(namespace, digest):
    import hashlib

    prompt = _system_prompt(namespace, DEFAULT_TOOLS, 16)
    assert hashlib.sha256(prompt.encode()).hexdigest() == digest


# the ablation without interactive tools: read-only exec_shell, and the telemetry as a bundle
EXEC_ONLY = frozenset({"exec_shell"})
LOGS = "starting\n" * 4 + "error: connection refused"
METRICS = "media-service: no samples" + "\nuser-service: cpu 0.2" * 3
BUNDLE = [
    ("get_logs", {"namespace": NS, "service": "media-service"}, LOGS),
    ("get_metrics", {"namespace": NS}, METRICS),
]
# the telemetry advice #163 removed from every arm
ADVICE = (
    "Logs, metrics and traces are noisy and slow, so use them after kubectl, and read logs for "
    "one service at a time."
)
NOTE = (
    "Logs, metrics and traces were collected once at injection and are given with the task; no "
    "tool fetches them."
)


# AC: the bundle comes with the task, after the cluster state and before the past fixes, each
# output cut as a tool's output is: the end of a log, the start of anything else
def test_the_bundle_is_given_with_the_task_and_each_output_is_cut_as_a_tools_output_is():
    _, _, chat = run([final()], tools=EXEC_ONLY, bundle=BUNDLE, max_observation_chars=25)
    past = "\n".join(
        f"- {e.fault_type} on {e.target_service}: {e.summary}. Fix: {e.fix_command}"
        for e in (h.entry for h in RETRIEVER.retrieve("media-service has no pods", DEV, 3))
    )
    assert chat.seen[0][1]["content"] == (
        "users report errors\n\nCluster state:\nmedia-service has no pods\n\n"
        "Telemetry collected at injection:\n"
        f"{tool('get_logs', namespace=NS, service='media-service')}\n"
        "[truncated, 61 characters]\nerror: connection refused\n\n"
        f"{tool('get_metrics', namespace=NS)}\n"
        "media-service: no samples\n[truncated, 91 characters]\n\n"
        f"Past fixes that may be similar:\n{past}"
    )


class Recording:
    """RETRIEVER, with every query it is asked recorded."""

    def __init__(self):
        self.queries = []

    def retrieve(self, query, problem_id, k):
        self.queries.append(query)
        return RETRIEVER.retrieve(query, problem_id, k)


# AC: retrieval queries the observation only. The stub ranks this observation the same with the
# bundle text added, so the recorded query is what shows the bundle stayed out of it
def test_the_bundle_is_never_part_of_the_retrieval_query():
    observation = "deployment scaled to zero replicas, no pod"
    pending = [("get_logs", BUNDLE[0][1], "pod Pending, node selector names a missing node")]
    recording = Recording()
    plain, _, _ = run([final()], observation=observation, tools=EXEC_ONLY)
    bundled, _, _ = run(
        [final()], observation=observation, retriever=recording, tools=EXEC_ONLY, bundle=pending
    )
    assert bundled.retrieved == plain.retrieved and bundled.retrieved[0] == ("stub-002", 1)
    assert recording.queries == [observation]


@pytest.mark.parametrize(
    ("tools", "item", "match"),
    [
        (
            EXEC_ONLY,
            ("exec_shell", {"command": f"kubectl -n {NS} get pods"}, "x"),
            "a bundle holds",
        ),
    ],
)
def test_a_wrong_bundle_is_refused_before_retrieval_and_any_model_call(tools, item, match):
    # no reply to give and a problem the retriever refuses, so either step would raise otherwise
    with pytest.raises(ValueError, match=match):
        run([], problem=POOL, tools=tools, bundle=[item])


# AC: with a bundle the model cannot fetch telemetry, and its prompt says so and lists exec_shell
# only; nothing else in the prompt changes
def test_with_a_bundle_a_telemetry_call_is_refused_and_the_prompt_says_none_is_fetched():
    reply = tool("get_logs", namespace=NS, service="media-service")
    bundled, tools, _ = run([reply, final()], tools=EXEC_ONLY, bundle=BUNDLE)
    assert tools.calls == []
    assert bundled.steps[0].observation.startswith("Refused: unknown tool 'get_logs'")
    system = bundled.system_prompt
    assert NOTE in system and ADVICE not in system
    tool_lines = system.split("Tools:\n")[1].split("\nFault types:")[0]
    assert tool_lines.startswith('- {"tool": "exec_shell"') and "\n" not in tool_lines
    assert not any(name in system for name in TELEMETRY_TOOLS)
    plain, _, _ = run([final()], tools=EXEC_ONLY)
    # the bundle arm differs from the same arm without a bundle only by the note (#163)
    assert ADVICE not in plain.system_prompt
    assert system == plain.system_prompt.replace(
        "then describe it.\n", f"then describe it. {NOTE}\n"
    )


def test_a_bundle_can_come_with_the_telemetry_tools_and_the_prompt_says_they_fetch_more():
    # the toolsbundle arm of the 2 by 2 (#38): tools kept, bundle added
    result, _, chat = run([final()], tools=DEFAULT_TOOLS, bundle=BUNDLE)
    system = chat.seen[0][0]["content"]
    assert "the telemetry tools can fetch more" in system
    assert "no tool fetches them" not in system
    _, _, chat = run([final()], tools=EXEC_ONLY, bundle=BUNDLE)
    assert "no tool fetches them" in chat.seen[0][0]["content"]


def test_the_prompts_of_two_arms_differ_only_in_their_tool_lines():
    # no advice on telemetry, so an arm without the telemetry tools gets the same text (#163)
    full = _base_prompt(NS, DEFAULT_TOOLS, 16).splitlines()
    shell = _base_prompt(NS, frozenset({"exec_shell"}), 16).splitlines()
    dropped = [line for line in full if line not in shell]
    assert dropped and all(line.startswith('- {"tool": "get_') for line in dropped)
    # the same shared lines in the same order and number
    assert [line for line in full if line not in dropped] == shell
    assert not any(w in "\n".join(full) for w in ("noisy", "slow", "after kubectl"))


@pytest.mark.parametrize("opened_at", [0, 1_794_475_589, 1_794_475_589_000, -5, True, 1.79e15])
def test_a_ticket_time_not_in_microseconds_is_refused(opened_at):
    with pytest.raises(ValueError, match="microseconds"):
        task_text("Social Network", NS, opened_at)


def test_every_script_passes_the_injection_time_to_task_text():
    # the scripts have no tests of their own, so a call with two arguments would fail only at run
    # time, after its touch (#163)
    import pathlib
    import re

    scripts = pathlib.Path(__file__).resolve().parents[2] / "scripts"
    calls = []
    for path in sorted(scripts.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\btask_text\(", text):
            depth, i = 1, match.end()
            while depth:
                depth += {"(": 1, ")": -1}.get(text[i], 0)
                i += 1
            calls.append((path.name, text[match.end() : i - 1]))
    assert calls
    for name, args in calls:
        parts = [a.strip() for a in args.split(",") if a.strip()]
        assert len(parts) == 3 and parts[2] == "injected_at", (name, args)


def test_retrieval_gets_the_stable_form_and_the_model_the_observation_as_it_is():
    # #164: one place applies the query form, the planner's retrieval
    from remvalgate.retrieval.query_form import retrieval_query

    observation = "\n".join(
        [
            "NAME                                     READY   STATUS    RESTARTS      AGE",
            "pod/media-service-6d5985bd55-wslks       0/1     Pending   3 (37s ago)   5m",
        ]
    )

    class Recording:
        def __init__(self):
            self.queries = []

        def retrieve(self, query, problem_id, k):
            self.queries.append(query)
            return RETRIEVER.retrieve(query, problem_id, k)

    recording = Recording()
    _, _, chat = run([final()], observation=observation, retriever=recording)
    assert recording.queries == [retrieval_query(observation)]
    assert recording.queries[0] != observation and "wslks" not in recording.queries[0]
    # the model sees the observation unchanged
    assert observation in chat.seen[0][1]["content"]
