"""Checks of the pool runner that need no cluster: the scripts have no unit tests of their own."""

import ast
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_baselines_pool.py"


def _tuple(name: str) -> tuple:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found")


def test_baseline_7_runs_on_the_pool_as_3c_told_to_escalate_when_unsure():
    # #165 runs baseline 7 in one interleaved batch with 3a to 3c
    assert "7" in _tuple("BASELINES") and "7" in _tuple("PLANNER")
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'escalate_when_unsure=baseline == "7"' in text
    assert 'if b in ("2", "3c", "7", *ABLATION):' in text
