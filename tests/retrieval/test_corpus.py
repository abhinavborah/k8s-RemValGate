import random

import pytest

from remvalgate.harness.folds import assign_folds, fault_type, pool_folds, pool_ids
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, load_registry
from remvalgate.retrieval import PastFix, Source
from remvalgate.retrieval.corpus import build_corpus, check_corpus

# synthetic ids that carry the stems of the fault types but name no registered problem
STEMS = {
    "TargetPortMisconfig": "synth-k8s_target_port-misconfig-{}",
    "RevokeAuth": "synth-revoke_auth-{}",
    "UserUnregistered": "synth-user_unregistered-{}",
    "ScalePod": "synth-scale_pod_zero-{}",
    "AssignNonExistentNode": "synth-assign_to_non_existent_node-{}",
}
PLAN = {
    "TargetPortMisconfig": 6,
    "RevokeAuth": 2,
    "UserUnregistered": 2,
    "ScalePod": 3,
    "AssignNonExistentNode": 3,
}


def problems() -> list[str]:
    return [STEMS[t].format(i) for t, n in PLAN.items() for i in range(1, n + 1)]


def incident(problem_id: str) -> PastFix:
    return PastFix(
        entry_id=problem_id,
        source=Source.INCIDENT,
        fault_type=fault_type(problem_id),
        target_service=f"demo-{problem_id}",
        summary=f"incident {problem_id} of a synthetic fault",
        fix_command=f"kubectl -n demo patch deployment demo-{problem_id} --type=json",
    )


def runbook() -> list[PastFix]:
    return [
        PastFix(
            entry_id=f"runbook-{t}",
            source=Source.RUNBOOK,
            fault_type=t,
            target_service="<service>",
            summary=f"general steps for {t}",
            fix_command=f"kubectl -n demo rollout restart deployment/<service> # {t}",
        )
        for t in STEMS
    ]


ASSIGNMENT = assign_folds(problems())
INCIDENTS = [incident(p) for p in problems()]
FOLDS = range(4)


def held_out(fold: int) -> list[str]:
    return [p for p, f in ASSIGNMENT.items() if f == fold]


def build(fold: int, **overrides):
    args = {"assignment": ASSIGNMENT, "incidents": INCIDENTS, "runbook": runbook()}
    args.update(overrides)
    return build_corpus(fold, **args)


# --- build_corpus ----------------------------------------------------------------------------


@pytest.mark.parametrize("fold", FOLDS)
def test_no_held_out_incident_or_its_fix_is_in_the_corpus(fold):
    corpus = build(fold)
    gone = held_out(fold)
    assert len(gone) == 4
    ids = {e.entry_id for e in corpus}
    fixes = {e.fix_command for e in corpus}
    for problem_id in gone:
        assert problem_id not in ids
        assert incident(problem_id).fix_command not in fixes


@pytest.mark.parametrize("fold", FOLDS)
def test_the_corpus_holds_the_twelve_training_incidents_and_every_runbook_entry(fold):
    corpus = build(fold)
    incidents = [e for e in corpus if e.source is Source.INCIDENT]
    notes = [e for e in corpus if e.source is Source.RUNBOOK]
    assert sorted(e.entry_id for e in incidents) == sorted(
        p for p in problems() if p not in held_out(fold)
    )
    assert sorted(e.entry_id for e in notes) == sorted(e.entry_id for e in runbook())


@pytest.mark.parametrize("fold", FOLDS)
def test_every_fault_type_has_a_past_incident_in_each_fold(fold):
    types = {e.fault_type for e in build(fold) if e.source is Source.INCIDENT}
    assert types == set(STEMS)


@pytest.mark.parametrize("fold", FOLDS)
def test_a_built_corpus_passes_the_check(fold):
    assert check_corpus(build(fold), fold, ASSIGNMENT, INCIDENTS) == []


def test_the_corpus_does_not_depend_on_the_order_of_the_input():
    shuffled = INCIDENTS[:]
    random.Random(3).shuffle(shuffled)
    notes = runbook()
    random.Random(4).shuffle(notes)
    assert build(0, incidents=shuffled, runbook=notes) == build(0)


@pytest.mark.parametrize("fold", FOLDS)
def test_the_committed_pool_gives_an_isolated_corpus_in_every_fold(fold):
    # only the pool ids are read, as in tests/harness/test_folds.py; the entries are synthetic
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    assignment = pool_folds(registry)
    incidents = [incident(p) for p in pool_ids(registry)]
    corpus = build_corpus(fold, assignment=assignment, incidents=incidents, runbook=runbook())
    training = [e for e in corpus if e.source is Source.INCIDENT]
    assert len(training) == 12
    assert not {e.entry_id for e in training} & {p for p, f in assignment.items() if f == fold}
    assert {e.fault_type for e in training} == set(STEMS)
    assert check_corpus(corpus, fold, assignment, incidents) == []


def test_two_folds_give_two_different_corpora():
    assert build(0) != build(1)


def test_the_corpus_is_sorted_incidents_first_then_runbook_by_id():
    corpus = build(2)
    ids = [e.entry_id for e in corpus]
    split = sum(e.source is Source.INCIDENT for e in corpus)
    assert ids[:split] == sorted(ids[:split])
    assert ids[split:] == sorted(ids[split:])
    assert all(e.source is Source.INCIDENT for e in corpus[:split])


# --- build_corpus refusals -------------------------------------------------------------------


@pytest.mark.parametrize("fold", [-1, 4, 99])
def test_a_fold_that_does_not_exist_is_refused(fold):
    with pytest.raises(ValueError, match="fold"):
        build(fold)


def test_an_incident_that_is_not_in_the_assignment_is_refused():
    stray = incident("synth-scale_pod_zero-9")
    with pytest.raises(ValueError, match="not in the fold assignment"):
        build(0, incidents=[*INCIDENTS, stray])


def test_an_incident_labelled_with_the_wrong_fault_type_is_refused():
    wrong = incident(problems()[0])
    wrong = PastFix(**{**wrong.__dict__, "fault_type": "RevokeAuth"})
    with pytest.raises(ValueError, match="fault type"):
        build(0, incidents=[wrong, *INCIDENTS[1:]])


def test_a_runbook_entry_marked_as_an_incident_is_refused():
    bad = PastFix(**{**runbook()[0].__dict__, "source": Source.INCIDENT})
    with pytest.raises(ValueError, match="runbook"):
        build(0, runbook=[bad, *runbook()[1:]])


def test_an_incident_marked_as_a_runbook_entry_is_refused():
    bad = PastFix(**{**INCIDENTS[0].__dict__, "source": Source.RUNBOOK})
    with pytest.raises(ValueError, match="incident"):
        build(0, incidents=[bad, *INCIDENTS[1:]])


def test_a_runbook_entry_that_reuses_a_problem_id_is_refused():
    bad = PastFix(**{**runbook()[0].__dict__, "entry_id": held_out(0)[0]})
    with pytest.raises(ValueError, match="problem id"):
        build(0, runbook=[bad, *runbook()[1:]])


def test_a_duplicate_entry_id_is_refused():
    with pytest.raises(ValueError, match="duplicate"):
        build(0, incidents=[*INCIDENTS, INCIDENTS[0]])


def test_a_runbook_entry_that_copies_a_held_out_fix_is_refused():
    leak = PastFix(**{**runbook()[0].__dict__, "fix_command": incident(held_out(0)[0]).fix_command})
    with pytest.raises(ValueError, match="held-out"):
        build(0, runbook=[leak, *runbook()[1:]])


def test_a_fault_type_with_no_training_incident_is_refused():
    only_one = [i for i in INCIDENTS if i.fault_type != "ScalePod"] + [
        incident("synth-scale_pod_zero-1")
    ]
    assignment = {p: f for p, f in ASSIGNMENT.items() if p in {i.entry_id for i in only_one}}
    # the only ScalePod incident is held out in its own fold, so that corpus has none
    fold = assignment["synth-scale_pod_zero-1"]
    with pytest.raises(ValueError, match="ScalePod"):
        build(fold, assignment=assignment, incidents=only_one)


# --- check_corpus: it must catch each leak ---------------------------------------------------


def test_check_catches_a_held_out_incident_in_the_corpus():
    corpus = [*build(0), incident(held_out(0)[0])]
    messages = check_corpus(corpus, 0, ASSIGNMENT, INCIDENTS)
    assert any(held_out(0)[0] in m for m in messages)


def test_check_catches_a_held_out_incident_even_when_its_fix_was_edited():
    edited = PastFix(**{**incident(held_out(3)[0]).__dict__, "fix_command": "kubectl get pods"})
    messages = check_corpus([*build(3), edited], 3, ASSIGNMENT, INCIDENTS)
    assert any("held-out incident is in the corpus" in m for m in messages)


def test_check_catches_a_held_out_fix_under_another_id():
    sneaky = PastFix(
        **{**runbook()[0].__dict__, "fix_command": incident(held_out(1)[0]).fix_command}
    )
    messages = check_corpus([*build(1), sneaky], 1, ASSIGNMENT, INCIDENTS)
    assert any("held-out" in m for m in messages)


def test_check_catches_a_fault_type_with_no_incident():
    thinned = [
        e for e in build(0) if not (e.source is Source.INCIDENT and e.fault_type == "RevokeAuth")
    ]
    messages = check_corpus(thinned, 0, ASSIGNMENT, INCIDENTS)
    assert any("RevokeAuth" in m for m in messages)


def stray(entry_id: str, fault_type_: str) -> PastFix:
    # an incident whose id is not in the fold assignment, so it is no training problem of any fold
    return PastFix(
        entry_id=entry_id,
        source=Source.INCIDENT,
        fault_type=fault_type_,
        target_service="demo-stray",
        summary=f"incident {entry_id} of a synthetic fault",
        fix_command=f"kubectl -n demo patch deployment demo-{entry_id} --type=json",
    )


def test_check_catches_an_incident_that_is_not_a_problem_of_the_pool():
    # a confirmation slice problem has no fold, so it is neither held out nor a training problem
    outsider = stray("conf-scale_pod_zero_social_net-mitigation-1", "ScalePod")
    messages = check_corpus([*build(0), outsider], 0, ASSIGNMENT, INCIDENTS)
    assert any(outsider.entry_id in m and "not a problem of the pool" in m for m in messages)


def test_a_stray_incident_does_not_hide_a_fault_type_with_no_training_incident():
    thinned = [
        e for e in build(0) if not (e.source is Source.INCIDENT and e.fault_type == "ScalePod")
    ]
    messages = check_corpus([*thinned, stray("stray-1", "ScalePod")], 0, ASSIGNMENT, INCIDENTS)
    assert any("ScalePod: no past incident" in m for m in messages)
    assert any("stray-1" in m and "not a problem of the pool" in m for m in messages)


def test_a_confirmation_problem_labelled_as_a_runbook_entry_is_caught_by_check_and_build():
    # the source label does not decide whether an id is a problem id
    disguised = PastFix(
        **{**runbook()[0].__dict__, "entry_id": "conf-scale_pod_zero_social_net-mitigation-1"}
    )
    messages = check_corpus([*build(0), disguised], 0, ASSIGNMENT, INCIDENTS)
    assert any(disguised.entry_id in m and "not a problem of the pool" in m for m in messages)
    with pytest.raises(ValueError, match="not a problem of the pool"):
        build(0, runbook=[*runbook(), disguised])


def test_a_held_out_incident_does_not_hide_a_fault_type_with_no_training_incident():
    held = next(p for p in held_out(1) if fault_type(p) == "ScalePod")
    thinned = [
        e for e in build(1) if not (e.source is Source.INCIDENT and e.fault_type == "ScalePod")
    ]
    messages = check_corpus([*thinned, incident(held)], 1, ASSIGNMENT, INCIDENTS)
    assert any(held in m and "held-out incident" in m for m in messages)
    assert any("ScalePod: no past incident" in m for m in messages)


def test_check_ignores_whitespace_when_it_compares_fixes():
    padded = PastFix(
        **{
            **runbook()[0].__dict__,
            "fix_command": "  " + incident(held_out(2)[0]).fix_command + " ",
        }
    )
    messages = check_corpus([*build(2), padded], 2, ASSIGNMENT, INCIDENTS)
    assert any("held-out" in m for m in messages)


def test_check_returns_a_list_of_strings():
    assert all(isinstance(m, str) for m in check_corpus([], 0, ASSIGNMENT, INCIDENTS))
