"""Story E5-S4 (BRD R5.4): the ablation without the deterministic check, applied offline.

Reads planner runs that sampled candidates, each given as its manifest under runs/manifests/ with
the transcript beside it, applies the allow-list again to every sampled candidate, and reports the
share removed by reason, for the candidates a run without the check would have drawn and for both
draws (src/remvalgate/harness/no_check.py). Nothing is run: no command reaches a cluster, so it
needs neither a cluster nor a model server.

Every given run must be of a different problem, and all of one slice, one model and one sampling
setup, so the pooled counts hold one run per problem of one system. A pool problem's analysis is a
touch: start_run logs a nocheck- run per problem before its transcript is read, so it needs gate
G1 and a clean checkout, as any pool run does. A run refused after a touch, for example by a
verdict the policy no longer gives, keeps that touch, since the transcript was read; the problems
before it keep their results and no summary is written. Development runs log nothing. A manifest
per problem, its result and a pooled summary naming the runs go to runs/manifests/, which git
ignores.

    uv run python scripts/ablate_no_check.py runs/manifests/<run_id>.json [...]
"""

import argparse
import hashlib
import sys
from pathlib import Path

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, RunRefused, load_registry
from remvalgate.harness.no_check import ablate_runs

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "runs" / "manifests"
TOUCH_LOG = ROOT / "runs" / "touch_log.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifests", nargs="+", type=Path, help="planner run manifests")
    args = parser.parse_args()
    try:
        summary, out = ablate_runs(
            args.manifests,
            registry=load_registry(DEFAULT_REGISTRY_PATH),
            policy=load_policy(DEFAULT_POLICY_PATH),
            policy_sha256=hashlib.sha256(DEFAULT_POLICY_PATH.read_bytes()).hexdigest(),
            touch_log=TOUCH_LOG,
            repo=ROOT,
            aiopslab_commit=pinned_commit(ROOT),
            out_dir=OUT_DIR,
        )
    except (ValueError, RunRefused) as error:
        raise SystemExit(f"refused: {error}") from error
    print(f"{summary['problems']} problems: {', '.join(summary['source_run_ids'])}")
    if summary["no_candidates_run_ids"]:
        print(f"sampled nothing: {', '.join(summary['no_candidates_run_ids'])}")
    print(
        f"resampled={summary['resampled']}"
        f" resampled_without_check={summary['resampled_without_check']}"
    )
    for view in ("without_check", "both_draws"):
        t = summary[view]
        print(f"pooled {view}: {t['candidates']} candidates")
        for name in ("admitted", "removed", "no_command"):
            print(f"  {name:<22} {t[name]['count']:>4}  {_pct(t[name]['share'])}")
        for reason, row in t["by_reason"].items():
            print(f"    {reason:<20} {row['count']:>4}  {_pct(row['share'])}")
    print(f"summary {out}")
    return 0


def _pct(share: float | None) -> str:
    return "n/a" if share is None else f"{100 * share:5.1f}%"


if __name__ == "__main__":
    sys.exit(main())
