# Capability ablations

Story E9-S6 (#57). One page for the ablation of each capability the research question uses, as
counts with rates and no significance test (R9.4). The runs are those of #165, from one commit,
interleaved, at seeds 1000 and 1001, on the 16 pool problems, except where a section says
otherwise (R5.4 and the retrieval ablation of the fine-tuned planner are labelling runs at seed 0).
The fine-tuning ablation is the
primary analysis ([primary_analysis.md](primary_analysis.md)).

## Recovered problems, every arm

From #165 step 1. "Both seeds" counts a problem only when it recovered at both (the rule of #56).

| Arm | Retrieval | Tools | Bundle | Seed 1000 | Seed 1001 | Both seeds |
|---|---|---|---|---|---|---|
| 3a | none | telemetry | no | 5 of 16, 0.31 | 3 of 16, 0.19 | 2 of 16, 0.13 |
| 3b | dense | telemetry | no | 6 of 16, 0.38 | 6 of 16, 0.38 | 6 of 16, 0.38 |
| 3c | dense plus reranker | telemetry | no | 7 of 16, 0.44 | 8 of 16, 0.50 | 6 of 16, 0.38 |
| toolsbundle | dense plus reranker | telemetry | yes | 10 of 16, 0.63 | 9 of 16, 0.56 | 8 of 16, 0.50 |
| kubectlonly | dense plus reranker | read-only kubectl | no | 8 of 16, 0.50 | 9 of 16, 0.56 | 7 of 16, 0.44 |
| notools | dense plus reranker | read-only kubectl | yes | 11 of 16, 0.69 | 9 of 16, 0.56 | 9 of 16, 0.56 |

## Retrieval

3a, 3b and 3c differ in retrieval only. Diagnosis and recovery per seed, with Wilson intervals and
discordant pairs, are in [rag_ablation.md](rag_ablation.md) ("Added on 2026-11-14"). The intrinsic
retrieval metrics of configurations (i) to (iv) are in [intrinsic_retrieval.md](intrinsic_retrieval.md).

On the fine-tuned planner (#34, labelled 2026-11-14 with `--no-retrieval`, against #159's labels
with retrieval; same server, fold builds, seed 0 and K = 8). #34 ran from `3963f4f` and #159 from
`f434ca2`; of the code the labelling run uses, only `scripts/run_oracle_labels.py` changed between
them, by the `--no-retrieval` option (#180).

| Fine-tuned planner | top pick solves | problems with a working candidate | working candidates | mixed | recoverable |
|---|---|---|---|---|---|
| with retrieval (#159) | 8 of 16, 0.50 | 7 of 16, 0.44 | 55 of 120, 0.46 | 1 | 0 |
| without retrieval (#34) | 3 of 16, 0.19 | 5 of 16, 0.31 | 32 of 96, 0.33 | 1 | 2 |

The top pick fails without retrieval on 5 problems it solves with it, and on none the other way.
Problems with no candidates (turn cap): 1 with retrieval, 4 without. Descriptive, with no test. The
labels with retrieval ran 15:12 to 17:12 IST and these 21:41 to 23:11 IST, not interleaved (#161),
so cluster drift is a confound beside retrieval; the top pick is compared at seed 0 only.

## Tool calling

The 2 by 2 of telemetry tools against the error bundle: 3c, toolsbundle, kubectlonly and notools.
Per seed, with Wilson intervals and discordant pairs, in [tool_ablation.md](tool_ablation.md)
("Added on 2026-11-14: results of the 2 by 2").

## Without the deterministic check (R5.4)

`scripts/ablate_no_check.py` applies the allow-list again, offline, to every sampled candidate of
a labelling run and reports the share it removes by reason. It reads pool transcripts, so each
problem is a logged touch, and it runs from the checkout whose touch log the runs of #165 step 3
append to. It ran on 2026-11-14 after step 3 on the 16 labels of #165 step 2 (untuned) and those of
#159 (fine-tuned). The fine-tuned labels come from 4 models, one per fold, and the script pools one
model per run, so it ran per fold and the counts are added; fold 2's summary file clashed with
fold 1's name (same second) and is pooled from its per-problem files.

| Candidates | n | admitted | removed | no command | FLAG_DENIED | VERB_NOT_ALLOWED | SHELL_METACHAR | PARSE_ERROR | RESOURCE_NOT_ALLOWED |
|---|---|---|---|---|---|---|---|---|---|
| untuned (step 2, 16 problems) | 128 | 73, 0.57 | 44, 0.34 | 11, 0.09 | 14 | 14 | 10 | 5 | 1 |
| fine-tuned (#159, 15 problems) | 120 | 88, 0.73 | 29, 0.24 | 3, 0.03 | 17 | 1 | 10 | 0 | 1 |

Per fold, fine-tuned: fold 0, 24 candidates (3 problems), 13 admitted, 8 removed, 3 no command;
fold 1, 32, 25, 7, 0; fold 2, 32, 25, 7, 0; fold 3, 32, 25, 7, 0. All ran from `f434ca2` on
2026-11-14 (`scripts/ablate_no_check.py runs/oracle_3cft/oracle3cft-fold<f>-*.json`, one call per
fold). Fold 2's numbers are the sum of its four `nocheck-*.nocheck.json` files (`without_check`
counts), since its summary file was not written; the name clash is fixed in this change.

One fine-tuned problem (`k8s_target_port-misconfig-mitigation-1`) reached the turn cap with no
candidates, so it has none to check and logs no touch. Without the check, a third of the untuned
candidates and a quarter of the fine-tuned ones would reach the cluster unvetted.

## Fine-tuning

[primary_analysis.md](primary_analysis.md): the fine-tuned planner (3cft) against the untuned
planner (3c) on the same server, interleaved, at both seeds, with baseline 5 beside them.
