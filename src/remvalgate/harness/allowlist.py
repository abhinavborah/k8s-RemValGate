"""Allow-list and namespace sandbox check for sampled candidate commands.

The policy file holds placeholder contents until the group fixes the lists.
"""

import json
import re
from collections.abc import Mapping
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
