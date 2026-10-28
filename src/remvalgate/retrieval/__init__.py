"""Retriever interface for the planner and baselines.

A retriever returns ranked past-fix entries (past incidents and runbook entries) for a query.
The evaluation configurations (BRD R4.2) and the development stub return the same shape, so the
planner and baselines can be built before the evaluation retrieval exists.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class Source(StrEnum):
    INCIDENT = "incident"
    RUNBOOK = "runbook"


class RetrieverRefused(Exception):
    pass


@dataclass(frozen=True)
class PastFix:
    entry_id: str
    source: Source
    fault_type: str
    target_service: str
    summary: str
    fix_command: str


@dataclass(frozen=True)
class Hit:
    entry: PastFix
    # similarity in [0, 1], higher is closer
    score: float
    # 1 is the best hit
    rank: int


@runtime_checkable
class Retriever(Protocol):
    def retrieve(self, query: str, problem_id: str, k: int) -> list[Hit]:
        """Return up to k hits for the query, best first, ranked from 1."""
        ...
