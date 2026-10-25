import json

import pytest

from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, Reason, load_policy


def _write_policy(tmp_path, **changes):
    data = json.loads(DEFAULT_POLICY_PATH.read_text())
    for key, value in changes.items():
        if value is None:
            del data[key]
        else:
            data[key] = value
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data))
    return path


def test_placeholder_policy_loads():
    policy = load_policy(DEFAULT_POLICY_PATH)
    assert policy.binaries == {"kubectl"}
    assert "rollout restart" in policy.verbs
    assert policy.flags["--replicas"] is True


def test_policy_missing_key_raises(tmp_path):
    with pytest.raises(ValueError, match="flags"):
        load_policy(_write_policy(tmp_path, flags=None))


def test_policy_extra_key_raises(tmp_path):
    with pytest.raises(ValueError, match="deny"):
        load_policy(_write_policy(tmp_path, deny=["--all"]))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("binaries", "kubectl"),
        ("resources", ["pods", 1]),
        ("verbs", ["set  image"]),
        ("verbs", ["rollout history deploy"]),
        ("flags", ["-o"]),
        ("flags", {"-o": "false"}),
        ("flags", {"-o": ["wide", 1]}),
    ],
)
def test_policy_wrong_value_raises(tmp_path, key, value):
    with pytest.raises(ValueError, match=key):
        load_policy(_write_policy(tmp_path, **{key: value}))


def test_policy_not_an_object_raises(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text("[]")
    with pytest.raises(ValueError):
        load_policy(path)


def test_policy_flags_read_only():
    with pytest.raises(TypeError):
        load_policy(DEFAULT_POLICY_PATH).flags["--kubeconfig"] = True


def test_reason_set_is_fixed():
    assert [r.value for r in Reason] == [
        "EMPTY",
        "SHELL_METACHAR",
        "PARSE_ERROR",
        "BINARY_NOT_ALLOWED",
        "OUT_OF_SANDBOX",
        "FLAG_DENIED",
        "VERB_NOT_ALLOWED",
        "RESOURCE_NOT_ALLOWED",
    ]
