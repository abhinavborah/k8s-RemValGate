"""Oracle labels of the K candidates of one problem (story E3-S5, R3.5).

scripts/run_oracle_labels.py executes every candidate of baseline 3c once, with a harness reset
between candidates, and classifies each execution with the outcome classifier. This module holds
the logic that needs no cluster: which candidates are near-identical, the labels of a problem, the
coverage estimate for k = 1 to K, the rule for K, and when an injected fault shows in pod status.

A candidate worked when its execution is RECOVERED. APPLIED_HARMFUL never counts as working, even
when the fault is gone, since the classifier puts harm first. A candidate the allow-list rejected,
or one that cannot reach exec_shell unchanged, is never executed and has not worked. A
near-identical candidate is not executed again: it takes the label of the earlier one. The plan's
own command, the action of baseline 3c, is labelled the same way but apart: it is not one of the
K, so it is outside n, c and the problem's flag.
"""

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

from remvalgate.harness.allowlist import Policy, parse_args, tokenize
from remvalgate.harness.harm import Reason, Status
from remvalgate.harness.outcome import Outcome

# overlay.fixes imports no aiopslab, so this module still imports without a cluster
from remvalgate.overlay.fixes import CannotSend

# the outcomes an execution can have; the escalated ones are a system's, never a command's
EXECUTED = frozenset({Outcome.RECOVERED, Outcome.APPLIED_INEFFECTIVE, Outcome.APPLIED_HARMFUL})
DATABASE_TYPES = frozenset({"RevokeAuth", "UserUnregistered"})


def _json_form(value: str | None) -> str | None:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, RecursionError):
        return value
    # a number or a string stays as written, so --replicas=1 and --replicas=1.0 stay apart
    if not isinstance(parsed, dict | list):
        return value
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def dedup_key(command: str, policy: Policy) -> tuple:
    """The form two near-identical commands share: the same kubectl call once the shell's quoting
    is removed.

    Flags compare by name and value in any order, with --flag=value read as --flag value; a flag
    given twice keeps the order of its values. A JSON value, such as a patch body, compares as
    parsed JSON with sorted keys, so spacing and key order do not count, and a list keeps its
    order. Positional arguments and what an exec runs after -- compare token for token, so deploy
    and deployment, -p and --patch, or two values stay apart: only commands that make the same
    call are merged. Meant for admitted commands: one the allow-list's tokenizer refuses raises
    Rejected.
    """
    argv = tokenize(command)
    parsed = parse_args(argv[1:], policy)
    # sorted is stable, so a repeated flag keeps the order of its values
    flags = sorted(((name, _json_form(value)) for name, value in parsed.flags), key=lambda f: f[0])
    return (
        argv[0],
        tuple(parsed.namespaces),
        parsed.all_namespaces,
        tuple(parsed.positionals),
        tuple(flags),
        None if parsed.command is None else tuple(parsed.command),
    )


def originals(commands: Sequence[str | None], policy: Policy) -> list[int | None]:
    """For each command, the index of the first near-identical one: its own index when it is
    executed, an earlier index when it takes that one's label, and None where commands holds None
    (no admitted command)."""
    first: dict[tuple, int] = {}
    return [
        None if command is None else first.setdefault(dedup_key(command, policy), i)
        for i, command in enumerate(commands)
    ]


@dataclass(frozen=True)
class Label:
    # the draw's seed; None for the plan's own command
    seed: int | None
    # None when the reply held no command
    command: str | None
    # why no execution labels it: its rejection, or the reason the command it duplicates could
    # not be sent; None exactly when it has an outcome
    rejection: str | None
    # the seed of the earlier near-identical candidate whose execution labels it
    duplicate_of: int | None
    # the classifier's outcome of the execution that labels it, its own or the one it duplicates
    outcome: Outcome | None

    def __post_init__(self) -> None:
        if (self.outcome is None) == (self.rejection is None):
            raise ValueError("a label has an outcome or a rejection, never both or neither")
        # type() and not membership, so a string read from a file is not taken as an Outcome
        if self.outcome is not None and (
            type(self.outcome) is not Outcome or self.outcome not in EXECUTED
        ):
            raise ValueError(f"not the outcome of an execution: {self.outcome!r}")

    @property
    def worked(self) -> bool:
        return self.outcome is Outcome.RECOVERED


@dataclass(frozen=True)
class ProblemLabels:
    problem_id: str
    # the K sampled candidates in draw order; empty when the plan has no diagnosis
    candidates: tuple[Label, ...]
    # the plan's own command, the action of baseline 3c; None when the plan gave no command
    plan: Label | None

    @property
    def n(self) -> int:
        return len(self.candidates)

    @property
    def c(self) -> int:
        return sum(label.worked for label in self.candidates)

    @property
    def any_working_candidate(self) -> bool:
        """The problem's oracle flag. False with no candidates: none was there to work."""
        return self.c > 0

    @property
    def mixed(self) -> bool:
        """At least one candidate worked and at least one candidate with an outcome did not.

        Candidates with no outcome (rejected, or not sendable) are left out: the gate scores
        admitted candidates only, so a problem whose only failures were rejected offers no choice.
        """
        labelled = [label for label in self.candidates if label.outcome is not None]
        return any(label.worked for label in labelled) and not all(
            label.worked for label in labelled
        )

    def record(self) -> dict:
        """The labels as a labels file holds them, with the counts derived from them."""
        return {
            "problem_id": self.problem_id,
            "candidates": [_label_record(label) for label in self.candidates],
            "plan": None if self.plan is None else _label_record(self.plan),
            "n": self.n,
            "c": self.c,
            "any_working_candidate": self.any_working_candidate,
            "mixed": self.mixed,
        }


def _label_record(label: Label) -> dict:
    return {
        "seed": label.seed,
        "command": label.command,
        "rejection": label.rejection,
        "duplicate_of": label.duplicate_of,
        "outcome": None if label.outcome is None else str(label.outcome),
        "worked": label.worked,
    }


def _label(record: dict) -> Label:
    outcome = record["outcome"]
    return Label(
        seed=record["seed"],
        command=record["command"],
        rejection=record["rejection"],
        duplicate_of=record["duplicate_of"],
        outcome=None if outcome is None else Outcome(outcome),
    )


def read_labels(record: dict) -> ProblemLabels:
    """Read a labels file's labels back. The derived counts are recomputed, not read, and a file
    whose counts disagree with its labels raises ValueError."""
    labels = ProblemLabels(
        problem_id=record["problem_id"],
        candidates=tuple(_label(r) for r in record["candidates"]),
        plan=None if record["plan"] is None else _label(record["plan"]),
    )
    if labels.record() != {k: record.get(k) for k in labels.record()}:
        raise ValueError(f"{labels.problem_id}: the counts in the file disagree with its labels")
    return labels


def label_problem(
    problem_id: str,
    candidates: Sequence[tuple[int, str | None, str | None]],
    plan: tuple[str, str | None] | None,
    run: Callable[[str], Outcome],
    policy: Policy,
) -> ProblemLabels:
    """Execute each distinct admitted command once and label every candidate and the plan's own
    command.

    candidates holds (seed, command, rejection) per draw, in draw order, as sample returns them;
    plan holds the plan's own command and its rejection, or is None when the plan gave no command.
    run executes one command, with the harness reset after it, and returns its outcome; it raises
    CannotSend before anything runs for a command that cannot reach exec_shell unchanged. The
    candidates are executed first, in draw order, then the plan's own command unless it duplicates
    one of them. Any other error from run propagates, so a failed reset stops the labelling.
    """
    items = [*candidates, *([(None, *plan)] if plan is not None else [])]
    admitted = [command if rejection is None else None for _, command, rejection in items]
    first = originals(admitted, policy)
    executions: dict[int, tuple[Outcome | None, str | None]] = {}
    labels = []
    for i, ((seed, command, rejection), source) in enumerate(zip(items, first, strict=True)):
        if source is None:
            labels.append(Label(seed, command, rejection, None, None))
            continue
        if source == i:
            try:
                executions[i] = (run(command), None)
            except CannotSend as error:
                executions[i] = (None, f"cannot be sent unchanged: {error}")
        outcome, why = executions[source]
        duplicate_of = None if source == i else items[source][0]
        labels.append(Label(seed, command, why, duplicate_of, outcome))
    return ProblemLabels(
        problem_id,
        tuple(labels[: len(candidates)]),
        labels[-1] if plan is not None else None,
    )


def pass_at_k(n: int, c: int, k: int) -> Fraction:
    """The unbiased estimate that at least one of k candidates drawn from n works, when c of the n
    work: 1 - C(n - c, k) / C(n, k) (Chen et al. 2021, arXiv 2107.03374, equation 1). It is 1
    when n - c < k.

    Exact, so the K rule can test equality. Raises ValueError unless every argument is an int
    with 0 <= c <= n and 1 <= k <= n.
    """
    for name, value in (("n", n), ("c", c), ("k", k)):
        if type(value) is not int:
            raise ValueError(f"{name} must be an int, got {value!r}")
    if not 0 <= c <= n or not 1 <= k <= n:
        raise ValueError(f"need 0 <= c <= n and 1 <= k <= n, got n={n}, c={c}, k={k}")
    return 1 - Fraction(math.comb(n - c, k), math.comb(n, k))


def coverage(counts: Sequence[tuple[int, int]], k: int) -> Fraction:
    """The estimated number of problems with a working candidate among k: pass@k summed over the
    problems, each given as (n, c). A problem with no candidates (n = 0) adds 0."""
    return sum((pass_at_k(n, c, k) for n, c in counts if n), Fraction(0))


def _size(counts: Sequence[tuple[int, int]]) -> int:
    """The K of the problems with candidates, 0 when none has any."""
    sizes = {n for n, _ in counts if n}
    if len(sizes) > 1:
        raise ValueError(f"the problems with candidates have different K: {sorted(sizes)}")
    return sizes.pop() if sizes else 0


def smallest_k(counts: Sequence[tuple[int, int]]) -> int:
    """The K rule: the smallest k whose coverage equals the coverage at k = K, where K is the n of
    every problem with candidates. Raises ValueError when those n differ or no problem has any."""
    size = _size(counts)
    if not size:
        raise ValueError("no problem has candidates")
    full = coverage(counts, size)
    return next(k for k in range(1, size + 1) if coverage(counts, k) == full)


def summary(problems: Sequence[ProblemLabels]) -> dict:
    """The counts the report needs, over the given problems.

    coverage maps k to the estimated number of problems with a working candidate among k, for k =
    1 to K. Raises ValueError for a repeated problem id, or problems with candidates that have
    different K.
    """
    ids = [p.problem_id for p in problems]
    if len(set(ids)) != len(ids):
        raise ValueError("a problem is labelled more than once")
    counts = [(p.n, p.c) for p in problems]
    size = _size(counts)
    candidates = [label for p in problems for label in p.candidates]
    return {
        "problems": len(problems),
        "k": size,
        "no_candidates": sum(p.n == 0 for p in problems),
        "with_working_candidate": sum(p.any_working_candidate for p in problems),
        "mixed": sum(p.mixed for p in problems),
        "candidates": len(candidates),
        "without_outcome": sum(label.outcome is None for label in candidates),
        "duplicates": sum(label.duplicate_of is not None for label in candidates),
        "coverage": {k: float(coverage(counts, k)) for k in range(1, size + 1)},
        "smallest_k": smallest_k(counts) if size else None,
    }


def fault_shows(fault_type: str, faulty_service: str, status: Mapping[str, Status]) -> bool:
    """Whether an injected fault shows in the pod status, so the status before an action can be
    taken: one taken earlier can read the faulted workload as healthy, and detect_harm would then
    count the fault as harm.

    TargetPortMisconfig changes a Service only and never shows in pods, so it shows at once.
    ScalePod shows when the Deployment has no pods left (parse_pods leaves out terminating pods).
    AssignNonExistentNode shows when its pod is unhealthy. RevokeAuth and UserUnregistered show
    when the client of the database (geo for mongodb-geo, rate for mongodb-rate) runs and is not
    ready, as a client crash-looping on the fault does: AIOpsLab's injector deletes the client's
    pods whatever its script did, so a client pod still being created (PHASE) does not show that
    the fault took.
    Raises ValueError for another fault type.
    """
    if fault_type == "TargetPortMisconfig":
        return True
    if fault_type == "ScalePod":
        return f"Deployment/{faulty_service}" not in status
    if fault_type == "AssignNonExistentNode":
        found = status.get(f"Deployment/{faulty_service}")
        return found is not None and not found.healthy
    if fault_type in DATABASE_TYPES:
        found = status.get(f"Deployment/{faulty_service.removeprefix('mongodb-')}")
        return found is not None and found.reason is Reason.NOT_READY
    raise ValueError(f"unknown fault type {fault_type!r}")
