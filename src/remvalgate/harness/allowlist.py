"""Allow-list and namespace sandbox check for sampled candidate commands.

The policy file holds placeholder contents until the group fixes the lists.
"""

import json
import re
import shlex
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

DEFAULT_POLICY_PATH = Path(__file__).with_name("allowlist_policy.json")

_POLICY_KEYS = {"binaries", "verbs", "patch_denied_keys"}
_VERB_KEYS = {"resources", "flags", "name_required"}
_PATCH_FLAGS = ("-p", "--patch")
# a namespace is a DNS-1123 label; an object name is a DNS-1123 subdomain
_LABEL = re.compile(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?")
_SUBDOMAIN = re.compile(r"[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?")


class Reason(StrEnum):
    """Rejection reasons, in the order the checks run. The first failure wins.

    One exception: a flag that no verb allows is rejected before the verb check, but a flag
    that only the matched verb disallows is rejected after it.
    """

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
class VerbRule:
    resources: frozenset[str]
    # flag name to False (takes no value), True (takes any value) or the set of allowed values
    flags: Mapping[str, bool | frozenset[str]]
    name_required: bool


@dataclass(frozen=True)
class Policy:
    binaries: frozenset[str]
    # verb (one word, or two separated by a single space) to its rule
    verbs: Mapping[str, VerbRule]
    # JSON keys a patch body may not contain, at any depth
    patch_denied_keys: frozenset[str]


def _strings(key: str, value: object) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"policy {key}: expected a list of strings")
    return frozenset(value)


def _verb_rule(verb: str, rule: object) -> VerbRule:
    if not re.fullmatch(r"\S+( \S+)?", verb):
        raise ValueError("policy verbs: each verb is one word or two separated by one space")
    if not isinstance(rule, dict) or rule.keys() != _VERB_KEYS:
        raise ValueError(f"policy verbs: {verb!r} needs exactly {sorted(_VERB_KEYS)}")
    if not isinstance(rule["name_required"], bool):
        raise ValueError(f"policy verbs: {verb!r} name_required must be true or false")
    if not isinstance(rule["flags"], dict):
        raise ValueError(f"policy verbs: {verb!r} flags must be an object")
    flags = {
        name: value if isinstance(value, bool) else _strings("verbs", value)
        for name, value in rule["flags"].items()
    }
    return VerbRule(
        resources=_strings("verbs", rule["resources"]),
        flags=MappingProxyType(flags),
        name_required=rule["name_required"],
    )


def load_policy(path: str | Path) -> Policy:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("policy: expected a JSON object")
    missing = _POLICY_KEYS - data.keys()
    extra = data.keys() - _POLICY_KEYS
    if missing or extra:
        raise ValueError(f"policy keys: missing {sorted(missing)}, unexpected {sorted(extra)}")
    if not isinstance(data["verbs"], dict):
        raise ValueError("policy verbs: expected an object")
    verbs = {verb: _verb_rule(verb, rule) for verb, rule in data["verbs"].items()}
    takes_value: dict[str, bool] = {}
    for rule in verbs.values():
        for name, value in rule.flags.items():
            # the parser must know whether a flag takes a value before it knows the verb
            if takes_value.setdefault(name, value is not False) != (value is not False):
                raise ValueError(f"policy verbs: flag {name} takes a value for some verbs only")
    return Policy(
        binaries=_strings("binaries", data["binaries"]),
        verbs=MappingProxyType(verbs),
        patch_denied_keys=_strings("patch_denied_keys", data["patch_denied_keys"]),
    )


@dataclass(frozen=True)
class Verdict:
    command: str
    admitted: bool
    reason: Reason | None


# Characters bash expands or treats as operators. The scan follows bash quoting because
# shlex.split performs no expansion: "$(id)" and {a,b} reach it as plain words.
_UNQUOTED = frozenset(";&|<>(){}$`\\*?[]#~!")
# Rejected in every quoting state, single quotes included: AIOpsLab's kind mode escapes only
# double quotes and runs the command as sh -c "..." under a host shell, where these expand.
_ANYWHERE = frozenset("$`\\")


def _scan(command: str) -> None:
    quote = None
    for ch in command:
        if ord(ch) < 0x20 or ord(ch) == 0x7F or ch in _ANYWHERE:
            raise Rejected(Reason.SHELL_METACHAR)
        if quote:
            if ch == quote:
                quote = None
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


def _keys(node: object) -> Iterator[str]:
    # every key at any depth, plus the segments of JSON patch paths such as /spec/hostNetwork
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            if key == "path" and isinstance(value, str):
                yield from value.split("/")
            yield from _keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item)


def _patch_allowed(body: str, policy: Policy) -> bool:
    try:
        return policy.patch_denied_keys.isdisjoint(_keys(json.loads(body)))
    except (ValueError, RecursionError):
        # YAML bodies and unparsable JSON are not inspected, so they are not admitted
        return False


def _check_args(args: list[str], policy: Policy, namespace: str) -> None:
    takes_value = {
        name: value is not False
        for rule in policy.verbs.values()
        for name, value in rule.flags.items()
    }
    namespaces: list[str | None] = []
    flags: list[tuple[str, str | None]] = []
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
        elif name not in takes_value:
            flag_denied = True
        elif not takes_value[name]:
            # any attached value is rejected, since pflag reads -wA as -w -A
            flag_denied = flag_denied or value is not None
            flags.append((name, None))
        else:
            if value is None:
                value = next(rest, None)
            flag_denied = flag_denied or value is None
            flags.append((name, value))

    # namespace before flags, so the reason does not depend on token order
    if namespaces != [namespace]:
        raise Rejected(Reason.OUT_OF_SANDBOX)
    if flag_denied:
        raise Rejected(Reason.FLAG_DENIED)

    if len(positionals) >= 2 and f"{positionals[0]} {positionals[1]}" in policy.verbs:
        rule, targets = policy.verbs[f"{positionals[0]} {positionals[1]}"], positionals[2:]
    elif positionals and positionals[0] in policy.verbs:
        rule, targets = policy.verbs[positionals[0]], positionals[1:]
    else:
        raise Rejected(Reason.VERB_NOT_ALLOWED)

    for name, value in flags:
        allowed = rule.flags.get(name)
        if allowed is None or (isinstance(allowed, frozenset) and value not in allowed):
            raise Rejected(Reason.FLAG_DENIED)
        if name in _PATCH_FLAGS and not _patch_allowed(value, policy):
            raise Rejected(Reason.FLAG_DENIED)

    if not targets:
        raise Rejected(Reason.RESOURCE_NOT_ALLOWED)
    if "/" in targets[0]:
        # type/name form: every argument names its own type
        pairs = [target.partition("/") for target in targets]
        types, names = [t for t, _, _ in pairs], [n for _, _, n in pairs]
    else:
        types, names = targets[0].split(","), targets[1:]
    if not set(types) <= rule.resources:
        raise Rejected(Reason.RESOURCE_NOT_ALLOWED)
    if rule.name_required and not names:
        raise Rejected(Reason.RESOURCE_NOT_ALLOWED)
    if not all(_SUBDOMAIN.fullmatch(name) for name in names):
        raise Rejected(Reason.RESOURCE_NOT_ALLOWED)


def check(command: str, policy: Policy, namespace: str) -> Verdict:
    """Admit the command unchanged, or reject it with the first failing reason.

    Raises ValueError if the target namespace is not a valid sandbox: it must be a DNS-1123
    label and neither "default" nor a "kube-" system namespace.
    """
    if not _LABEL.fullmatch(namespace) or namespace == "default" or namespace.startswith("kube-"):
        raise ValueError(f"not a sandbox namespace: {namespace!r}")
    try:
        argv = tokenize(command)
        if argv[0] not in policy.binaries:
            raise Rejected(Reason.BINARY_NOT_ALLOWED)
        _check_args(argv[1:], policy, namespace)
    except Rejected as rejected:
        return Verdict(command=command, admitted=False, reason=rejected.reason)
    return Verdict(command=command, admitted=True, reason=None)


@dataclass(frozen=True)
class ReasonCounts:
    total: int
    # rejections per reason, with every reason present
    by_reason: Mapping[Reason, int]

    @property
    def admitted(self) -> int:
        return self.total - sum(self.by_reason.values())


def reason_counts(verdicts: Iterable[Verdict]) -> ReasonCounts:
    """Count verdicts, so the share removed by each reason is by_reason[r] / total."""
    total = 0
    by_reason = {reason: 0 for reason in Reason}
    for verdict in verdicts:
        total += 1
        if verdict.reason is not None:
            by_reason[verdict.reason] += 1
    return ReasonCounts(total=total, by_reason=by_reason)
