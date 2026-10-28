"""Hand check for story E3-S9: validate each development problem with a known-fix command.

Needs aiopslab/config.yml, the kind cluster and Social Network deployed and Ready (README,
Deploy and health check), so it is run by hand and never by CI or the tests. No system or model
runs and the LLM judge must be off. For each development problem in the slice registry it:

1. appends a creation touch to runs/touch_log.jsonl,
2. builds the problem through the overlay registry and injects its fault,
3. checks that the mitigation eval fails, so the fault took effect,
4. runs the known-fix kubectl command, and
5. checks that the mitigation eval passes.

After each problem, passed or not, the fault is recovered with AIOpsLab's own recover step;
if a check fails, the script stops.

    uv run python scripts/validate_dev_problems.py
"""

import os
import subprocess
import sys
from pathlib import Path

from aiopslab.paths import config

from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, Slice, load_registry, log_creation
from remvalgate.overlay import new_orchestrator
from remvalgate.overlay.fixes import FIXES

NS = "test-social-network"
TOUCH_LOG = Path(__file__).resolve().parents[1] / "runs" / "touch_log.jsonl"
KUBECTL = ["kubectl", "--context", "kind-kind", "-n", NS]


def _eval(prob, settle: str) -> bool:
    # the settle window only delays a pass, so the check before the fix needs none
    os.environ["AIOPSLAB_MITIGATION_SETTLE_SECONDS"] = settle
    return prob.eval("", [], 0.0)["success"]


def validate(problem_id: str, probs, registry) -> None:
    entry = log_creation(problem_id, registry, TOUCH_LOG)
    print(f"== {problem_id}: creation touch {entry['run_id']}")
    prob = probs.get_problem_instance(problem_id)
    try:
        # inside the try: a partial AssignNonExistentNode inject has deleted the deployment
        prob.inject_fault()
        if _eval(prob, "0"):
            raise AssertionError("the eval passes with the fault injected")
        for args in FIXES[problem_id]:
            subprocess.run(KUBECTL + args, check=True)
        if not _eval(prob, "120"):
            raise AssertionError("the eval fails after the known fix")
    finally:
        # also after a pass, so the next problem starts from AIOpsLab's recovered state
        prob.recover_fault()
    print(f"{problem_id}: PASS")


def main() -> int:
    # AIOpsLab's injectors run kubectl in the current context, the fixes above in kind-kind
    current = subprocess.run(
        ["kubectl", "config", "current-context"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if current != "kind-kind":
        raise AssertionError(f"current kubectl context is {current!r}, expected 'kind-kind'")
    # with it on, every eval would call the LLM judge
    if config.get("qualitative_eval"):
        raise AssertionError("set qualitative_eval: false in aiopslab/config.yml")
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    dev = [i for i, s in registry.slices.items() if s is Slice.DEVELOPMENT]
    if sorted(dev) != sorted(FIXES):
        raise AssertionError("the known fixes do not match the development slice")
    probs = new_orchestrator().probs
    for problem_id in dev:
        validate(problem_id, probs, registry)
    print("result: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
