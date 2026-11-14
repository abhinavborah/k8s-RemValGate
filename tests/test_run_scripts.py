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
    assert "('2', '3c', '3cft', '7', *ABLATION)" in ast.unparse(_function(POOL, "retrievers"))
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


def test_the_fine_tuned_arm_is_3c_with_its_folds_server():
    # #159: 3cft shares 3c's retrieval and planner path and differs only in the served model
    assert "3cft" in _tuple("BASELINES") and "3cft" in _tuple("PLANNER")
    assert "'3cft'" in ast.unparse(_function(POOL, "retrievers"))
    run_one = _function(POOL, "run_one")
    text = ast.unparse(run_one)
    # the fold's server and record are chosen before start_run, so a wrong server stops first
    assert text.index("args.folds.get(problem_id, args.dev_fold)") < text.index("start_run(")
    assert text.index("build_for(MODEL, server['base_url'], server['served_build'], fold, key)") < (
        text.index("start_run(")
    )
    assert "base_url=server['base_url']" in text
    # the manifest records which fold's adapter served the run
    assert "'fold': fold" in text
    main = ast.unparse(_function(POOL, "main"))
    assert "3cft on development problems needs --dev-fold" in main
    assert "3cft needs --fold-servers" in main


def test_fold_servers_needs_all_four_folds():
    text = ast.unparse(_function(POOL, "fold_servers"))
    assert "[0, 1, 2, 3]" in text


def test_the_fine_tuned_labels_never_sample_a_problem_with_its_own_adapter():
    main = ast.unparse(_function(LABELS, "main"))
    # --fold needs a served build and refuses a pool problem of another fold
    assert "--fold labels a served fine-tuned planner, so it needs --served-build" in main
    assert "folds[i] != args.fold" in main and "not in fold" in main
    # the arm and the fold are recorded, and the labels go to their own folder and run ids
    assert "'baseline': arm" in main and "'fold': args.fold" in main
    assert "FT_OUT_DIR, f'oracle3cft-fold{args.fold}'" in main
    assert "OUT_DIR, 'oracle'" in main


def test_the_labels_without_retrieval_differ_from_3cft_in_retrieval_only():
    # #34: --no-retrieval needs --fold, uses 3a's configuration (i) and keeps its own folder and ids
    main = ast.unparse(_function(LABELS, "main"))
    assert "--no-retrieval runs the fine-tuned planner, so it needs --fold" in main
    assert "(pool_untuned_retriever if on_pool else untuned_retriever)('3a', registry)" in main
    assert "NORET_OUT_DIR, f'oracle3cftnoret-fold{args.fold}'" in main
    # recorded in the manifest's sampling and in the transcript's config
    assert main.count("'retrieval': retrieval") == 2
    # the seed, K, server and record are the same arguments as without the flag
    assert "build_for(args.model, args.base_url, args.served_build, args.fold, key)" in main


def test_confirmation_and_singleton_run_2_and_3c_over_the_held_out_corpus():
    # in no fold, so no fine-tuned build and no fold corpus (BRD 1.20)
    held_out = next(
        n
        for n in _tree(POOL).body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "HELD_OUT_BASELINES"
    )
    assert "'2', '3c'" in ast.unparse(held_out.value)
    source = ast.unparse(_function(POOL, "retrievers"))
    assert "held_out_retriever(registry)" in source
    main = ast.unparse(_function(POOL, "main"))
    # the slice is opened only after every check of the run, by the one call
    assert main.index("open_confirmation(") > main.index("prometheus_port_forward(")
