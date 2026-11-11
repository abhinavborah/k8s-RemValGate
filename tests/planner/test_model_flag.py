import argparse
from pathlib import Path

import pytest

from remvalgate.planner.models import add_model_argument, check_pool_model

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_model_argument(parser)
    return parser


def test_default_is_the_4b_so_earlier_commands_are_unchanged():
    assert _parser().parse_args([]).model == "qwen/qwen3.5-4b"


def test_the_9b_is_accepted():
    args = _parser().parse_args(["--model", "qwen/qwen3.5-9b"])
    assert args.model == "qwen/qwen3.5-9b"


def test_another_model_is_refused():
    # the judge's family is fixed in the script, so another family must not get through
    with pytest.raises(SystemExit):
        _parser().parse_args(["--model", "gemma"])


@pytest.mark.parametrize("script", ["run_planner_dev.py", "run_oracle_labels.py"])
def test_each_script_uses_the_flag(script):
    # read as text: the scripts need an AIOpsLab config file to import
    text = (SCRIPTS / script).read_text(encoding="utf-8")
    assert "add_model_argument(parser)" in text
    assert "model=args.model" in text
    assert "model_name=args.model" in text
    assert "model=MODEL" not in text


def test_the_9b_is_refused_on_the_pool():
    # a pool run logs an evaluation-set touch, so another model there is a separate decision
    with pytest.raises(ValueError):
        check_pool_model("qwen/qwen3.5-9b", runs_pool=True)


def test_the_default_runs_on_the_pool_and_the_9b_on_development():
    check_pool_model("qwen/qwen3.5-4b", runs_pool=True)
    check_pool_model("qwen/qwen3.5-9b", runs_pool=False)
