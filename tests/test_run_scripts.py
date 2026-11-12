"""Checks of the pool runner that need no cluster: the scripts have no unit tests of their own, and
they need an AIOpsLab config file to import, so they are read as syntax trees."""

import ast
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
POOL = SCRIPTS / "run_baselines_pool.py"
LABELS = SCRIPTS / "run_oracle_labels.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _tuple(name: str) -> tuple:
    for node in _tree(POOL).body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found")


def _function(path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path.name}")


def _keyword(function: ast.AST, callee: str, name: str) -> str:
    """The value, as source, that function passes as keyword name in its one call of callee,
    direct or through asyncio.to_thread(callee, ...)."""
    calls = [
        n
        for n in ast.walk(function)
        if isinstance(n, ast.Call)
        and callee
        in (getattr(n.func, "id", None), getattr(n.args[0] if n.args else None, "id", None))
    ]
    assert len(calls) == 1, f"{len(calls)} calls of {callee}"
    values = [k.value for k in calls[0].keywords if k.arg == name]
    assert values, f"{callee} is called without {name}"
    return ast.unparse(values[0])


def test_baseline_7_runs_on_the_pool_as_3c_told_to_escalate_when_unsure():
    # #165 runs baseline 7 in one interleaved batch with 3a to 3c
    assert "7" in _tuple("BASELINES") and "7" in _tuple("PLANNER")
    # 3c's retrieval configuration (iii)
    assert "('2', '3c', '7', *ABLATION)" in ast.unparse(_function(POOL, "retrievers"))
    # run_one passes baseline == "7" to diagnose, and diagnose hands its flag on to plan
    assert _keyword(_function(POOL, "run_one"), "diagnose", "escalate_when_unsure") == (
        "baseline == '7'"
    )
    assert _keyword(_function(LABELS, "diagnose"), "plan", "escalate_when_unsure") == (
        "escalate_when_unsure"
    )


def test_the_oracle_labels_never_escalate():
    # the labelling run calls diagnose without the flag, so it keeps the default False
    labels = _function(LABELS, "diagnose")
    assert labels.args.args[-1].arg == "escalate_when_unsure"
    assert ast.literal_eval(labels.args.defaults[-1]) is False
    calls = [
        n
        for n in ast.walk(_function(LABELS, "main"))
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "diagnose"
    ]
    assert len(calls) == 1
    assert "escalate_when_unsure" not in {k.arg for k in calls[0].keywords}


def test_a_pool_result_records_the_flag_and_why_a_plan_has_no_command():
    text = ast.unparse(_function(POOL, "run_one"))
    assert "'stop': stop" in text and "'escalation': escalation" in text
    assert "'escalate_when_unsure': baseline == '7'" in text
