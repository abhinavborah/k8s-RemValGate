import json

import pytest

from remvalgate.harness.toolcall import (
    DEFAULT_SCHEMAS_PATH,
    ErrorType,
    load_schemas,
)

SCHEMAS = load_schemas(DEFAULT_SCHEMAS_PATH)


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
    assert schemas.tools.keys() == {"exec_shell", "get_logs", "get_metrics", "get_traces"}
    assert schemas.tools["exec_shell"] == {"command": str}
    assert schemas.tools["get_metrics"]["duration"] is int
    assert schemas.targets["get_logs"] == {"namespace": "namespace", "name": "service"}
    assert schemas.kinds["deploy"] == "deployment"
    assert schemas.kinds["po"] == "pod"
    assert {"-c", "--context"} <= schemas.value_flags


def test_schemas_missing_key_raises(tmp_path):
    with pytest.raises(ValueError, match="kinds"):
        load_schemas(_write_schemas(tmp_path, _with(kinds=None)))


def test_schemas_extra_key_raises(tmp_path):
    with pytest.raises(ValueError, match="aliases"):
        load_schemas(_write_schemas(tmp_path, _with(aliases={})))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("placeholder", True),
        ("tools", ["exec_shell"]),
        ("tools", {"exec_shell": ["command"]}),
        ("tools", {"exec_shell": {"command": "string"}}),
        ("targets", {"get_logs": {"namespace": "ns"}}),
        ("targets", {"get_logs": {"pod": "service"}}),
        ("targets", {"get_status": {"namespace": "namespace"}}),
        ("targets", {"get_metrics": {"namespace": "duration"}}),
        ("kinds", {"deploy": 1}),
        ("kinds", ["deploy"]),
        # kinds are looked up in lower case, so an alias must be lower case to match
        ("kinds", {"Deploy": "deployment"}),
        ("value_flags", "-c"),
        ("value_flags", ["-c", 1]),
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
