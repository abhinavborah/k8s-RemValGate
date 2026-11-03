import pytest

from remvalgate.harness.allowlist import (
    DEFAULT_POLICY_PATH,
    Reason,
    check_chain,
    load_policy,
    split_chain,
)
from remvalgate.harness.folds import FAULT_TYPES, fault_type, pool_folds, pool_ids
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, load_registry, logged_namespace
from remvalgate.overlay.fixes import FIXES
from remvalgate.retrieval import Source
from remvalgate.retrieval.corpus import build_corpus
from remvalgate.retrieval.pool_corpus import (
    RUNBOOK,
    TEMPLATES,
    UPSTREAM_FIXES,
    fix_command,
    pool_incidents,
)

REGISTRY = load_registry(DEFAULT_REGISTRY_PATH)
INCIDENTS = pool_incidents(REGISTRY)
POLICY = load_policy(DEFAULT_POLICY_PATH)
MONGO_TYPES = {"RevokeAuth", "UserUnregistered"}


def test_every_pool_problem_has_one_record_from_a_known_fix():
    assert [e.entry_id for e in INCIDENTS] == pool_ids(REGISTRY)
    assert len(INCIDENTS) == 16
    # every pool problem is covered by exactly one of the two fix tables
    for problem_id in pool_ids(REGISTRY):
        assert (problem_id in FIXES) != (problem_id in UPSTREAM_FIXES)


def test_records_follow_the_rule():
    for e in INCIDENTS:
        assert e.source is Source.INCIDENT
        assert e.fault_type == fault_type(e.entry_id)
        assert e.target_service in e.fix_command
        client = e.target_service.removeprefix("mongodb-")
        assert e.summary == TEMPLATES[e.fault_type].format(service=e.target_service, client=client)
        assert e.fix_command.startswith(f"kubectl -n {logged_namespace(e.entry_id)} ")
        assert "rollout status" not in e.fix_command
        # exec_shell refuses a command that ends in a double quote
        assert not e.fix_command.endswith('"')


def test_overlay_records_are_the_overlay_fix_under_the_rule():
    for e in INCIDENTS:
        if e.entry_id in FIXES:
            assert e.fix_command == fix_command(FIXES[e.entry_id], logged_namespace(e.entry_id))


def test_fixes_are_pairwise_distinct():
    # a duplicate would make check_corpus refuse the folds that hold one copy out
    fixes = [e.fix_command.strip() for e in [*INCIDENTS, *RUNBOOK]]
    assert len(set(fixes)) == len(fixes)


def test_no_password_is_committed():
    for e in [*INCIDENTS, *RUNBOOK]:
        if "pwd" in e.fix_command:
            assert 'pwd: "<password>"' in e.fix_command


def test_runbook_covers_every_fault_type_with_ids_free_of_stems():
    assert {e.fault_type for e in RUNBOOK} == set(FAULT_TYPES.values())
    for e in RUNBOOK:
        assert e.source is Source.RUNBOOK
        assert not any(stem in e.entry_id for stem in FAULT_TYPES)


@pytest.mark.parametrize("fold", range(4))
def test_build_corpus_builds_every_fold(fold):
    corpus = build_corpus(
        fold, assignment=pool_folds(REGISTRY), incidents=INCIDENTS, runbook=list(RUNBOOK)
    )
    assert len(corpus) == 12 + len(RUNBOOK)


def test_kubernetes_records_pass_the_allow_list_and_mongodb_records_do_not():
    for e in INCIDENTS:
        verdict = check_chain(e.fix_command, POLICY, logged_namespace(e.entry_id))
        if e.fault_type in MONGO_TYPES:
            # deploy/<name> is not a bare pod name, which the exec rule needs
            assert verdict.reason is Reason.RESOURCE_NOT_ALLOWED
            assert len(split_chain(e.fix_command)) == 2
        else:
            assert verdict.admitted, e.entry_id


def test_fix_command_drops_waits_and_joins_the_rest():
    commands = [["scale", "deployment", "a", "--replicas=1"], ["rollout", "status", "deploy/a"]]
    assert fix_command(commands, "ns") == "kubectl -n ns scale deployment a --replicas=1"
    two = fix_command([["rollout", "restart", "deployment/a"], ["scale", "deploy", "b"]], None)
    assert two == "kubectl rollout restart deployment/a && kubectl scale deploy b"
    with pytest.raises(ValueError):
        fix_command([["rollout", "status", "deploy/a"]], "ns")
