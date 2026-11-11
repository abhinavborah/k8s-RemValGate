"""The extrinsic RAG ablation (E4-S4, BRD R4.4): diagnosis accuracy and single-action recovery
of the untuned planner in retrieval configurations (i) no retrieval, (ii) dense and (iii) dense
plus reranker, baselines 3a, 3b and 3c, read from the baseline-only run with no new rollout.

A diagnosis is correct when its fault type and its service both match the injected fault, as
proposal version 2 defines diagnosis accuracy ("the share of problems whose diagnosed fault type
and target service match the injected fault"). Fault-type accuracy alone is also reported. The
service is compared ignoring case and surrounding space. For RevokeAuth and UserUnregistered the
target is the MongoDB deployment, mongodb-<client>, and the client service <client> also counts,
as AIOpsLab's own localization eval of these two faults accepts either. A problem with no
diagnosis (an escalation, the turn cap or a reply that never parsed) counts as wrong, so every
rate is over the problems run. Single-action recovery is the outcome RECOVERED.

These rules were fixed before the diagnoses of the runs were tabulated; one diagnosis was read
only to learn the shape of the record.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from remvalgate.harness.outcome import Outcome
from remvalgate.harness.stats import bonett_price, mcnemar_midp, wilson

# fault types whose target is a MongoDB deployment named mongodb-<client>
MONGODB_TYPES = frozenset({"RevokeAuth", "UserUnregistered"})
CONFIGURATIONS = {"3a": "(i) no retrieval", "3b": "(ii) dense", "3c": "(iii) dense plus reranker"}
FIELDS = ("fault_type_correct", "diagnosis_correct", "recovered")


@dataclass(frozen=True)
class Row:
    problem_id: str
    has_diagnosis: bool
    fault_type_correct: bool
    diagnosis_correct: bool
    recovered: bool


def service_matches(named: str, target: str, fault_type: str) -> bool:
    """Whether the named service is the target, or for a MongoDB fault the target's client."""
    accepted = {target.strip().lower()}
    if fault_type in MONGODB_TYPES:
        accepted.add(target.strip().lower().removeprefix("mongodb-"))
    return named.strip().lower() in accepted


def score(
    problem_id: str,
    diagnosis: Mapping[str, str] | None,
    outcome: Outcome,
    true_type: str,
    true_service: str,
) -> Row:
    """One problem's row. diagnosis is the planner's {fault_type, service, reason}, or None."""
    if diagnosis is None:
        return Row(problem_id, False, False, False, outcome is Outcome.RECOVERED)
    type_ok = diagnosis["fault_type"] == true_type
    service_ok = service_matches(diagnosis["service"], true_service, true_type)
    return Row(problem_id, True, type_ok, type_ok and service_ok, outcome is Outcome.RECOVERED)


def _check_once(rows: Sequence[Row], what: str) -> None:
    ids = [r.problem_id for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{what}: a problem has more than one row")


def summary(rows: Sequence[Row]) -> dict:
    """Counts, rates and 95 percent Wilson intervals of each field over the rows.

    Raises ValueError for no rows or a problem with more than one row.
    """
    if not rows:
        raise ValueError("no rows")
    _check_once(rows, "summary")
    n = len(rows)
    out: dict = {"n": n, "with_diagnosis": sum(r.has_diagnosis for r in rows)}
    for field in FIELDS:
        x = sum(getattr(r, field) for r in rows)
        out[field] = {"count": x, "rate": x / n, "wilson95": list(wilson(x, n))}
    return out


def paired(first: Sequence[Row], second: Sequence[Row], field: str) -> dict:
    """Paired comparison of one field on the problems both configurations ran.

    n12 counts problems where only the first is correct, n21 where only the second is. Reports
    the McNemar mid-p value and the Bonett-Price interval of the difference first minus second.
    Raises ValueError for an unknown field, a problem with more than one row, or no shared problem.
    """
    if field not in FIELDS:
        raise ValueError(f"unknown field {field!r}")
    _check_once(first, "first")
    _check_once(second, "second")
    a = {r.problem_id: getattr(r, field) for r in first}
    b = {r.problem_id: getattr(r, field) for r in second}
    shared = sorted(a.keys() & b.keys())
    if not shared:
        raise ValueError("no problem is shared")
    n12 = sum(a[p] and not b[p] for p in shared)
    n21 = sum(b[p] and not a[p] for p in shared)
    return {
        "n": len(shared),
        "first": sum(a[p] for p in shared),
        "second": sum(b[p] for p in shared),
        "n12": n12,
        "n21": n21,
        "mcnemar_midp": mcnemar_midp(n12, n21),
        "difference_bonett_price95": list(bonett_price(n12, n21, len(shared))),
        "not_shared": sorted(a.keys() ^ b.keys()),
    }
