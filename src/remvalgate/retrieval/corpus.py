"""The retrieval corpus of one fold (E4-S1, R4.1).

A fold's corpus holds the recorded incidents of its training problems and the runbook entries,
and nothing from the problems the fold holds out, so no evaluation incident can retrieve itself
or its own recorded fix. check_corpus is the isolation test: it reports a held-out incident in the
corpus, an incident that is not a problem of the pool, a corpus entry whose fix is the recorded
fix of a held-out incident, and a fault type of the pool with no past incident among the training
incidents. build_corpus builds and refuses a leak.

The incident records are supplied by the caller as PastFix entries whose entry_id is the problem
id. Runbook entries are general steps for a fault type and carry an id of their own. The pool
needs at least two instances of every fault type for the last check to hold in every fold.
"""

from collections.abc import Mapping, Sequence

from remvalgate.harness.folds import fault_type
from remvalgate.retrieval import PastFix, Source


def _validate(
    fold: int,
    assignment: Mapping[str, int],
    incidents: Sequence[PastFix],
    runbook: Sequence[PastFix],
) -> None:
    if fold not in set(assignment.values()):
        raise ValueError(
            f"fold {fold!r} is not one of the folds {sorted(set(assignment.values()))}"
        )
    for entry in incidents:
        if entry.source is not Source.INCIDENT:
            raise ValueError(f"{entry.entry_id}: an incident entry must have source incident")
        if entry.entry_id not in assignment:
            raise ValueError(f"{entry.entry_id}: the incident is not in the fold assignment")
        if fault_type(entry.entry_id) != entry.fault_type:
            raise ValueError(
                f"{entry.entry_id}: labelled {entry.fault_type!r} but its id reads as fault type "
                f"{fault_type(entry.entry_id)!r}"
            )
    for entry in runbook:
        if entry.source is not Source.RUNBOOK:
            raise ValueError(f"{entry.entry_id}: a runbook entry must have source runbook")
        if entry.entry_id in assignment:
            raise ValueError(f"{entry.entry_id}: a runbook entry cannot reuse a problem id")
    ids = [e.entry_id for e in [*incidents, *runbook]]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate entry ids among the incidents and the runbook entries")


def check_corpus(
    corpus: Sequence[PastFix],
    fold: int,
    assignment: Mapping[str, int],
    incidents: Sequence[PastFix],
) -> list[str]:
    """Return one message per leak or gap; an empty list means the corpus is isolated.

    incidents holds the recorded incident of every problem, held out or not, so the fixes of the
    held-out ones are known. Fixes are compared after stripping surrounding whitespace.
    """
    held_out = {problem_id for problem_id, f in assignment.items() if f == fold}
    held_fixes = {e.fix_command.strip() for e in incidents if e.entry_id in held_out}
    messages = []
    for entry in corpus:
        if entry.entry_id in held_out:
            messages.append(f"{entry.entry_id}: a held-out incident is in the corpus")
        elif entry.source is Source.INCIDENT and entry.entry_id not in assignment:
            messages.append(f"{entry.entry_id}: an incident that is not a problem of the pool")
        if entry.entry_id not in held_out and entry.fix_command.strip() in held_fixes:
            messages.append(f"{entry.entry_id}: its fix is the recorded fix of a held-out incident")
    # the fault type is read from the id of a training incident, not from its label, and an
    # incident outside the assignment does not count
    present = {
        fault_type(e.entry_id)
        for e in corpus
        if e.source is Source.INCIDENT and e.entry_id in assignment
    }
    for type_ in sorted({fault_type(problem_id) for problem_id in assignment} - present):
        messages.append(f"{type_}: no past incident of this fault type in the corpus")
    return messages


def build_corpus(
    fold: int,
    *,
    assignment: Mapping[str, int],
    incidents: Sequence[PastFix],
    runbook: Sequence[PastFix],
) -> list[PastFix]:
    """Return the corpus of one fold: its training incidents, then every runbook entry.

    Each part is sorted by entry id, so the result does not depend on the input order. Raises
    ValueError for an unknown fold, a duplicate entry id, an incident outside the assignment, an
    entry with the wrong source or fault type, a runbook entry that reuses a problem id, and any
    leak or gap that check_corpus reports.
    """
    _validate(fold, assignment, incidents, runbook)
    training = sorted(
        (e for e in incidents if assignment[e.entry_id] != fold), key=lambda e: e.entry_id
    )
    corpus = [*training, *sorted(runbook, key=lambda e: e.entry_id)]
    messages = check_corpus(corpus, fold, assignment, incidents)
    if messages:
        raise ValueError("; ".join(messages))
    return corpus
