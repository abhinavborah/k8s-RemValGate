import json
import re

import pytest

from remvalgate.harness.mongo_snapshot import DEPLOYMENTS, read_users, restore, take

NS = "test-hotel-reservation"
RW = {"role": "readWrite", "db": "geo-db"}
UA = {"role": "userAdminAnyDatabase", "db": "admin"}
PASSWORDS = {"admin": "adminpass1", "root": "rootpass1"}


class FakeMongo:
    """The users of each deployment; applies the user-management calls a script makes."""

    def __init__(self, fail=()):
        self.users = {d: {"admin": [UA, RW], "root": [UA, RW]} for d in DEPLOYMENTS}
        self.passwords = {d: dict(PASSWORDS) for d in DEPLOYMENTS}
        self.fail = set(fail)
        self.scripts = []

    def __call__(self, deployment, script):
        if deployment in self.fail:
            raise RuntimeError("exec failed")
        self.scripts.append(script)
        users = self.users[deployment]
        if "getUsers()" in script:
            lines = ["MongoDB shell version v4.4.6"]
            for name, roles in sorted(users.items()):
                user = {"_id": f"admin.{name}", "user": name, "db": "admin", "roles": roles}
                user["mechanisms"] = ["SCRAM-SHA-1", "SCRAM-SHA-256"]
                lines.append("REMVALGATE_USER " + json.dumps(user))
            return "\n".join(lines) + "\n"
        calls = r"\.(dropUser|createUser|grantRolesToUser|revokeRolesFromUser)\((.*?)\);"
        for call, args in re.findall(calls, script):
            if call == "dropUser":
                del users[json.loads(args)]
            elif call == "createUser":
                name = re.search(r'user: "([^"]+)"', args)[1]
                roles = json.loads(re.search(r"roles: (\[.*?\])(?:, mechanisms|\})", args)[1])
                self.passwords[deployment][name] = re.search(r'pwd: "([^"]+)"', args)[1]
                users[name] = roles
            elif call in ("grantRolesToUser", "revokeRolesFromUser"):
                name, roles = json.loads(f"[{args}]")
                have = users[name]
                if call == "grantRolesToUser":
                    users[name] = have + roles
                else:
                    users[name] = [r for r in have if r not in roles]
        return ""


def test_read_users_keeps_user_lines_only_with_roles_in_a_fixed_order():
    mongo = FakeMongo()
    mongo.users["mongodb-geo"]["admin"] = [RW, UA]
    users = read_users("mongodb-geo", mongo)
    assert set(users) == {"admin.admin", "admin.root"}
    assert users["admin.admin"]["roles"] == [RW, UA]
    assert users["admin.root"]["roles"] == [RW, UA]


def test_take_reads_every_target_deployment():
    snapshot = take(NS, FakeMongo())
    assert snapshot.namespace == NS and set(snapshot.users) == set(DEPLOYMENTS)


@pytest.mark.parametrize(
    "change",
    [
        lambda users: users["admin"].remove(RW),  # RevokeAuth
        lambda users: users.pop("admin"),  # UserUnregistered
        lambda users: users.update(extra=[RW]),  # a user added
        lambda users: users["root"].append({"role": "dbOwner", "db": "geo-db"}),  # a role added
    ],
)
@pytest.mark.parametrize("fault_type", ["revoke_auth", "storage_user_unregistered"])
def test_restore_makes_the_users_equal_the_snapshot(fault_type, change):
    mongo = FakeMongo()
    snapshot = take(NS, mongo)
    change(mongo.users["mongodb-geo"])
    result = restore(fault_type, snapshot, mongo, PASSWORDS)
    assert result.attempted and result.restored, result
    assert read_users("mongodb-geo", mongo) == snapshot.users["mongodb-geo"]
    # a dropped user is created again with its own password
    assert mongo.passwords["mongodb-geo"] == PASSWORDS


def test_an_unchanged_deployment_gets_no_script():
    mongo = FakeMongo()
    snapshot = take(NS, mongo)
    restore("revoke_auth", snapshot, mongo, PASSWORDS)
    assert all("getUsers()" in s for s in mongo.scripts)


def test_a_dropped_user_with_no_known_password_is_reported():
    mongo = FakeMongo()
    snapshot = take(NS, mongo)
    mongo.users["mongodb-rate"].pop("admin")
    result = restore("storage_user_unregistered", snapshot, mongo, {"root": "rootpass1"})
    assert result.errors == ("mongodb-rate: no password to create admin.admin again",)
    assert result.changed == ("mongodb-rate/users",)
    assert not result.restored


def test_a_failed_call_is_recorded_and_the_next_deployment_still_restored():
    mongo = FakeMongo()
    snapshot = take(NS, mongo)
    mongo.users["mongodb-rate"]["admin"].remove(RW)
    mongo.fail = {"mongodb-geo"}
    result = restore("revoke_auth", snapshot, mongo, PASSWORDS)
    assert result.errors == ("mongodb-geo: exec failed",)
    assert read_users("mongodb-rate", mongo) == snapshot.users["mongodb-rate"]
    assert not result.restored


def test_a_kubernetes_object_fault_is_not_attempted():
    mongo = FakeMongo()
    snapshot = take(NS, mongo)
    calls = len(mongo.scripts)
    assert not restore("misconfig_k8s", snapshot, mongo, PASSWORDS).attempted
    assert len(mongo.scripts) == calls


def test_the_read_script_is_balanced():
    # the fake does not parse the read script, so a stray brace would only show on a cluster
    from remvalgate.harness.mongo_snapshot import _READ

    for left, right in ("()", "{}", "[]"):
        assert _READ.count(left) == _READ.count(right)
