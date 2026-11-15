"""Snapshot and restore of the MongoDB users of the database faults (story E8-S5, #187).

RevokeAuth revokes readWrite on the target database from the admin user, and UserUnregistered
drops that user. Neither changes a Kubernetes object, so snapshot.py leaves them out of scope.
take saves each user's name, roles and authentication mechanisms on each target MongoDB, and
restore makes the users equal to that again: a user the snapshot does not hold is dropped, a
dropped user is created again with its password, and roles are granted or revoked to match.

The MongoDBs of AIOpsLab's Hotel Reservation have no user with the root role: their root user
holds userAdminAnyDatabase, which manages users but may not read or write the stored credentials.
So a dropped user is created again from its password, which comes from the environment
(credentials.py) for admin and root, and a password the command changed is not put back.

The MongoDB calls are injected, as in snapshot.py and harm.py, so CI tests the logic without a
cluster. mongo_exec runs them through kubectl exec as the root user.
"""

import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from remvalgate.harness.credentials import redact, values
from remvalgate.harness.snapshot import OUT_OF_SCOPE, RestoreResult

# the MongoDB Deployments the database faults target (AIOpsLab's inject_app.py)
DEPLOYMENTS = ("mongodb-geo", "mongodb-rate")
# marks the output lines that carry a user, so shell banners and warnings are ignored
_MARK = "REMVALGATE_USER "
_READ = (
    "db.getSiblingDB('admin').getUsers().forEach(function (u) {"
    f" print('{_MARK}' + JSON.stringify({{_id: u._id, user: u.user, db: u.db,"
    " roles: u.roles, mechanisms: u.mechanisms})); });"
)

# (deployment, JavaScript) -> the mongo shell's stdout; raises RuntimeError on a failed call
Exec = Callable[[str, str], str]


@dataclass(frozen=True)
class UserSnapshot:
    namespace: str
    # deployment -> user id ("<database>.<user>") -> user, roles and mechanisms
    users: Mapping[str, Mapping[str, dict]]


def _normal(user: dict) -> dict:
    roles = sorted((r["role"], r["db"]) for r in user["roles"])
    return {
        "user": user["user"],
        "db": user["db"],
        "roles": [{"role": r, "db": d} for r, d in roles],
        "mechanisms": sorted(user.get("mechanisms") or []),
    }


def read_users(deployment: str, exec_: Exec) -> dict[str, dict]:
    """Every user of the deployment's MongoDB, by user id, with its roles in a fixed order."""
    users = {}
    for line in exec_(deployment, _READ).splitlines():
        if line.startswith(_MARK):
            user = json.loads(line[len(_MARK) :])
            users[user["_id"]] = _normal(user)
    return users


def take(namespace: str, exec_: Exec, deployments: tuple[str, ...] = DEPLOYMENTS) -> UserSnapshot:
    """Save the users of each deployment. A failed read raises, before the command runs."""
    return UserSnapshot(namespace, {d: read_users(d, exec_) for d in deployments})


def env_passwords() -> dict[str, str]:
    """The passwords of the users the harness knows, from the environment (credentials.py)."""
    found = values()
    return {"admin": found["ADMIN_PASSWORD_REF"], "root": found["ROOT_PASSWORD_REF"]}


def _restore_script(
    saved: Mapping[str, dict], now: Mapping[str, dict], passwords: Mapping[str, str]
) -> tuple[str, list[str]]:
    """The script that makes the users now equal the saved ones, and the users it cannot
    create again because no password is known for them."""
    lines, missing = [], []
    for user_id in sorted(set(now) - set(saved)):
        user = now[user_id]
        on = f"db.getSiblingDB({json.dumps(user['db'])})"
        lines.append(f"{on}.dropUser({json.dumps(user['user'])});")
    for user_id in sorted(saved):
        user = saved[user_id]
        on = f"db.getSiblingDB({json.dumps(user['db'])})"
        if user_id not in now:
            if user["user"] not in passwords:
                missing.append(user_id)
                continue
            # pwd as a bare key, the form credentials.redact finds in an error
            spec = (
                f"{{user: {json.dumps(user['user'])}, pwd: {json.dumps(passwords[user['user']])}, "
                f"roles: {json.dumps(user['roles'])}"
            )
            if user["mechanisms"]:
                spec += f", mechanisms: {json.dumps(user['mechanisms'])}"
            lines.append(f"{on}.createUser({spec}}});")
            continue
        have = {(r["role"], r["db"]) for r in now[user_id]["roles"]}
        want = {(r["role"], r["db"]) for r in user["roles"]}
        name = json.dumps(user["user"])
        if want - have:
            grant = [{"role": r, "db": d} for r, d in sorted(want - have)]
            lines.append(f"{on}.grantRolesToUser({name}, {json.dumps(grant)});")
        if have - want:
            revoke = [{"role": r, "db": d} for r, d in sorted(have - want)]
            lines.append(f"{on}.revokeRolesFromUser({name}, {json.dumps(revoke)});")
    return "".join(lines), missing


def restore(
    fault_type: str,
    snapshot: UserSnapshot,
    exec_: Exec,
    passwords: Mapping[str, str] | None = None,
) -> RestoreResult:
    """Make each deployment's users equal the snapshot, then read them again to compare.

    passwords maps a user name to the password a dropped user is created again with; it defaults
    to env_passwords(). A fault type with Kubernetes-object state is not this module's: nothing
    is attempted, as snapshot.restore does for these faults. A failed call, or a dropped user
    with no known password, is recorded and the next deployment is still restored.
    """
    if fault_type not in OUT_OF_SCOPE:
        return RestoreResult(attempted=False, changed=())
    if passwords is None:
        try:
            passwords = env_passwords()
        except (KeyError, ValueError):
            # no password set: a dropped user is then reported, not created again
            passwords = {}
    changed, errors = [], []
    for deployment, users in snapshot.users.items():
        try:
            script, missing = _restore_script(users, read_users(deployment, exec_), passwords)
            errors += [f"{deployment}: no password to create {u} again" for u in missing]
            if script:
                exec_(deployment, script)
            if read_users(deployment, exec_) != users:
                changed.append(f"{deployment}/users")
        except RuntimeError as error:
            errors.append(f"{deployment}: {error}")
    return RestoreResult(attempted=True, changed=tuple(changed), errors=tuple(errors))


def mongo_exec(namespace: str, context: str | None = None, timeout: float = 120) -> Exec:
    """Return an Exec that runs the mongo shell in a deployment as root, never through a shell.

    The root password comes from the environment and is redacted from any error. Not unit tested
    and never run by CI; scripts/check_mongo_rollback.py runs it on a cluster.
    """
    password = values()["ROOT_PASSWORD_REF"]
    base = ["kubectl", *(["--context", context] if context else []), "-n", namespace, "exec"]

    def exec_(deployment: str, script: str) -> str:
        args = [*base, f"deploy/{deployment}", "--", "mongo", "admin", "--quiet"]
        args += ["-u", "root", "-p", password, "--authenticationDatabase", "admin"]
        try:
            run = subprocess.run(
                [*args, "--eval", script], capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"timed out after {error.timeout} s") from error
        # the shell reports an uncaught error on stdout and exits with 252
        if run.returncode != 0 or "uncaught exception" in run.stdout:
            text = run.stdout.strip() + "\n" + run.stderr.strip()
            # cut short, and redacted: a createUser script holds a password
            raise RuntimeError(redact(text.strip())[:300])
        return run.stdout

    return exec_
