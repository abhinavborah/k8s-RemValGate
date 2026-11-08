"""Database credentials for the MongoDB fixes, written as placeholders and filled in at execution.

Hotel Reservation's MongoDB enforces authentication, so a fix for RevokeAuth or
UserUnregistered must log in (mongo -u USER -p PASSWORD --authenticationDatabase admin). The
planner is told the users and writes each password as a placeholder token. The allow-list admits
only the tokens, and the harness replaces them with the real values just before the command
runs. Commands, transcripts, records and logs keep the tokens, so no password is stored.

The values come from environment variables set by whoever starts the run; they are never
committed. A value must be letters and digits only, so the filled command needs no quoting.
"""

import os
import re
from collections.abc import Mapping

# namespaces whose MongoDB enforces authentication: the planner is told the users there
AUTH_NAMESPACES = frozenset({"test-hotel-reservation"})
USERS = frozenset({"admin", "root"})
# placeholder token: the environment variable that holds its value
TOKENS = {
    "ADMIN_PASSWORD_REF": "REMVALGATE_MONGO_ADMIN_PASSWORD",
    "ROOT_PASSWORD_REF": "REMVALGATE_MONGO_ROOT_PASSWORD",
}
_VALUE = re.compile(r"[A-Za-z0-9]+")


def values(env: Mapping[str, str] = os.environ) -> dict[str, str]:
    """The value of every token. Raises KeyError for a missing variable and ValueError for a
    value that is not letters and digits only."""
    found = {}
    for token, name in TOKENS.items():
        value = env[name]
        if not _VALUE.fullmatch(value):
            raise ValueError(f"{name} must be letters and digits only")
        found[token] = value
    return found


def fill(command: str, env: Mapping[str, str] = os.environ) -> str:
    """command with every token replaced by its value. A command with no token is returned as it
    is and needs no variable."""
    if not any(token in command for token in TOKENS):
        return command
    for token, value in values(env).items():
        command = command.replace(token, value)
    return command


def redact(text: str, env: Mapping[str, str] = os.environ) -> str:
    """text with every set value replaced by its token where a password is written (after -p,
    or as a pwd field), for anything that is stored. Only those places, since a value can also
    be an ordinary word."""
    for token, name in TOKENS.items():
        value = env.get(name)
        if not value:
            continue
        v = re.escape(value)
        text = re.sub(rf"(-p\s+|--password[=\s]+)(['\"]?){v}\b", rf"\g<1>\g<2>{token}", text)
        text = re.sub(rf"(pwd\s*:\s*['\"]){v}(['\"])", rf"\g<1>{token}\g<2>", text)
    return text
