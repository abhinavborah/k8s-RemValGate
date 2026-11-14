"""Every injection of the pool runner and the oracle labels waits for mongod on a database fault.

The scripts need an AIOpsLab config file to import, so they are read as syntax trees.
"""

import ast
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
GUARD = "problem_fault_type(problem_id) in DATABASE_TYPES"


def _injecting(path: Path) -> dict[str, ast.AST]:
    """The functions of path that call prob.inject_fault()."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and "prob.inject_fault()" in ast.unparse(node)
    }


def test_every_injection_follows_a_wait_for_mongod_on_a_database_fault_only():
    found = {}
    for script in ("run_oracle_labels.py", "run_baselines_pool.py"):
        found |= {f"{script}:{n}": f for n, f in _injecting(SCRIPTS / script).items()}
    assert set(found) == {
        "run_oracle_labels.py:diagnose",
        "run_oracle_labels.py:execute",
        "run_baselines_pool.py:observe_fault",
        "run_baselines_pool.py:diagnose_without_tools",
    }
    for name, function in found.items():
        guards = [
            n
            for n in function.body
            if isinstance(n, ast.If)
            and ast.unparse(n.test) == GUARD
            and ast.unparse(n.body) == "wait_for_mongod(namespace)"
        ]
        assert len(guards) == 1, f"{name}: no wait for mongod under the database fault guard"
        injections = [
            n.lineno
            for n in ast.walk(function)
            if isinstance(n, ast.Call) and ast.unparse(n) == "prob.inject_fault()"
        ]
        assert guards[0].lineno < min(injections), f"{name}: the wait comes after the injection"
