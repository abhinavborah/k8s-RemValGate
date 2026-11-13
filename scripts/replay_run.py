"""Replay of one recorded run from its saved files only (E10-S7): no cluster, model or GPU.

The run's files are downloaded from the shared drive (k8s-RemValGate/run-logs/<run id>/) to one
folder first. The script reads:

    <run id>.json                the run manifest
    <run id>.transcript.json     the observation, the diagnosis and the candidates
    <run id>.labels.json         the labels of an oracle run (scripts/run_oracle_labels.py), or
    <run id>.result.json         the result row of a baseline run (scripts/run_baselines_pool.py)

and prints, in order: the injected fault, the observation, the diagnosis, the K candidates with
any refusal reason, the chosen action or the escalation, and the outcome. Every number shown is
read from those files.

One terminal capture and one pod watch cover a whole batch of runs. With --cast and --pods the
script cuts both down to this run, from the manifest's timestamp to the timestamp of the next run
in the folder (or to the end of the recording), and writes <run id>.cast and <run id>.pods.log
beside the manifest. The terminal capture is then played with:

    asciinema play --idle-time-limit 2 --speed 2 <run id>.cast

Usage:

    uv run python scripts/replay_run.py <folder>/<run id>.json [--step] \
        [--cast <batch>.cast] [--pods <batch>.pods.log]
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from remvalgate.harness.folds import fault_type

SUFFIXES = (".transcript.json", ".labels.json", ".result.json")
SHOWN_LINES = 12  # lines of the first observation shown


def _load(manifest_path: Path, suffix: str) -> dict:
    path = manifest_path.with_suffix(suffix)
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def _head(text: str, lines: int = SHOWN_LINES) -> str:
    rows = str(text).rstrip().splitlines()
    more = len(rows) - lines
    return "\n".join(rows[:lines] + ([f"... ({more} more lines)"] if more > 0 else []))


def _candidate(number: int, c: dict) -> str:
    # an oracle label has an outcome; a transcript candidate has only the allow-list verdict
    state = f"refused: {c['rejection']}" if c.get("rejection") else c.get("outcome") or "admitted"
    if c.get("duplicate_of") is not None:
        state += f" (same command as candidate {c['duplicate_of'] + 1})"
    return f"{number}. [{state}] seed {c.get('seed')}\n   {c.get('command')}"


def stages(manifest: dict, transcript: dict, labels: dict, result: dict) -> list[tuple[str, str]]:
    """The six parts of the playback, in order, as (title, text)."""
    problem = manifest["problem_id"]
    sampling = manifest.get("sampling", {})
    fault = (
        f"problem {problem}\nfault type {fault_type(problem)}\n"
        f"namespace {manifest.get('cluster', {}).get('namespace')}\n"
        f"run {manifest['run_id']}\n"
        f"at {manifest.get('timestamp')}, commit {manifest.get('commit')}\n"
        f"system {sampling.get('baseline')}, model {manifest.get('model_name')}"
        f" ({manifest.get('model_version')}), seed {manifest.get('seed')},"
        f" K = {manifest.get('k')}, R = {manifest.get('r')}"
    )

    first = transcript.get("first_observation")
    if first is None:  # baselines 1 and 2 keep a list of observations
        first = "\n".join(str(o) for o in transcript.get("observations", []))
    steps = transcript.get("steps") or []
    observation = _head(first or "(none recorded)")
    if steps:
        observation += f"\n\nthe planner's {len(steps)} turns:"
        observation += "".join(
            f"\n  turn {s.get('turn')}: {s.get('tool')} {json.dumps(s.get('arguments'))[:120]}"
            if s.get("tool")
            else f"\n  turn {s.get('turn')}: no tool call (the final answer, or a reply not parsed)"
            for s in steps
        )

    d = transcript.get("diagnosis")
    diagnosis = (
        f"fault type {d.get('fault_type')} on service {d.get('service')}\n{d.get('reason')}"
        if d
        else f"none (reason: {transcript.get('reason') or result.get('reason')})"
    )

    candidates = labels.get("candidates") or transcript.get("candidates") or []
    if candidates:
        listed = "\n".join(_candidate(i, c) for i, c in enumerate(candidates, 1))
        refused = sum(bool(c.get("rejection")) for c in candidates)
        listed += f"\n\n{len(candidates)} candidates, {refused} refused by the allow-list"
    elif labels:
        listed = "no candidates were sampled, since the plan gave no diagnosis"
    else:
        listed = "this system proposes one command and samples no candidates"

    plan = labels.get("plan") or {}
    command = plan.get("command") or result.get("command") or transcript.get("command")
    stop = result.get("stop") or transcript.get("stop")
    escalation = result.get("escalation") or transcript.get("escalation")
    verdict = transcript.get("verdict") or {}
    refusal = plan.get("rejection") or verdict.get("reason")
    if result.get("allow_list") not in (None, "admitted"):
        refusal = result["allow_list"]
    if escalation or stop == "escalate" or command is None:
        chosen = f"escalated to a human, nothing applied (stop: {stop})"
        chosen += f"\n{escalation}" if escalation else ""
    else:
        chosen = f"{command}\nallow-list: {refusal or 'admitted'} (stop: {stop})"

    outcome = f"outcome {plan.get('outcome') or result.get('outcome') or 'none, nothing applied'}"
    if labels:
        outcome += (
            f"\noracle labels: n = {labels.get('n')}, c = {labels.get('c')},"
            f" any working candidate = {labels.get('any_working_candidate')},"
            f" mixed = {labels.get('mixed')}"
        )
    for e in labels.get("executions") or result.get("executions") or []:
        outcome += (
            f"\nexecuted in {e.get('seconds')} s: {e.get('outcome')},"
            f" fault remains = {e.get('fault_remains')}, harmful = {e.get('harmful')}"
        )

    return [
        ("1. Injected fault", fault),
        ("2. Observation", observation),
        ("3. Diagnosis", diagnosis),
        ("4. Candidates", listed),
        ("5. Chosen action or escalation", chosen),
        ("6. Outcome", outcome),
    ]


def _time(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(UTC).timestamp()


def window(manifest_path: Path) -> tuple[float, float]:
    """From this run's start to the start of the next run in the folder, in epoch seconds."""
    start = _time(json.loads(manifest_path.read_text("utf-8"))["timestamp"])
    later = []
    for path in manifest_path.parent.glob("*.json"):
        # a macOS archive adds a "._" file beside each file
        if path.name.endswith(SUFFIXES) or path.name.startswith("."):
            continue
        stamp = json.loads(path.read_text("utf-8")).get("timestamp")
        if isinstance(stamp, str) and _time(stamp) > start:
            later.append(_time(stamp))
    return start, min(later, default=float("inf"))


def slice_cast(cast: Path, start: float, end: float, out: Path) -> int:
    """Write the events of an asciinema capture that fall in [start, end) to out.

    Version 3 times are intervals since the previous event, version 2 times are seconds since the
    start of the capture. Returns the number of events written.
    """
    with cast.open(encoding="utf-8") as lines:
        header = json.loads(next(lines))
        intervals = header.get("version") == 3
        clock, last, kept = float(header["timestamp"]), None, []
        for line in lines:
            if not line.startswith("["):  # a comment line of version 3
                continue
            event = json.loads(line)
            at = clock + event[0]
            if intervals:
                clock = at
            if start <= at < end:
                event[0] = round(at - (start if last is None or not intervals else last), 3)
                last = at
                kept.append(json.dumps(event))
    header["timestamp"] = int(start)
    out.write_text("\n".join([json.dumps(header), *kept]) + "\n", encoding="utf-8")
    return len(kept)


def slice_pods(pods: Path, start: float, end: float, out: Path) -> int:
    """Write the pod watch lines stamped in [start, end) to out. Returns the number written."""
    kept = []
    for line in pods.read_text("utf-8", errors="replace").splitlines():
        try:
            at = _time(line.split(" ", 1)[0])
        except ValueError:
            continue
        if start <= at < end:
            kept.append(line)
    out.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return len(kept)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifest", type=Path, help="<folder>/<run id>.json")
    parser.add_argument("--step", action="store_true", help="wait for Enter between the parts")
    parser.add_argument("--cast", type=Path, help="the batch's terminal capture, cut to this run")
    parser.add_argument("--pods", type=Path, help="the batch's pod watch log, cut to this run")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    manifest = json.loads(args.manifest.read_text("utf-8"))
    transcript, labels, result = (_load(args.manifest, s) for s in SUFFIXES)
    if not transcript or not (labels or result):
        sys.exit(f"{args.manifest}: the transcript and the labels or result file must be beside it")
    for title, text in stages(manifest, transcript, labels, result):
        print(f"\n=== {title} ===\n{text}", flush=True)
        if args.step:
            input()

    start, end = window(args.manifest)
    if args.pods:
        out = args.manifest.with_suffix(".pods.log")
        print(f"\n{slice_pods(args.pods, start, end, out)} pod watch lines written to {out}")
    if args.cast:
        out = args.manifest.with_suffix(".cast")
        count = slice_cast(args.cast, start, end, out)
        if not count:
            sys.exit(f"{args.cast} has no events in this run's time window")
        print(f"\n{count} terminal events written to {out}")
        print(f"play it with: asciinema play --idle-time-limit 2 --speed 2 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
