"""Tool-call and schema validity scoring against a gold record.

The schema file holds placeholder tool signatures until the AIOpsLab commit is pinned.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

DEFAULT_SCHEMAS_PATH = Path(__file__).with_name("toolcall_schemas.json")

_SCHEMAS_KEYS = {"placeholder", "tools", "targets", "kinds", "value_flags"}
_TARGET_KEYS = {"namespace", "name"}
_TYPES = {"str": str, "int": int}


class ErrorType(StrEnum):
    """Scoring outcomes, in the order the checks run. The first failure wins."""

    OK = "OK"
    BAD_SCHEMA = "BAD_SCHEMA"
    WRONG_OP = "WRONG_OP"
    WRONG_TARGET = "WRONG_TARGET"


@dataclass(frozen=True)
class Schemas:
    # tool to argument name to its type
    tools: Mapping[str, Mapping[str, type]]
    # tool to the argument that names its target namespace and object, where it has one
    targets: Mapping[str, Mapping[str, str]]
    # kubectl resource alias to its canonical kind
    kinds: Mapping[str, str]
    # kubectl flags outside the allow-list policy that take a value
    value_flags: frozenset[str]


def _str_map(key: str, value: object) -> dict[str, str]:
    if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
        raise ValueError(f"schemas {key}: expected an object of strings")
    return value


def load_schemas(path: str | Path) -> Schemas:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("schemas: expected a JSON object")
    missing = _SCHEMAS_KEYS - data.keys()
    extra = data.keys() - _SCHEMAS_KEYS
    if missing or extra:
        raise ValueError(f"schemas keys: missing {sorted(missing)}, unexpected {sorted(extra)}")
    if not isinstance(data["placeholder"], str):
        raise ValueError("schemas placeholder: expected a note")
    if not isinstance(data["tools"], dict):
        raise ValueError("schemas tools: expected an object")
    tools = {}
    for tool, arguments in data["tools"].items():
        arguments = _str_map("tools", arguments)
        if not set(arguments.values()) <= _TYPES.keys():
            raise ValueError(f"schemas tools: {tool!r} types must be one of {sorted(_TYPES)}")
        tools[tool] = MappingProxyType({name: _TYPES[t] for name, t in arguments.items()})
    if not isinstance(data["targets"], dict):
        raise ValueError("schemas targets: expected an object")
    targets = {}
    for tool, target in data["targets"].items():
        target = _str_map("targets", target)
        if tool not in tools or not target.keys() <= _TARGET_KEYS:
            raise ValueError(f"schemas targets: {tool!r} needs a tool and {sorted(_TARGET_KEYS)}")
        if any(tools[tool].get(argument) is not str for argument in target.values()):
            raise ValueError(f"schemas targets: {tool!r} must name its string arguments")
        targets[tool] = MappingProxyType(target)
    kinds = _str_map("kinds", data["kinds"])
    if any(alias != alias.lower() for alias in kinds):
        raise ValueError("schemas kinds: aliases must be lower case")
    value_flags = data["value_flags"]
    if not isinstance(value_flags, list) or not all(isinstance(f, str) for f in value_flags):
        raise ValueError("schemas value_flags: expected a list of strings")
    return Schemas(
        tools=MappingProxyType(tools),
        targets=MappingProxyType(targets),
        kinds=MappingProxyType(kinds),
        value_flags=frozenset(value_flags),
    )
