import pytest

from remvalgate.harness.credentials import TOKENS, fill, redact, values

ENV = {"REMVALGATE_MONGO_ADMIN_PASSWORD": "Abc1", "REMVALGATE_MONGO_ROOT_PASSWORD": "Xyz9"}
COMMAND = (
    "kubectl -n ns exec deploy/m -- mongo admin -u root -p ROOT_PASSWORD_REF"
    " --authenticationDatabase admin"
    """ --eval 'db.createUser({user: "admin", pwd: "ADMIN_PASSWORD_REF", roles: []})'"""
)


def test_fill_replaces_every_token_and_redact_puts_them_back():
    filled = fill(COMMAND, ENV)
    assert not any(token in filled for token in TOKENS)
    assert "-p Xyz9" in filled and 'pwd: "Abc1"' in filled
    assert redact(filled, ENV) == COMMAND


def test_a_command_with_no_token_needs_no_variable():
    assert fill("kubectl -n ns get pods", {}) == "kubectl -n ns get pods"


def test_redact_touches_only_where_a_password_is_written():
    assert redact("user Abc1 and -p Abc1", ENV) == "user Abc1 and -p ADMIN_PASSWORD_REF"


def test_a_missing_or_unsafe_value_raises():
    with pytest.raises(KeyError):
        fill(COMMAND, {"REMVALGATE_MONGO_ADMIN_PASSWORD": "Abc1"})
    with pytest.raises(ValueError):
        values({**ENV, "REMVALGATE_MONGO_ROOT_PASSWORD": "a b"})
