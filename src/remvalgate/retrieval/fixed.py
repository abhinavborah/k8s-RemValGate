"""Retrieval configuration (iii), fixed before any evaluation run (E4-S6, gate G5).

fixed.json records the settings of (iii) as a list of dated entries, each with a reason. The last
entry must equal the live settings: configuration_iii refuses to build otherwise, and a unit test
fails, so a change to a setting needs a new entry with the group's reason. Ablation results are
reported and never used to change it (BRD 7.1).

The development problems are served under a key of their own with the synthetic stub corpus, so a
development run touches no evaluation problem and every other problem is refused.
"""

import inspect
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from remvalgate.harness.manifest import Registry, Slice
from remvalgate.planner.loop import plan
from remvalgate.retrieval import PastFix, Source
from remvalgate.retrieval.configs import (
    DENSE_MODEL,
    DEPTH,
    RERANKER_MODEL,
    DenseRerank,
    Encode,
    Score,
    entry_text,
    load_models,
)
from remvalgate.retrieval.stub import STUB_CORPUS

FIXED_PATH = Path(__file__).with_name("fixed.json")
DEV_KEY = -1


def settings() -> dict:
    """The live settings of (iii), in the form fixed.json records them.

    shortcut: the normalised embeddings and the absent query instruction live inside load_models
    and are guarded by review, not by this record; move them to constants if they ever vary.
    """
    fields = ("fault_type", "target_service", "summary", "fix_command")
    template = PastFix("{entry_id}", Source.RUNBOOK, *(f"{{{f}}}" for f in fields))
    return {
        "dense_model": list(DENSE_MODEL),
        "reranker_model": list(RERANKER_MODEL),
        "depth": DEPTH,
        "entry_text": entry_text(template),
        "planner_k": inspect.signature(plan).parameters["k"].default,
    }


def check_fixed(path: str | Path = FIXED_PATH) -> None:
    """Raise ValueError unless every entry has a date and a reason and the last entry's settings
    equal the live settings."""
    entries = json.loads(Path(path).read_text())["entries"]
    if not entries:
        raise ValueError(f"{path}: no entry")
    for n, entry in enumerate(entries, start=1):
        if not str(entry.get("date", "")).strip() or not str(entry.get("reason", "")).strip():
            raise ValueError(f"{path}: entry {n} has no date or no reason")
    recorded, live = entries[-1]["settings"], settings()
    changed = sorted(k for k in recorded.keys() | live.keys() if recorded.get(k) != live.get(k))
    if changed:
        raise ValueError(
            f"retrieval settings {changed} differ from {path}: add an entry with the group's reason"
        )


def configuration_iii(
    assignment: Mapping[str, int],
    corpora: Mapping[int, Sequence[PastFix]],
    *,
    models: tuple[Encode, Score] | None = None,
    device: str | None = None,
) -> DenseRerank:
    """Return (iii) at the fixed settings. models replaces the pinned models in tests; without it
    the models are loaded, which needs the retrieval dependency group."""
    check_fixed()
    encode, score = models or load_models(device)
    return DenseRerank(assignment, corpora, encode=encode, score=score, depth=DEPTH)


def development_retriever(
    registry: Registry, *, models: tuple[Encode, Score] | None = None, device: str | None = None
) -> DenseRerank:
    """(iii) over the stub corpus, serving the registry's development problems only."""
    development = [i for i, s in registry.slices.items() if s is Slice.DEVELOPMENT]
    if not development:
        raise ValueError("the slice registry holds no development problem")
    return configuration_iii(
        dict.fromkeys(development, DEV_KEY),
        {DEV_KEY: STUB_CORPUS},
        models=models,
        device=device,
    )
