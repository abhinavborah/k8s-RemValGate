# Intrinsic retrieval metrics

Story E4-S3 (#32), requirement R4.3. Hit@1, Hit@3, MRR and fault-type Recall@3 of the four
retrieval configurations (#31) over the one fixed query per pool incident (#29,
`docs/pool_queries.md`), each ranked over the corpus of the fold that holds the problem out
(#30, #135). Descriptive only: no threshold, and nothing here changes configuration (iii), which
was fixed before this run (#35, `src/remvalgate/retrieval/fixed.json`).

## How it was run

```
uv sync --group retrieval
uv run python scripts/run_intrinsic_retrieval.py
```

The script reads the two query files of #29 from `runs/queries/`, refuses to start unless they hold
exactly one query per pool problem, loads the models, then opens one pool run per problem
(`start_run`, a touch, gate G1 checked, clean tree). Every configuration ranks the whole fold
corpus (17 entries: 12 training incidents and 5 runbook entries), so MRR sees every rank. The code
is `src/remvalgate/retrieval/intrinsic.py`; its tests use fake models and synthetic queries only.

Run on 2026-11-05 at 12:25:01 UTC, at commit `7a54324`, on CPU:

- dense model `BAAI/bge-small-en-v1.5` at `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`
- reranker `BAAI/bge-reranker-base` at `2cfc18c9415c912f9d8155881c133215df768a70`
- torch 2.15.0+cpu, sentence-transformers 6.2.0, transformers 5.22.1
- query files `pool_queries-20261031t155042z.jsonl` and `pool_queries-20261031t163138z.jsonl`

The run added 16 `intrinsic-*` touches to `runs/touch_log.jsonl`, one per pool problem. The report
(`intrinsic-20261105t122501z.json`: metrics, saturation, the top 3 and the first relevant rank of
every query, and the query lengths) and the 16 manifests are in `runs/intrinsic/`, which git
ignores. They are archived as `intrinsic_20261105.tar.gz` (sha256
`05ee47a4218f756382d5604146c5290a362cfe9ce8703f3c65fd4071e457487d`) in the folder
`intrinsic_retrieval_20261105` on one group member's drive, shared with the reviewers.

Before the touches, the query files were opened once to count their rows and problem ids and the
length in characters of each query (3201 to 4586). No query text was read or scored before the
run.

## Relevance labels

- **Fault type**: an entry is relevant when its fault type is the query incident's. Each query has
  2 to 6 relevant entries in its fold (58 over the 16 queries), so Recall@3 cannot reach 1 for a
  query with more than 3: its ceiling over the 16 queries is 0.838.
- **Fault type plus target service**, the stricter label of BRD section 9 point 1: no query has a
  relevant entry. Within each fault type every pool instance targets a different service, and the
  runbook entries name `<service>`. Every configuration therefore scores 0 under it and Recall@3 is
  undefined. This label cannot separate the configurations on this pool, so it is reported as
  such and not replaced after the fact.

## Results (fault-type label)

Counts out of the queries in each group. Recall@3 is the mean over queries of relevant entries in
the first 3 divided by the relevant entries in the fold.

| Configuration | Group | Hit@1 | Hit@3 | MRR | Recall@3 |
|---|---|---|---|---|---|
| (i) none | all 16 | 0 | 0 | 0.000 | 0.000 |
| (ii) dense | all 16 | 5 | 9 | 0.497 | 0.271 |
| (iii) dense + reranker | all 16 | 3 | 7 | 0.404 | 0.229 |
| (iv) hybrid + reranker | all 16 | 3 | 6 | 0.387 | 0.198 |
| (ii) dense | database 4 | 2 | 3 | 0.619 | 0.375 |
| (iii) dense + reranker | database 4 | 0 | 2 | 0.348 | 0.250 |
| (iv) hybrid + reranker | database 4 | 0 | 1 | 0.281 | 0.125 |
| (ii) dense | TargetPortMisconfig 6 | 0 | 1 | 0.232 | 0.028 |
| (iii) dense + reranker | TargetPortMisconfig 6 | 0 | 0 | 0.193 | 0.000 |
| (iv) hybrid + reranker | TargetPortMisconfig 6 | 0 | 0 | 0.192 | 0.000 |

(i) returns nothing, so it scores 0 on every metric with every query counted.

By fault type, from the first relevant rank of each query: all three ranking configurations put a
relevant entry first for the 3 AssignNonExistentNode queries. (ii) also does so for the 2
UserUnregistered queries, which (iii) and (iv) rank 2nd to 4th. No configuration puts a relevant
entry in the first 3 for any TargetPortMisconfig query except one, ranked 3rd by (ii); their first
relevant ranks are 3 to 7.

## Saturation

The rule, committed with the code before any query was scored: under the fault-type label over all
16 queries, the labels saturate if (ii), (iii) and (iv) tie on Hit@3 or any of them reaches 16.
Hit@3 is 9, 7 and 6, so the labels did **not** saturate, and the stricter-label sensitivity check
the issue asks for if saturation is present is not needed. It could not have discriminated anyway
(see Relevance labels).

## Query length

Both models read at most 512 tokens (`model_max_length` 512 in both tokenizer configurations, and
`max_seq_length` 512 for the dense model, at the pinned revisions) and every query is longer: 776
to 1419 tokens for the dense model's tokenizer and 803 to 1445 for the reranker's, so the dense and
reranker stages of (ii) to (iv) read only the start of every query. The BM25 stage of (iv) has no
such limit and reads the whole query. The target service is named in every query (pod and
Deployment names), and in the first 512 query tokens of the dense model's tokenizer for all 16.
For the reranker the service falls outside the first 512 tokens of the query for 2 queries
(`pool-assign_to_non_existent_node_social_net-mitigation-3`,
`pool-k8s_target_port-misconfig-mitigation-6`).

These counts are of query tokens against the full 512, but the special tokens take part of the
window. The dense model adds 2 (`[CLS]` and `[SEP]`), so it reads at most 510 query tokens, and the
check for it is within 2 tokens of what it reads: a service named only in query tokens 511 and 512
would be counted as read. The reranker adds 4 to a query and entry pair and also reads the entry
text, so it reads less of the query than the check assumes, and its count is an upper bound. The
check now subtracts each tokenizer's special tokens (`query_lengths`, `special`); it was not run
again, since that reads the query files and takes a new touch.

The planner retrieves with its first observation as it was taken, before any clipping, so the same
truncation applies there. No query instruction is added to the dense model
(`load_models`).

## What the numbers say and do not say

- 16 queries, 5 fault types: one query changes Hit@3 by 6 percentage points. Over all 16,
  (ii) scores above (iii) and (iv) on every metric, and (iii) above (iv) on all but Hit@1, where
  they tie. No test is run on this ordering, and no claim that the reranker hurts is made from it;
  the paired comparison belongs to the ablation reporting (#57).
- The findings of `docs/pool_queries.md` show in the results. The 6 TargetPortMisconfig queries
  show no trace of the fault and differ only in pod names and ages, and they score lowest. The
  namespace gives the database faults away, so any Hotel Reservation entry is a database entry:
  for those 4 queries the fault-type label is helped by the application, not by the symptom. The
  query cannot tell RevokeAuth from UserUnregistered, so a hit across the two counts as a miss.
- Configuration (iii) stays fixed. These results are reported and are not used to change it
  (BRD 7.1).
