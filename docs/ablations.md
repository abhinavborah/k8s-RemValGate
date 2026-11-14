# Capability ablations

Story E9-S6 (#57). One page for the ablation of each capability the research question uses, as
counts with rates and no significance test (R9.4). The runs are those of #165, from one commit,
interleaved, at seeds 1000 and 1001, on the 16 pool problems. The fine-tuning ablation is the
primary analysis ([primary_analysis.md](primary_analysis.md)).

## Recovered problems, every arm

From #165 step 1. "Both seeds" counts a problem only when it recovered at both (the rule of #56).

| Arm | Retrieval | Tools | Bundle | Seed 1000 | Seed 1001 | Both seeds |
|---|---|---|---|---|---|---|
| 3a | none | telemetry | no | 5 of 16, 0.31 | 3 of 16, 0.19 | 2 of 16, 0.12 |
| 3b | dense | telemetry | no | 6 of 16, 0.38 | 6 of 16, 0.38 | 6 of 16, 0.38 |
| 3c | dense plus reranker | telemetry | no | 7 of 16, 0.44 | 8 of 16, 0.50 | 6 of 16, 0.38 |
| toolsbundle | dense plus reranker | telemetry | yes | 10 of 16, 0.62 | 9 of 16, 0.56 | 8 of 16, 0.50 |
| kubectlonly | dense plus reranker | read-only kubectl | no | 8 of 16, 0.50 | 9 of 16, 0.56 | 7 of 16, 0.44 |
| notools | dense plus reranker | read-only kubectl | yes | 11 of 16, 0.69 | 9 of 16, 0.56 | 9 of 16, 0.56 |

## Retrieval

3a, 3b and 3c differ in retrieval only. Diagnosis and recovery per seed, with Wilson intervals and
discordant pairs, are in [rag_ablation.md](rag_ablation.md) ("Added on 2026-11-14"). The intrinsic
retrieval metrics of configurations (i) to (iv) are in [intrinsic_retrieval.md](intrinsic_retrieval.md).

## Tool calling

The 2 by 2 of telemetry tools against the error bundle: 3c, toolsbundle, kubectlonly and notools.
Per seed, with Wilson intervals and discordant pairs, in [tool_ablation.md](tool_ablation.md)
("Added on 2026-11-14: results of the 2 by 2").

## Without the deterministic check (R5.4)

`scripts/ablate_no_check.py` applies the allow-list again, offline, to every sampled candidate of
a labelling run and reports the share it removes by reason. It reads pool transcripts, so each
problem is a logged touch, and it runs from the checkout whose touch log the runs of #165 step 3
append to. It runs on the 16 labels of #165 step 2 (untuned) and the 16 of #159 (fine-tuned) once
step 3 has ended, and its result is added here.

Until then, from the run 3 labels (`docs/oracle_labels.md`): of 128 candidates, 44 were refused by
the allow-list (VERB_NOT_ALLOWED 14, FLAG_DENIED 14, SHELL_METACHAR 10, PARSE_ERROR 5,
RESOURCE_NOT_ALLOWED 1), and 11 replies had no command or were not valid JSON.

## Fine-tuning

[primary_analysis.md](primary_analysis.md): the fine-tuned planner (3cft) against the untuned
planner (3c) on the same server, interleaved, at both seeds, with baseline 5 beside them.
