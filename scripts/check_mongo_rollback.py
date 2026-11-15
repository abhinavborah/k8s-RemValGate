r"""Live check of the MongoDB rollback (story E8-S5, #187) on a scratch user, never a problem.

Needs the kind cluster with Hotel Reservation Ready and the root password in the environment
(harness/credentials.py). Uses no pool, confirmation or singleton problem and no fault script, so
it is not an evaluation-set touch. On mongodb-geo it creates a scratch user with readWrite on
geo-db, snapshots the users, then for each database fault's change (a revoked role, a dropped
user) applies it, restores the snapshot and checks that the users equal it and that the scratch
user can log in again. It always drops the scratch user at the end and checks that the users are
as before the check. It prints only pass or fail lines, never a user document.

    set -a; . ~/.remvalgate_mongo.env; set +a
    uv run python scripts/check_mongo_rollback.py
"""

import secrets
import string
import subprocess
import sys

from remvalgate.harness.manifest import HOTEL_RESERVATION
from remvalgate.harness.mongo_snapshot import mongo_exec, read_users, restore, take

DEPLOYMENT = "mongodb-geo"
USER = "remvalgateprobe"
CHANGES = {
    "revoke_auth": f"db.getSiblingDB('admin').revokeRolesFromUser('{USER}', "
    "[{role: 'readWrite', db: 'geo-db'}]);",
    "storage_user_unregistered": f"db.getSiblingDB('admin').dropUser('{USER}');",
}


def logs_in(password: str) -> bool:
    args = ["kubectl", "-n", HOTEL_RESERVATION, "exec", f"deploy/{DEPLOYMENT}", "--", "mongo"]
    args += ["geo-db", "--quiet", "-u", USER, "-p", password, "--authenticationDatabase", "admin"]
    args += ["--eval", "print(db.getSiblingDB('geo-db').probe.countDocuments({}) >= 0)"]
    run = subprocess.run(args, capture_output=True, text=True, timeout=120)
    return run.returncode == 0 and run.stdout.strip().endswith("true")


def main() -> int:
    exec_ = mongo_exec(HOTEL_RESERVATION)
    before = read_users(DEPLOYMENT, exec_)
    if f"admin.{USER}" in before:
        print(f"FAIL: {USER} already exists; drop it first")
        return 1
    password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))
    failures = 0
    try:
        exec_(
            DEPLOYMENT,
            f"db.getSiblingDB('admin').createUser({{user: '{USER}', pwd: '{password}', "
            "roles: [{role: 'readWrite', db: 'geo-db'}]});",
        )
        snapshot = take(HOTEL_RESERVATION, exec_, (DEPLOYMENT,))
        for fault_type, change in CHANGES.items():
            exec_(DEPLOYMENT, change)
            changed = read_users(DEPLOYMENT, exec_) != snapshot.users[DEPLOYMENT]
            result = restore(fault_type, snapshot, exec_, {USER: password})
            login = logs_in(password)
            ok = changed and result.restored and login
            failures += not ok
            print(
                f"{'PASS' if ok else 'FAIL'} {fault_type}: change took {changed}, "
                f"restored {result.restored}, changed {list(result.changed)}, "
                f"errors {len(result.errors)}, login after restore {login}"
            )
    finally:
        try:
            exec_(DEPLOYMENT, f"db.getSiblingDB('admin').dropUser('{USER}');")
        except RuntimeError as error:
            print(f"FAIL: dropping {USER}: {error}")
            failures += 1
        same = read_users(DEPLOYMENT, exec_) == before
        failures += not same
        print(f"{'PASS' if same else 'FAIL'} users as before the check: {same}")
    print("result: PASS" if not failures else f"result: FAIL ({failures})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
