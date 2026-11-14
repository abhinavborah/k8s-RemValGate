"""Retrieval configuration (iii), fixed before any evaluation run (E4-S6, gate G5).

fixed.json records the settings of (iii) as a list of entries, each with a UTC timestamp and a
reason. The last entry must equal the live settings: configuration_iii refuses to build otherwise,
and a unit test fails, so a change to a setting needs a new entry with the group's reason. Ablation
results are reported and never used to change it (BRD 7.1). Run code builds (iii) only through
configuration_iii, never DenseRerank directly, whose depth and models are arguments the record
does not see; a test checks that no file under scripts/ or src/ names DenseRerank except configs.py,
this module and the model smoke check. The other configurations stay free for baselines and metrics.

The development problems are served under a key of their own with the synthetic stub corpus, so a
development run touches no evaluation problem and every other problem is refused. The stub corpus
has no more entries than the fixed depth, so there the reranker scores every entry and the dense
stage changes nothing: a development run checks the wiring, not the two-stage ranking.

The pool problems are served by pool_retriever, each from the corpus of the fold that holds it
out (R4.1), built from the pool incident records and the runbook entries (E4-S8).
"""

import inspect
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from remvalgate.harness.folds import pool_folds
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
from remvalgate.retrieval.corpus import build_corpus
from remvalgate.retrieval.pool_corpus import RUNBOOK, pool_incidents
from remvalgate.retrieval.stub import STUB_CORPUS

FIXED_PATH = Path(__file__).with_name("fixed.json")
DEV_KEY = -1
# the corpus key of the problems outside the pool (confirmation slice and singleton types)
HELD_OUT_KEY = -2
# the touch log's format, so timestamps compare as strings
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"


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
    """Raise ValueError unless every entry is an object with a UTC timestamp, a reason and
    settings, the timestamps rise, and the last entry's settings equal the live settings."""
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = record.get("entries") if isinstance(record, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: no entry")
    previous = ""
    for n, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or not isinstance(entry.get("settings"), dict):
            raise ValueError(f"{path}: entry {n} is not an object with settings")
        if not str(entry.get("reason", "")).strip():
            raise ValueError(f"{path}: entry {n} has no reason")
        stamp = entry.get("timestamp")
        try:
            datetime.strptime(str(stamp), TIMESTAMP)
        except ValueError:
            raise ValueError(
                f"{path}: entry {n} timestamp {stamp!r} is not like {TIMESTAMP}"
            ) from None
        if stamp <= previous:
            raise ValueError(f"{path}: entry {n} is not later than the entry before it")
        previous = stamp
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


def pool_retriever(
    registry: Registry, *, models: tuple[Encode, Score] | None = None, device: str | None = None
) -> DenseRerank:
    """(iii) over the fold corpora of the pool, serving the registry's pool problems only: each
    problem retrieves from the corpus of the fold that holds it out, so it never retrieves its
    own record. build_corpus refuses a fold with a leak."""
    assignment = pool_folds(registry)
    incidents = pool_incidents(registry)
    corpora = {
        fold: build_corpus(fold, assignment=assignment, incidents=incidents, runbook=RUNBOOK)
        for fold in sorted(set(assignment.values()))
    }
    return configuration_iii(assignment, corpora, models=models, device=device)


def held_out_retriever(
    registry: Registry, *, models: tuple[Encode, Score] | None = None, device: str | None = None
) -> DenseRerank:
    """(iii) for the confirmation slice and the singleton types, which are in no fold: every pool
    incident, then every runbook entry, as the deployed system would retrieve (BRD 1.20). The
    corpus holds no record of these problems, since none was authored for them (log_authoring)."""
    held_out = [i for i, s in registry.slices.items() if s in (Slice.CONFIRMATION, Slice.SINGLETON)]
    if not held_out:
        raise ValueError("the slice registry holds no confirmation or singleton problem")
    incidents = sorted(pool_incidents(registry), key=lambda e: e.entry_id)
    corpus = [*incidents, *sorted(RUNBOOK, key=lambda e: e.entry_id)]
    leaked = sorted({e.entry_id for e in corpus} & set(held_out))
    if leaked:
        raise ValueError(f"the held-out corpus holds records of held-out problems: {leaked}")
    return configuration_iii(
        dict.fromkeys(held_out, HELD_OUT_KEY),
        {HELD_OUT_KEY: corpus},
        models=models,
        device=device,
    )
