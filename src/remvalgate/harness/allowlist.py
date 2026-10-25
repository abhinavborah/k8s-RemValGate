"""Allow-list and namespace sandbox check for sampled candidate commands.

The policy file holds placeholder contents until the group fixes the lists.
"""

import json
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

DEFAULT_POLICY_PATH = Path(__file__).with_name("allowlist_policy.json")


class Reason(StrEnum):
    """Rejection reasons, in the order the checks run. The first failure wins."""

    EMPTY = "EMPTY"
    SHELL_METACHAR = "SHELL_METACHAR"
    PARSE_ERROR = "PARSE_ERROR"
    BINARY_NOT_ALLOWED = "BINARY_NOT_ALLOWED"
    OUT_OF_SANDBOX = "OUT_OF_SANDBOX"
    FLAG_DENIED = "FLAG_DENIED"
    VERB_NOT_ALLOWED = "VERB_NOT_ALLOWED"
    RESOURCE_NOT_ALLOWED = "RESOURCE_NOT_ALLOWED"


class Rejected(Exception):
    def __init__(self, reason: Reason):
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True)
class Policy:
    binaries: frozenset[str]
    # one word, or two separated by a single space ("rollout restart")
    verbs: frozenset[str]
    resources: frozenset[str]
    # flag name to False (takes no value), True (takes any value) or the set of allowed values
    flags: Mapping[str, bool | frozenset[str]]


def _strings(key: str, value: object) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"policy {key}: expected a list of strings")
    return frozenset(value)


def load_policy(path: str | Path) -> Policy:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("policy: expected a JSON object")
    keys = {field.name for field in fields(Policy)}
    missing = keys - data.keys()
    extra = data.keys() - keys
    if missing or extra:
        raise ValueError(f"policy keys: missing {sorted(missing)}, unexpected {sorted(extra)}")

    verbs = _strings("verbs", data["verbs"])
    if not all(re.fullmatch(r"\S+( \S+)?", verb) for verb in verbs):
        raise ValueError("policy verbs: each verb is one word or two separated by one space")
    if not isinstance(data["flags"], dict):
        raise ValueError("policy flags: expected an object")
    flags: dict[str, bool | frozenset[str]] = {}
    for name, rule in data["flags"].items():
        flags[name] = rule if isinstance(rule, bool) else _strings("flags", rule)
    return Policy(
        binaries=_strings("binaries", data["binaries"]),
        verbs=verbs,
        resources=_strings("resources", data["resources"]),
        flags=MappingProxyType(flags),
    )


@dataclass(frozen=True)
class Verdict:
    command: str
    admitted: bool
    reason: Reason | None


# Characters bash expands or treats as operators. The scan follows bash quoting because
# shlex.split performs no expansion: "$(id)" and {a,b} reach it as plain words.
_UNQUOTED = frozenset(";&|<>(){}$`\\*?[]#~!")
_DOUBLE_QUOTED = frozenset("$`\\")


def _scan(command: str) -> None:
    quote = None
    for ch in command:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise Rejected(Reason.SHELL_METACHAR)
        if quote == "'":
            if ch == "'":
                quote = None
        elif quote == '"':
            if ch == '"':
                quote = None
            elif ch in _DOUBLE_QUOTED:
                raise Rejected(Reason.SHELL_METACHAR)
        elif ch in "'\"":
            quote = ch
        elif ch in _UNQUOTED:
            raise Rejected(Reason.SHELL_METACHAR)
    if quote:
        raise Rejected(Reason.PARSE_ERROR)


def tokenize(command: str) -> list[str]:
    """Split a candidate into argv, rejecting anything the shell would expand or chain."""
    if not command.strip():
        raise Rejected(Reason.EMPTY)
    _scan(command)
    # cannot raise: the scan has already rejected backslashes and unterminated quotes
    return shlex.split(command)


def _split_flag(token: str) -> tuple[str, str | None]:
    if token.startswith("--"):
        name, eq, value = token.partition("=")
        return name, value if eq else None
    # a short flag may carry its value attached, as in -nsandbox-a or -owide. An empty
    # attached value ("-n=") stays "" so the next token is not read as the value.
    return token[:2], token[2:].removeprefix("=") if len(token) > 2 else None


def _check_args(args: list[str], policy: Policy, namespace: str) -> None:
    namespaces: list[str | None] = []
    flag_denied = False
    positionals: list[str] = []
    rest = iter(args)
    for token in rest:
        if not token.startswith("-") or token == "-":
            positionals.append(token)
            continue
        name, value = _split_flag(token)
        if name in ("-A", "--all-namespaces"):
            raise Rejected(Reason.OUT_OF_SANDBOX)
        if name in ("-n", "--namespace"):
            namespaces.append(value if value is not None else next(rest, None))
        elif name not in policy.flags:
            flag_denied = True
        elif policy.flags[name] is False:
            # any attached value is rejected, since pflag reads -wA as -w -A
            flag_denied = flag_denied or value is not None
        else:
            if value is None:
                value = next(rest, None)
            allowed = policy.flags[name]
            if value is None or (allowed is not True and value not in allowed):
                flag_denied = True

    # namespace before flags, so the reason does not depend on token order
    if namespaces != [namespace]:
        raise Rejected(Reason.OUT_OF_SANDBOX)
    if flag_denied:
        raise Rejected(Reason.FLAG_DENIED)

    if len(positionals) >= 2 and f"{positionals[0]} {positionals[1]}" in policy.verbs:
        targets = positionals[2:]
    elif positionals and positionals[0] in policy.verbs:
        targets = positionals[1:]
    else:
        raise Rejected(Reason.VERB_NOT_ALLOWED)

    if not targets:
        raise Rejected(Reason.RESOURCE_NOT_ALLOWED)
    if "/" in targets[0]:
        # type/name form: every argument names its own type
        types = [target.split("/", 1)[0] for target in targets]
    else:
        types = targets[0].split(",")
    if not set(types) <= policy.resources:
        raise Rejected(Reason.RESOURCE_NOT_ALLOWED)


def check(command: str, policy: Policy, namespace: str) -> Verdict:
    """Admit the command unchanged, or reject it with the first failing reason."""
    try:
        argv = tokenize(command)
        if argv[0] not in policy.binaries:
            raise Rejected(Reason.BINARY_NOT_ALLOWED)
        _check_args(argv[1:], policy, namespace)
    except Rejected as rejected:
        return Verdict(command=command, admitted=False, reason=rejected.reason)
    return Verdict(command=command, admitted=True, reason=None)


def reason_counts(verdicts: Iterable[Verdict]) -> dict[Reason, int]:
    """Rejections per reason, with every reason present. Admitted verdicts are not counted."""
    counts = {reason: 0 for reason in Reason}
    for verdict in verdicts:
        if verdict.reason is not None:
            counts[verdict.reason] += 1
    return counts
