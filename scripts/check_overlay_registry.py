"""Hand check for story E1-S7: an overlay entry appears in a new Orchestrator's registry.

Needs aiopslab/config.yml and a kubeconfig, so it is run by hand and never by CI or the tests.
It injects no problem and never instantiates one.

    uv run python scripts/check_overlay_registry.py
"""

import sys

from remvalgate.overlay import new_orchestrator

# no task word in the id, so it never joins a detection, localization, analysis or mitigation count
TEST_ID = "remvalgate_overlay_selftest"


def _never_called():
    raise AssertionError("the self test problem must never be instantiated")


def main() -> int:
    # an empty overlay, so the shipped overlay problems do not shift the count
    baseline = new_orchestrator({}).probs.get_problem_ids()
    orch = new_orchestrator({TEST_ID: _never_called})
    ids = orch.probs.get_problem_ids()
    fresh = new_orchestrator({}).probs.get_problem_ids()
    try:
        new_orchestrator({baseline[0]: _never_called})
    except ValueError:
        pass
    else:
        raise AssertionError("an upstream id was not refused")

    assert TEST_ID in ids, "overlay entry missing from the new Orchestrator"
    assert len(ids) == len(baseline) + 1, "more than the one overlay entry was added"
    assert TEST_ID not in fresh, "the entry leaked into another Orchestrator"
    print(f"upstream problems: {len(baseline)}, with overlay: {len(ids)}")
    print(f"{TEST_ID} in registry: True, in a fresh Orchestrator: False")
    print("an id upstream already has is refused: True")
    print("result: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
