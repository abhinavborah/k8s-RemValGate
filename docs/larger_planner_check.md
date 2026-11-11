# Larger untuned planner check (E5-S6, #154)

The BRD's fallback for "too few successful plans from a small planner" is a larger open-weight
Qwen planner. This check runs Qwen3.5-9B beside Qwen3.5-4B on the three development problems, to
see whether the larger planner gives candidates with mixed outcomes, which the gate needs and the
4B did not give at gate G3.

## Setup

`scripts/run_oracle_labels.py` with `--model`, on 2026-11-10, commit `88f0b19` (clean manifests),
seed 0, K = 8, context 32768, thinking on, temperature 0.7, lmstudio-community GGUF Q8_0 for both
models, one model loaded at a time. Development problems use the synthetic stub corpus and log no
touch. One run per model per problem. The rule was stated in #154 before the run. A later commit
changes only an import line of `scripts/run_planner_dev.py`, so that other scripts can still import
the default model name.

The manifests, labels, transcripts, terminal recordings and pod watch are on the shared drive under
`k8s-RemValGate/run-logs/` as `model154-dev-20261110.tar.gz` (502729 bytes, SHA-256
`5efb3f980577dcc4caad1b3f92a00c74eb580b2418546b67ffbb295ff710f74b`).

## Results

| Problem | Model | Turns | Diagnosis | Admitted of K | Distinct executed | Worked of K | Mixed |
|---|---|---|---|---|---|---|---|
| TargetPortMisconfig | 4B | 16 | none (turn cap) | no candidates | 0 | 0 | no |
| TargetPortMisconfig | 9B | 16 | type right, service wrong (`compose-post-service`) | 4 of 8 | 4 | 0 | no |
| ScalePod | 4B | 3 | right | 8 of 8 | 1 | 8 | no |
| ScalePod | 9B | 1 | right | 7 of 8 | 1 | 7 | no |
| AssignNonExistentNode | 4B | 16 | right | 8 of 8 | 1 | 8 | no |
| AssignNonExistentNode | 9B | 2 | right | 8 of 8 | 1 | 8 | no |

Turns are the planner's steps, read from each run's transcript; the cap is 16. A candidate
refused by the allow-list has no outcome, so mixed is read over executed candidates only. The target of the TargetPortMisconfig problem is `user-mention-service`. The 9B's plan
command there, a target port patch on `compose-post-service`, was applied and did not recover.

## Result against the rule

**Not promising.** No development problem has mixed outcomes for either model, and the 9B has no
working candidate on TargetPortMisconfig.

## Reading

Three problems, one run each: a signal, not a test.

- On the problem the 4B fails, the 9B reached a diagnosis within the turn cap and gave varied
  commands (4 distinct executed, against none), but on the wrong service, so none worked. It
  changed the failure, and it did not fix it.
- On the two problems the 4B solves, the 9B also solves them, in fewer turns (ScalePod 1
  against 3, AssignNonExistentNode 2 against 16).
- The 9B run took 34 minutes and the 4B run 16, with the cluster running. Swap was about 5 of 6 GB
  just after the run, on this 24 GB host.
