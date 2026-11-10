"""The planner models a run may serve, and the flag that chooses one."""

import argparse

MODEL = "qwen/qwen3.5-4b"
# the larger planner of the BRD's risk fallback (#154); only models of the family the judge's
# record names are listed, since that family is fixed in scripts/run_planner_dev.py
MODELS = (MODEL, "qwen/qwen3.5-9b")


def add_model_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", choices=MODELS, default=MODEL, help="the served planner model")
