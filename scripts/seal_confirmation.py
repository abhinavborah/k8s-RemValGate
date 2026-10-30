"""Seal the confirmation slice once (story E3-S3, gate G1, BRD 7.1 and R3.3).

Writes runs/confirmation_seal.json with each confirmation id's checksum, the AIOpsLab pin, the
commit and the time, and appends one seal touch per problem to runs/touch_log.jsonl. Nothing is
built, injected or run: the checksums are computed from the definitions in the overlay, so no
cluster is needed. It refuses if the slice is already sealed, the tree has uncommitted changes, a
problem has no creation touch or has already run, or a checksum differs from the table in
docs/confirmation_problems.md. Commit the two files right after it, since a pool run needs a
clean tree.

--reseal replaces the record of a sealed slice. Use it only after a group decision recorded on
the sealing issue, such as a new AIOpsLab pin; it still refuses if any problem has run.

    uv run python scripts/seal_confirmation.py [--reseal]
"""

import re
import sys
from pathlib import Path

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.harness.manifest import (
    DEFAULT_REGISTRY_PATH,
    RunRefused,
    Slice,
    load_registry,
    seal_confirmation,
)
from remvalgate.overlay.confirmation import checksum

ROOT = Path(__file__).resolve().parents[1]
TOUCH_LOG = ROOT / "runs" / "touch_log.jsonl"
DOC = ROOT / "docs" / "confirmation_problems.md"


def main(argv: list[str]) -> int:
    if argv not in ([], ["--reseal"]):
        print("usage: seal_confirmation.py [--reseal]")
        return 2
    pin = pinned_commit(ROOT)
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    text = DOC.read_text(encoding="utf-8")
    documented = dict(re.findall(r"^\| `(conf-[^`]+)` \|.*\| `([0-9a-f]{64})` \|$", text, re.M))
    registered = {i for i, s in registry.slices.items() if s is Slice.CONFIRMATION}
    if set(documented) != registered:
        print(f"refused: the ids in {DOC.name} differ from the confirmation ids in slices.json")
        return 1
    changed = sorted(i for i in documented if checksum(i, pin) != documented[i])
    if changed:
        print(f"refused: the checksum in {DOC.name} differs from the definition: {changed}")
        return 1
    try:
        seal = seal_confirmation(registry, TOUCH_LOG, ROOT, pin, checksum, reseal=bool(argv))
    except RunRefused as error:
        print(f"refused: {error}")
        return 1
    for problem_id, value in seal["checksums"].items():
        print(f"sealed {problem_id} {value}")
    print(f"commit {seal['commit']}, AIOpsLab {seal['aiopslab']}, at {seal['sealed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
