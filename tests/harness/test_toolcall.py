import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy
from remvalgate.harness.toolcall import (
    DEFAULT_SCHEMAS_PATH,
    ErrorCounts,
    ErrorType,
    Gold,
    Score,
    ToolCall,
    error_counts,
    load_schemas,
    score,
)

SCHEMAS = load_schemas(DEFAULT_SCHEMAS_PATH)
POLICY = load_policy(DEFAULT_POLICY_PATH)


def _schemas_data():
    return json.loads(DEFAULT_SCHEMAS_PATH.read_text())


def _write_schemas(tmp_path, data):
    path = tmp_path / "schemas.json"
    path.write_text(json.dumps(data))
    return path


def _with(**changes):
    data = _schemas_data()
    for key, value in changes.items():
        if value is None:
            del data[key]
        else:
            data[key] = value
    return data


def test_default_schemas_load():
    schemas = load_schemas(DEFAULT_SCHEMAS_PATH)
    assert schemas.tools.keys() == {
        "exec_shell",
        "get_logs",
        "get_metrics",
        "get_traces",
        "read_metrics",
        "read_traces",
    }
    assert schemas.tools["exec_shell"] == {"command": str, "timeout": int}
    assert schemas.required["exec_shell"] == {"command"}
    assert schemas.required["get_metrics"] == {"namespace"}
    assert schemas.tools["get_metrics"]["duration"] is int
    assert schemas.targets["get_logs"] == {"namespace": "namespace", "name": "service"}
    assert "not pinned" in schemas.placeholder
    assert schemas.kinds["deploy"] == "deployment"
    assert schemas.kinds["po"] == "pod"
    assert {"-c", "--context"} <= schemas.value_flags


def test_schemas_missing_key_raises(tmp_path):
    with pytest.raises(ValueError, match="kinds"):
        load_schemas(_write_schemas(tmp_path, _with(kinds=None)))


def test_schemas_without_placeholder_load(tmp_path):
    assert load_schemas(_write_schemas(tmp_path, _with(placeholder=None))).placeholder is None


def test_schemas_extra_key_raises(tmp_path):
    with pytest.raises(ValueError, match="aliases"):
        load_schemas(_write_schemas(tmp_path, _with(aliases={})))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("placeholder", True),
        ("tools", ["exec_shell"]),
        ("tools", {"exec_shell": ["command"]}),
        ("tools", {"exec_shell": {"command": "str"}}),
        ("tools", {"exec_shell": {"required": {"command": "string"}, "optional": {}}}),
        ("tools", {"exec_shell": {"required": {"command": "str"}, "optional": {"command": "str"}}}),
        ("targets", {"get_logs": {"namespace": "ns"}}),
        # a target must be a required argument, or a call could leave it out
        ("targets", {"get_metrics": {"namespace": "namespace", "name": "file"}}),
        ("targets", {"get_logs": {"pod": "service"}}),
        ("targets", {"get_status": {"namespace": "namespace"}}),
        ("targets", {"get_metrics": {"namespace": "duration"}}),
        ("kinds", {"deploy": 1}),
        ("kinds", ["deploy"]),
        # kinds are looked up in lower case, so an alias must be lower case to match
        ("kinds", {"Deploy": "deployment"}),
        ("value_flags", "-c"),
        ("value_flags", ["-c", 1]),
        ("excluded", "submit"),
        ("excluded", ["exec_shell"]),
    ],
)
def test_schemas_wrong_value_raises(tmp_path, key, value):
    with pytest.raises(ValueError, match=key):
        load_schemas(_write_schemas(tmp_path, _with(**{key: value})))


def test_schemas_not_an_object_raises(tmp_path):
    with pytest.raises(ValueError):
        load_schemas(_write_schemas(tmp_path, []))


def test_schemas_read_only():
    with pytest.raises(TypeError):
        SCHEMAS.tools["exec_shell"]["extra"] = str
    with pytest.raises(TypeError):
        SCHEMAS.kinds["x"] = "y"


def test_error_type_set_is_fixed():
    assert [e.value for e in ErrorType] == ["OK", "BAD_SCHEMA", "WRONG_OP", "WRONG_TARGET"]


def _shell(command, **extra):
    return ToolCall("exec_shell", {"command": command, **extra})


# synthetic fixtures only: namespace sandbox-a, names web, api, web-0, mongo-0
SCALE_WEB = Gold("exec_shell", "scale", "sandbox-a", "deployment", "web")
RESTART_WEB = Gold("exec_shell", "rollout restart", "sandbox-a", "deployment", "web")
LOGS_API = Gold("get_logs", namespace="sandbox-a", name="api")
METRICS = Gold("get_metrics", namespace="sandbox-a")

OK_CASES = [
    (_shell("kubectl scale deploy web --replicas=1 -n sandbox-a"), SCALE_WEB),
    (_shell("kubectl -n sandbox-a scale deployments web --replicas 1"), SCALE_WEB),
    (_shell("kubectl rollout restart deploy/web -n sandbox-a"), RESTART_WEB),
    # a gold field left as None is not checked
    (_shell("kubectl -n sandbox-a get pods"), Gold("exec_shell", "get", "sandbox-a", "pod")),
    (
        _shell("kubectl exec mongo-0 -n sandbox-a -- mongo --eval 'db.getUsers()'"),
        Gold("exec_shell", "exec", "sandbox-a", "pod", "mongo-0"),
    ),
    (
        _shell("kubectl exec pod/mongo-0 -n sandbox-a -- mongo --eval 'db.getUsers()'"),
        Gold("exec_shell", "exec", "sandbox-a", "pod", "mongo-0"),
    ),
    # kubectl reads kinds case-insensitively and with an optional API group
    (_shell("kubectl scale Deployment/web --replicas=1 -n sandbox-a"), SCALE_WEB),
    (_shell("kubectl scale deployment.apps web --replicas=1 -n sandbox-a"), SCALE_WEB),
    # flags the policy does not know, but whose values must not be read as targets
    (
        _shell("kubectl exec mongo-0 -c mongo -n sandbox-a -- mongo --eval 'db.getUsers()'"),
        Gold("exec_shell", "exec", "sandbox-a", "pod", "mongo-0"),
    ),
    (_shell("kubectl --context kind -n sandbox-a scale deploy web --replicas=1"), SCALE_WEB),
    # the allow-list rejects --all, but that is its concern, not this scorer's
    (
        _shell("kubectl delete pods --all -n sandbox-a"),
        Gold("exec_shell", "delete", "sandbox-a", "pod"),
    ),
    (ToolCall("get_logs", {"namespace": "sandbox-a", "service": "api"}), LOGS_API),
    (ToolCall("get_metrics", {"namespace": "sandbox-a", "duration": 60}), METRICS),
    # an optional argument may be left out or given
    (ToolCall("get_metrics", {"namespace": "sandbox-a"}), METRICS),
    (_shell("kubectl scale deploy web --replicas=1 -n sandbox-a", timeout=60), SCALE_WEB),
    (ToolCall("read_metrics", {"file_path": "metrics_output/web.csv"}), Gold("read_metrics")),
    # flag values from the kubectl reference must not be read as names
    (
        _shell("kubectl delete pod web --grace-period 0 -n sandbox-a"),
        Gold("exec_shell", "delete", "sandbox-a", "pod", "web"),
    ),
    (
        _shell("kubectl get pod web --field-selector status.phase=Running -n sandbox-a"),
        Gold("exec_shell", "get", "sandbox-a", "pod", "web"),
    ),
    (
        _shell("kubectl rollout undo deploy/web --to-revision 2 -n sandbox-a"),
        Gold("exec_shell", "rollout undo", "sandbox-a", "deployment", "web"),
    ),
]

# AC: a malformed schema is mapped to BAD_SCHEMA
BAD_SCHEMA_CASES = [
    (ToolCall("exec_shell", {}), SCALE_WEB),
    (_shell("kubectl scale deploy web --replicas=1 -n sandbox-a", retries=5), SCALE_WEB),
    (_shell("kubectl scale deploy web --replicas=1 -n sandbox-a", timeout="5"), SCALE_WEB),
    # submit ends the episode and is not scored as a tool call
    (ToolCall("submit", {}), SCALE_WEB),
    (ToolCall("exec_shell", {"command": 1}), SCALE_WEB),
    (ToolCall("get_metrics", {"namespace": "sandbox-a", "duration": "60"}), METRICS),
    # bool is a subclass of int, but not a valid int argument
    (ToolCall("get_metrics", {"namespace": "sandbox-a", "duration": True}), METRICS),
    (ToolCall("run_kubectl", {"command": "kubectl get pods -n sandbox-a"}), SCALE_WEB),
]

# AC: a wrong operation is mapped to WRONG_OP
WRONG_OP_CASES = [
    (_shell("kubectl delete pod web-0 -n sandbox-a"), SCALE_WEB),
    (ToolCall("get_metrics", {"namespace": "sandbox-a", "duration": 60}), LOGS_API),
    (_shell("kubectl rollout undo deploy/web -n sandbox-a"), RESTART_WEB),
    # no operation can be read from a command that does not tokenize or is not kubectl
    (_shell("kubectl scale deploy web --replicas=1 -n sandbox-a; rm x"), SCALE_WEB),
    (_shell(""), SCALE_WEB),
    (_shell("helm rollback web 1 -n sandbox-a"), SCALE_WEB),
]

# AC: a wrong target is mapped to WRONG_TARGET
WRONG_TARGET_CASES = [
    (_shell("kubectl scale deploy api --replicas=1 -n sandbox-a"), SCALE_WEB),
    (_shell("kubectl rollout restart svc/web -n sandbox-a"), RESTART_WEB),
    (_shell("kubectl scale deploy web --replicas=1 -n other"), SCALE_WEB),
    (_shell("kubectl scale deploy web --replicas=1"), SCALE_WEB),
    (_shell("kubectl scale deploy web --replicas=1 -n sandbox-a -n other"), SCALE_WEB),
    (_shell("kubectl scale deploy web --replicas=1 -A"), SCALE_WEB),
    (_shell("kubectl scale deploy web api --replicas=1 -n sandbox-a"), SCALE_WEB),
    # several kinds do not match one gold kind
    (_shell("kubectl get pods,svc -n sandbox-a"), Gold("exec_shell", "get", "sandbox-a", "pod")),
    (
        _shell("kubectl exec deploy/mongo-0 -n sandbox-a -- mongo --eval 'db.getUsers()'"),
        Gold("exec_shell", "exec", "sandbox-a", "pod", "mongo-0"),
    ),
    # an exec that names no pod has no kind
    (
        _shell("kubectl exec -n sandbox-a -- mongosh --eval 'db.getUsers()'"),
        Gold("exec_shell", "exec", "sandbox-a", "pod"),
    ),
    (ToolCall("get_logs", {"namespace": "sandbox-a", "service": "web"}), LOGS_API),
    (ToolCall("get_logs", {"namespace": "other", "service": "api"}), LOGS_API),
]

# several errors at once: BAD_SCHEMA before WRONG_OP before WRONG_TARGET
PRECEDENCE_CASES = [
    (
        ToolCall("get_metrics", {"namespace": "sandbox-a", "duration": 60, "step": 1}),
        LOGS_API,
        ErrorType.BAD_SCHEMA,
    ),
    (
        _shell("kubectl scale deploy api --replicas=1 -n other", retries=5),
        SCALE_WEB,
        ErrorType.BAD_SCHEMA,
    ),
    (_shell("kubectl delete pod api-0 -n other"), SCALE_WEB, ErrorType.WRONG_OP),
]

CASES = [
    *((call, gold, ErrorType.OK) for call, gold in OK_CASES),
    *((call, gold, ErrorType.BAD_SCHEMA) for call, gold in BAD_SCHEMA_CASES),
    *((call, gold, ErrorType.WRONG_OP) for call, gold in WRONG_OP_CASES),
    *((call, gold, ErrorType.WRONG_TARGET) for call, gold in WRONG_TARGET_CASES),
    *PRECEDENCE_CASES,
]


@pytest.mark.parametrize(("call", "gold", "expected"), CASES)
def test_score(call, gold, expected):
    assert score(call, gold, SCHEMAS, POLICY) == Score(call=call, error=expected)


@given(text=st.text(), gold=st.sampled_from([gold for _, gold in OK_CASES]))
def test_score_never_raises(text, gold):
    for command in (text, "kubectl " + text):
        call = _shell(command)
        assert score(call, gold, SCHEMAS, POLICY).call == call


@given(case=st.sampled_from(OK_CASES), data=st.data())
def test_dropped_required_argument_is_bad_schema(case, data):
    call, gold = case
    dropped = data.draw(st.sampled_from(sorted(SCHEMAS.required[call.tool])))
    broken = ToolCall(call.tool, {k: v for k, v in call.arguments.items() if k != dropped})
    assert score(broken, gold, SCHEMAS, POLICY).error is ErrorType.BAD_SCHEMA


def test_error_counts_empty():
    counts = error_counts([])
    assert counts == ErrorCounts(total=0, by_type={error: 0 for error in ErrorType})
    assert counts.accuracy == 0.0


def test_error_counts_mixed():
    # AC: each mapped error type is counted
    rows = [*OK_CASES[:2], *BAD_SCHEMA_CASES[:1], *WRONG_OP_CASES[:3], *WRONG_TARGET_CASES[:2]]
    counts = error_counts(score(call, gold, SCHEMAS, POLICY) for call, gold in rows)
    assert counts.total == 8
    assert counts.by_type == {
        ErrorType.OK: 2,
        ErrorType.BAD_SCHEMA: 1,
        ErrorType.WRONG_OP: 3,
        ErrorType.WRONG_TARGET: 2,
    }
    assert counts.accuracy == 0.25


@given(st.lists(st.sampled_from(CASES)))
def test_counts_sum_and_accuracy_bounds(rows):
    counts = error_counts(score(call, gold, SCHEMAS, POLICY) for call, gold, _ in rows)
    assert counts.total == len(rows)
    assert sum(counts.by_type.values()) == counts.total
    assert counts.by_type[ErrorType.OK] == sum(1 for *_, e in rows if e is ErrorType.OK)
    assert 0.0 <= counts.accuracy <= 1.0
    if counts.total:
        assert counts.accuracy == counts.by_type[ErrorType.OK] / counts.total


@pytest.mark.parametrize(
    "gold",
    [
        Gold("get_log", namespace="sandbox-a"),
        Gold("get_metrics", namespace="sandbox-a", name="web"),
        Gold("get_logs", operation="get"),
        Gold("get_logs", kind="service"),
        # an exec_shell kind is compared in its canonical form, so an alias never matches
        Gold("exec_shell", kind="pods"),
        Gold("exec_shell", kind="Pod"),
        # a two-word operation outside the policy is never read as one operation
        Gold("exec_shell", operation="rollout status"),
    ],
)
def test_gold_the_tool_cannot_match_raises(gold):
    call = ToolCall("get_logs", {"namespace": "sandbox-a", "service": "api"})
    with pytest.raises(ValueError, match="gold"):
        score(call, gold, SCHEMAS, POLICY)


@pytest.mark.parametrize("flag", sorted(SCHEMAS.value_flags))
def test_each_value_flag_keeps_the_target(flag):
    call = _shell(f"kubectl delete pod web {flag} x -n sandbox-a")
    gold = Gold("exec_shell", "delete", "sandbox-a", "pod", "web")
    assert score(call, gold, SCHEMAS, POLICY).error is ErrorType.OK
