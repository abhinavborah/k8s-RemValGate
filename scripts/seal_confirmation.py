"""Seal the confirmation slice once (story E3-S3, gate G1, BRD 7.1 and R3.3).

Writes runs/confirmation_seal.json with each confirmation id's checksum, the AIOpsLab pin, the
commit and the time, and appends one seal touch per problem to runs/touch_log.jsonl. Nothing is
built, injected or run: the checksums are computed from the definitions in the overlay, so no
cluster is needed. It refuses if the slice is already sealed, the tree has uncommitted changes, a
problem has no creation touch or has already run, or a checksum differs from the table in
docs/confirmation_problems.md. Commit the two files right after it, since a pool run needs a
clean tree.

    uv run python scripts/seal_confirmation.py
"""

import re
import sys
from pathlib import Path

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, load_registry, seal_confirmation
from remvalgate.overlay.confirmation import checksum

ROOT = Path(__file__).resolve().parents[1]
TOUCH_LOG = ROOT / "runs" / "touch_log.jsonl"
DOC = ROOT / "docs" / "confirmation_problems.md"


def main() -> int:
    pin = pinned_commit(ROOT)
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    text = DOC.read_text(encoding="utf-8")
    documented = dict(re.findall(r"^\| `(conf-[^`]+)` \|.*\| `([0-9a-f]{64})` \|$", text, re.M))
    computed = {i: checksum(i, pin) for i in documented}
    if documented != computed or set(documented) != {
        i for i, s in registry.slices.items() if s == "confirmation"
    }:
        raise AssertionError(f"the checksums in {DOC.name} do not match the definitions")
    seal = seal_confirmation(registry, TOUCH_LOG, ROOT, pin, checksum)
    for problem_id, value in seal["checksums"].items():
        print(f"sealed {problem_id} {value}")
    print(f"commit {seal['commit']}, AIOpsLab {seal['aiopslab']}, at {seal['sealed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
