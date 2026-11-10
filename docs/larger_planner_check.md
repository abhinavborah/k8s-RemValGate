# Larger untuned planner check (E5-S6, #154)

The BRD's fallback for "too few successful plans from a small planner" is a larger open-weight
Qwen planner. This check runs Qwen3.5-9B beside Qwen3.5-4B on the three development problems, to
see whether the larger planner gives candidates with mixed outcomes, which the gate needs and the
4B did not give at gate G3.

## Setup

`scripts/run_oracle_labels.py` with `--model`, on 2026-11-10, commit `88f0b19` (clean manifests),
seed 0, K = 8, context 32768, thinking on, temperature 0.7, lmstudio-community GGUF Q8_0 for both
models, one model loaded at a time. Development problems use the synthetic stub corpus and log no
touch. One run per model per problem. The rule was stated in #154 before the run.

## Results

| Problem | Model | Diagnosis | Admitted of K | Distinct executed | Worked of K | Mixed |
|---|---|---|---|---|---|---|
| TargetPortMisconfig | 4B | none (turn cap, 16 turns) | 0 of 0 | 0 | 0 | no |
| TargetPortMisconfig | 9B | type right, service wrong (`compose-post-service`) | 4 of 8 | 4 | 0 | no |
| ScalePod | 4B | right | 8 of 8 | 1 | 8 | no |
| ScalePod | 9B | right | 7 of 8 | 1 | 7 | no |
| AssignNonExistentNode | 4B | right | 8 of 8 | 1 | 8 | no |
| AssignNonExistentNode | 9B | right | 8 of 8 | 1 | 8 | no |

A candidate refused by the allow-list has no outcome, so mixed is read over executed candidates
only. The target of the TargetPortMisconfig problem is `user-mention-service`. The 9B's plan
command there, a target port patch on `compose-post-service`, was applied and did not recover.

## Result against the rule

**Not promising.** No development problem has mixed outcomes for either model, and the 9B has no
working candidate on TargetPortMisconfig.

## Reading

Three problems, one run each: a signal, not a test.

- On the problem the 4B fails, the 9B reached a diagnosis within the turn cap and gave varied
  commands (4 distinct executed, against none), but on the wrong service, so none worked. It
  changed the failure, and it did not fix it.
- On the two problems the 4B solves, the 9B also solves them, in fewer turns (1 and 2, against 3
  and 16).
- The 9B run took 34 minutes and the 4B run 16, with the cluster running. Swap reached about 5 GB
  of 6 GB on this 24 GB host.
