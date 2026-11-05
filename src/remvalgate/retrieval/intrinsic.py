"""Intrinsic retrieval metrics of the four configurations (E4-S3, R4.3).

One fixed query per pool incident (docs/pool_queries.md) is ranked by each configuration over the
corpus of the fold that holds the problem out. Hit@1, Hit@3, MRR and Recall@3 are reported with
counts per configuration, for all 16 queries and apart for the database problems and the
TargetPortMisconfig problems, whose queries docs/pool_queries.md singles out. The results are
descriptive and never change configuration (iii).

Two relevance labels: the fault type, and the stricter fault type plus target service. An entry's
fault type and service are compared with those of the query's own incident record. A query with
no relevant entry counts as 0 in Hit@k and MRR and is left out of Recall@3, which is undefined
for it, so queries_with_relevant says how many queries a Recall@3 rests on.

Recall@3 cannot reach 1 for a query with more than 3 relevant entries, so its ceiling is reported
beside it.

The saturation rule was committed before any query was scored: under the fault-type label, over
all queries, the labels saturate if (ii), (iii) and (iv) tie on Hit@3 or any of them reaches every
query.
"""

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

from remvalgate.harness.folds import pool_folds
from remvalgate.harness.manifest import Registry
from remvalgate.harness.metrics import hit_at_k, mrr, recall_at_k
from remvalgate.retrieval import PastFix, Retriever
from remvalgate.retrieval.configs import Dense, Encode, HybridRerank, NoRetrieval, Score
from remvalgate.retrieval.corpus import build_corpus
from remvalgate.retrieval.fixed import configuration_iii
from remvalgate.retrieval.pool_corpus import RUNBOOK, pool_incidents

CONFIGURATIONS = ("i", "ii", "iii", "iv")
# the models read at most this many tokens of a query
MODEL_TOKENS = 512
_DATABASE = frozenset({"RevokeAuth", "UserUnregistered"})
GROUPS: dict[str, Callable[[PastFix], bool]] = {
    "all": lambda q: True,
    "database": lambda q: q.fault_type in _DATABASE,
    "target_port": lambda q: q.fault_type == "TargetPortMisconfig",
}


def same_type(query: PastFix, entry: PastFix) -> bool:
    return entry.fault_type == query.fault_type


def same_type_and_service(query: PastFix, entry: PastFix) -> bool:
    return same_type(query, entry) and entry.target_service == query.target_service


LABELS: dict[str, Callable[[PastFix, PastFix], bool]] = {
    "fault_type": same_type,
    "type_and_service": same_type_and_service,
}

Results = Sequence[tuple[PastFix, Sequence[PastFix]]]


def read_queries(paths: Iterable[str | Path]) -> dict[str, str]:
    """Problem id to query, from the query files of scripts/record_pool_queries.py. A problem
    recorded twice is refused, since the query must be the one fixed query."""
    queries: dict[str, str] = {}
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["problem_id"] in queries:
                raise ValueError(f"{row['problem_id']}: more than one recorded query")
            queries[row["problem_id"]] = row["query"]
    return queries


def score(
    results: Results,
    relevant: Callable[[PastFix, PastFix], bool],
    total_relevant: Callable[[PastFix], int],
) -> dict:
    """Counts and rates of one configuration under one label over one group of queries."""
    n = len(results)

    def hit(k: int) -> dict:
        rate = hit_at_k(results, relevant, k)
        return {"count": round(rate * n), "rate": rate}

    scorable = [r for r in results if total_relevant(r[0]) > 0]
    recall = None
    if scorable:
        totals = [total_relevant(q) for q, _ in scorable]
        recall = {
            "found": sum(relevant(q, e) for q, ranked in scorable for e in ranked[:3]),
            "relevant": sum(totals),
            "rate": recall_at_k(scorable, relevant, total_relevant, 3),
            "ceiling": sum(min(3, t) / t for t in totals) / len(totals),
        }
    return {
        "queries": n,
        "queries_with_relevant": len(scorable),
        "hit_at_1": hit(1),
        "hit_at_3": hit(3),
        "mrr": mrr(results, relevant),
        "recall_at_3": recall,
    }


def saturation(configurations: Mapping[str, Mapping]) -> dict:
    """The committed rule, read from a report's configurations."""
    counts = {
        c: configurations[c]["fault_type"]["all"]["hit_at_3"]["count"] for c in ("ii", "iii", "iv")
    }
    n = configurations["ii"]["fault_type"]["all"]["queries"]
    return {
        "hit_at_3": counts,
        "queries": n,
        "saturated": len(set(counts.values())) == 1 or n in counts.values(),
    }


def build_configurations(
    assignment: Mapping[str, int],
    corpora: Mapping[int, Sequence[PastFix]],
    models: tuple[Encode, Score],
) -> dict[str, Retriever]:
    encode, rerank = models
    return {
        "i": NoRetrieval(assignment, corpora),
        "ii": Dense(assignment, corpora, encode=encode),
        "iii": configuration_iii(assignment, corpora, models=models),
        "iv": HybridRerank(assignment, corpora, encode=encode, score=rerank),
    }


def evaluate(queries: Mapping[str, str], registry: Registry, models: tuple[Encode, Score]) -> dict:
    """Rank every query with every configuration and report the metrics. Raises ValueError unless
    queries holds exactly the pool's problems."""
    assignment = pool_folds(registry)
    if queries.keys() != assignment.keys():
        difference = sorted(queries.keys() ^ assignment.keys())
        raise ValueError(f"queries and the pool differ on {difference}")
    records = pool_incidents(registry)
    corpora = {
        fold: build_corpus(fold, assignment=assignment, incidents=records, runbook=RUNBOOK)
        for fold in sorted(set(assignment.values()))
    }
    retrievers = build_configurations(assignment, corpora, models)

    # ids are ordered as the pool is, so the report does not depend on the order of the files
    ranked = {
        c: [
            (
                q,
                [
                    h.entry
                    for h in retriever.retrieve(
                        queries[q.entry_id], q.entry_id, len(corpora[assignment[q.entry_id]])
                    )
                ],
            )
            for q in records
        ]
        for c, retriever in retrievers.items()
    }

    def totals(relevant) -> Callable[[PastFix], int]:
        return lambda q: sum(relevant(q, e) for e in corpora[assignment[q.entry_id]])

    configurations = {
        c: {
            label: {
                group: score([r for r in results if keep(r[0])], relevant, totals(relevant))
                for group, keep in GROUPS.items()
            }
            for label, relevant in LABELS.items()
        }
        for c, results in ranked.items()
    }
    per_query = {
        q.entry_id: {
            "fold": assignment[q.entry_id],
            "fault_type": q.fault_type,
            "target_service": q.target_service,
            "relevant_entries": {label: totals(relevant)(q) for label, relevant in LABELS.items()},
            "top_3": {c: [e.entry_id for e in ranked[c][n][1][:3]] for c in CONFIGURATIONS},
            "first_relevant_rank": {
                c: {
                    label: next(
                        (i for i, e in enumerate(ranked[c][n][1], 1) if relevant(q, e)), None
                    )
                    for label, relevant in LABELS.items()
                }
                for c in CONFIGURATIONS
            },
        }
        for n, q in enumerate(records)
    }
    return {
        "configurations": configurations,
        "saturation": saturation(configurations),
        "per_query": per_query,
    }


def query_lengths(
    queries: Mapping[str, str],
    services: Mapping[str, str],
    tokenize: Callable[[str], Sequence[int]],
    decode: Callable[[Sequence[int]], str],
    limit: int = MODEL_TOKENS,
) -> dict:
    """How many tokens each query has and whether its target service is named in the part a model
    reads. The comparison ignores case and spaces, since a tokenizer may split a hyphenated name
    when it decodes. A reranker also reads the entry text in the same limit, so for it this is an
    upper bound on the query it reads."""

    def squeeze(text: str) -> str:
        return "".join(text.split()).lower()

    report = {}
    for problem_id, query in queries.items():
        ids = tokenize(query)
        service = squeeze(services[problem_id])
        report[problem_id] = {
            "characters": len(query),
            "tokens": len(ids),
            "truncated": len(ids) > limit,
            "service_in_query": service in squeeze(query),
            "service_in_read_part": service in squeeze(decode(ids[:limit])),
        }
    return report
