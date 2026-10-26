"""Tool-call and schema validity scoring against a gold record.

The schema file holds placeholder tool signatures until the AIOpsLab commit is pinned.
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from remvalgate.harness.allowlist import (
    Policy,
    Rejected,
    parse_args,
    split_targets,
    split_verb,
    tokenize,
)

DEFAULT_SCHEMAS_PATH = Path(__file__).with_name("toolcall_schemas.json")

_SCHEMAS_KEYS = {"tools", "excluded", "targets", "kinds", "value_flags"}
# the placeholder note is optional: it is removed once the signatures are confirmed
_OPTIONAL_KEYS = {"placeholder"}
_TOOL_KEYS = {"required", "optional"}
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
    # tool to argument name to its type, required and optional arguments alike
    tools: Mapping[str, Mapping[str, type]]
    # tool to the arguments a call must give
    required: Mapping[str, frozenset[str]]
    # tool to the argument that names its target namespace and object, where it has one
    targets: Mapping[str, Mapping[str, str]]
    # kubectl resource alias to its canonical kind
    kinds: Mapping[str, str]
    # kubectl flags outside the allow-list policy that take a value
    value_flags: frozenset[str]
    # the note that the signatures are not confirmed yet, so a run can record it
    placeholder: str | None


def _str_map(key: str, value: object) -> dict[str, str]:
    if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
        raise ValueError(f"schemas {key}: expected an object of strings")
    return value


def load_schemas(path: str | Path) -> Schemas:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("schemas: expected a JSON object")
    missing = _SCHEMAS_KEYS - data.keys()
    extra = data.keys() - _SCHEMAS_KEYS - _OPTIONAL_KEYS
    if missing or extra:
        raise ValueError(f"schemas keys: missing {sorted(missing)}, unexpected {sorted(extra)}")
    placeholder = data.get("placeholder")
    if placeholder is not None and not isinstance(placeholder, str):
        raise ValueError("schemas placeholder: expected a note")
    if not isinstance(data["tools"], dict):
        raise ValueError("schemas tools: expected an object")
    tools, required = {}, {}
    for tool, schema in data["tools"].items():
        if not isinstance(schema, dict) or schema.keys() != _TOOL_KEYS:
            raise ValueError(f"schemas tools: {tool!r} needs exactly {sorted(_TOOL_KEYS)}")
        needed = _str_map("tools", schema["required"])
        optional = _str_map("tools", schema["optional"])
        if needed.keys() & optional.keys():
            raise ValueError(f"schemas tools: {tool!r} lists an argument as required and optional")
        arguments = needed | optional
        if not set(arguments.values()) <= _TYPES.keys():
            raise ValueError(f"schemas tools: {tool!r} types must be one of {sorted(_TYPES)}")
        tools[tool] = MappingProxyType({name: _TYPES[t] for name, t in arguments.items()})
        required[tool] = frozenset(needed)
    excluded = data["excluded"]
    if not isinstance(excluded, list) or not all(isinstance(t, str) for t in excluded):
        raise ValueError("schemas excluded: expected a list of tool names")
    if not tools.keys().isdisjoint(excluded):
        raise ValueError("schemas excluded: a tool cannot be both scored and excluded")
    if not isinstance(data["targets"], dict):
        raise ValueError("schemas targets: expected an object")
    targets = {}
    for tool, target in data["targets"].items():
        target = _str_map("targets", target)
        if tool not in tools or not target.keys() <= _TARGET_KEYS:
            raise ValueError(f"schemas targets: {tool!r} needs a tool and {sorted(_TARGET_KEYS)}")
        if any(
            argument not in required[tool] or tools[tool][argument] is not str
            for argument in target.values()
        ):
            raise ValueError(f"schemas targets: {tool!r} must name its required string arguments")
        targets[tool] = MappingProxyType(target)
    kinds = _str_map("kinds", data["kinds"])
    if any(alias != alias.lower() for alias in kinds):
        raise ValueError("schemas kinds: aliases must be lower case")
    value_flags = data["value_flags"]
    if not isinstance(value_flags, list) or not all(isinstance(f, str) for f in value_flags):
        raise ValueError("schemas value_flags: expected a list of strings")
    return Schemas(
        tools=MappingProxyType(tools),
        required=MappingProxyType(required),
        targets=MappingProxyType(targets),
        kinds=MappingProxyType(kinds),
        value_flags=frozenset(value_flags),
        placeholder=placeholder,
    )


@dataclass(frozen=True)
class ToolCall:
    tool: str
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class Gold:
    """The expected call. A field left as None is not checked.

    operation and kind apply to exec_shell only, and other tools take only the target fields
    their schema maps. score raises ValueError for a record that breaks either rule.
    """

    tool: str
    # the kubectl verb for exec_shell, as the policy names it, such as "rollout restart"
    operation: str | None = None
    namespace: str | None = None
    # canonical kind, as the schema file's kinds map names it
    kind: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class Score:
    call: ToolCall
    error: ErrorType


@dataclass(frozen=True)
class _Target:
    operation: str | None
    namespace: str | None
    kind: str | None
    name: str | None


def _schema_valid(call: ToolCall, schemas: Schemas) -> bool:
    arguments = schemas.tools.get(call.tool)
    if arguments is None or not schemas.required[call.tool] <= call.arguments.keys():
        return False
    if not call.arguments.keys() <= arguments.keys():
        return False
    # type() and not isinstance(), so a bool is not taken for an int
    return all(type(value) is arguments[name] for name, value in call.arguments.items())


def _canonical_kind(kind: str, schemas: Schemas) -> str:
    # kubectl reads kinds case-insensitively, with an optional API group as in deployment.apps
    base = kind.lower().partition(".")[0]
    return schemas.kinds.get(base, base)


def _shell_target(command: str, schemas: Schemas, policy: Policy) -> _Target | None:
    # the allow-list verdict is not consulted: this scores intent, not admissibility
    try:
        argv = tokenize(command)
    except Rejected:
        return None
    if argv[0] not in policy.binaries:
        return None
    parsed = parse_args(argv[1:], policy, schemas.value_flags)
    # a namespace counts only when exactly one is given and -A is absent
    namespace = None
    if len(parsed.namespaces) == 1 and not parsed.all_namespaces:
        namespace = parsed.namespaces[0]
    positionals = parsed.positionals
    if positionals[:1] == ["exec"]:
        operation, targets = "exec", positionals[1:]
        # exec names a pod unless the type/name form says otherwise; no target, no kind
        types, names = (["pod"] if targets else []), targets
        if targets and "/" in targets[0]:
            types, names = split_targets(targets)
    else:
        verb = split_verb(positionals, policy)
        if verb is None:
            verb = (positionals[0], positionals[1:]) if positionals else (None, [])
        operation, targets = verb
        types, names = split_targets(targets) if targets else ([], [])
    kinds = {_canonical_kind(t, schemas) for t in types}
    # several kinds or names do not match one gold kind or name
    kind = kinds.pop() if len(kinds) == 1 else None
    name = names[0] if len(names) == 1 else None
    return _Target(operation, namespace, kind, name)


def _tool_target(call: ToolCall, schemas: Schemas) -> _Target:
    target = schemas.targets.get(call.tool, {})
    namespace = call.arguments.get(target["namespace"]) if "namespace" in target else None
    name = call.arguments.get(target["name"]) if "name" in target else None
    return _Target(None, namespace, None, name)


def _check_gold(gold: Gold, schemas: Schemas, policy: Policy) -> None:
    # a gold record no call can match would silently score every call as an error
    if gold.tool not in schemas.tools:
        raise ValueError(f"gold tool {gold.tool!r} is not in the schemas")
    if gold.tool == "exec_shell":
        if gold.kind is not None and _canonical_kind(gold.kind, schemas) != gold.kind:
            raise ValueError(f"gold kind {gold.kind!r} is not a canonical kind")
        # a two-word verb is read as one operation only when the policy names it
        if gold.operation and " " in gold.operation and gold.operation not in policy.verbs:
            raise ValueError(f"gold operation {gold.operation!r} is not a policy verb")
        return
    target = schemas.targets.get(gold.tool, {})
    if gold.operation is not None or gold.kind is not None:
        raise ValueError(f"gold for {gold.tool!r}: operation and kind apply to exec_shell only")
    if (gold.namespace is not None and "namespace" not in target) or (
        gold.name is not None and "name" not in target
    ):
        raise ValueError(f"gold for {gold.tool!r}: the tool has no such target argument")


def score(call: ToolCall, gold: Gold, schemas: Schemas, policy: Policy) -> Score:
    """Score a call against its gold record with the first error that applies.

    An exec_shell command is read with the allow-list tokenizer and argv walk, so a
    command that does not tokenize, or runs a binary the policy does not know, has no
    operation and scores WRONG_OP.

    Raises ValueError for a gold record that no call of its tool could match.
    """
    _check_gold(gold, schemas, policy)
    if not _schema_valid(call, schemas):
        return Score(call=call, error=ErrorType.BAD_SCHEMA)
    if call.tool != gold.tool:
        return Score(call=call, error=ErrorType.WRONG_OP)
    if call.tool == "exec_shell":
        target = _shell_target(call.arguments["command"], schemas, policy)
    else:
        target = _tool_target(call, schemas)
    if target is None or (gold.operation is not None and target.operation != gold.operation):
        return Score(call=call, error=ErrorType.WRONG_OP)
    expected = (gold.namespace, gold.kind, gold.name)
    actual = (target.namespace, target.kind, target.name)
    if any(want is not None and want != got for want, got in zip(expected, actual, strict=True)):
        return Score(call=call, error=ErrorType.WRONG_TARGET)
    return Score(call=call, error=ErrorType.OK)


@dataclass(frozen=True)
class ErrorCounts:
    total: int
    # scores per type, with every type present, OK included
    by_type: Mapping[ErrorType, int]

    @property
    def accuracy(self) -> float:
        """Tool-call accuracy, the share of calls scored OK; 0.0 when there are no calls.

        OK means a valid schema, the right tool and operation, and the right target. Parameter
        values, such as a replica count, a patch body or an exec script, are not scored.
        """
        return self.by_type[ErrorType.OK] / self.total if self.total else 0.0


def error_counts(scores: Iterable[Score]) -> ErrorCounts:
    """Count scores, so the share of each error type is by_type[e] / total."""
    total = 0
    by_type = {error: 0 for error in ErrorType}
    for item in scores:
        total += 1
        by_type[item.error] += 1
    return ErrorCounts(total=total, by_type=by_type)
