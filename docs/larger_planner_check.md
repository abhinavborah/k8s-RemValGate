# Larger untuned planner check (E5-S6, #154)

The BRD's fallback for "too few successful plans from a small planner" is a larger open-weight
Qwen planner. This check runs Qwen3.5-9B beside Qwen3.5-4B on the three development problems, to
see whether the larger planner gives candidates with mixed outcomes, which the gate needs. At gate
G3 the 4B gave mixed outcomes on 1 of 16 pool problems, whose top pick already worked, so no
problem was recoverable (5 needed; [baselines_pool.md](baselines_pool.md), Headroom, and
[oracle_labels.md](oracle_labels.md), run 2).

## Setup

`scripts/run_oracle_labels.py` with `--model`, on 2026-11-10, commit `88f0b19` (clean manifests),
seed 0, K = 8, context 32768, thinking on, temperature 0.7, GGUF Q8_0 for both models (the run
log shows 5.16 GB loaded for the 4B and 10.45 GB for the 9B), one model loaded at a time. Development problems use the synthetic stub corpus and log no
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

Turns are the planner's steps, read from each run's transcript; the cap is 16. Mixed is defined
in [oracle_labels.md](oracle_labels.md) ("Mixed outcomes") and `src/remvalgate/harness/oracle.py`,
both since 2026-11-04, before this run: a candidate refused by the allow-list has no outcome, so
mixed is read over executed candidates only. Had a refused candidate counted as not working,
ScalePod with the 9B (7 worked, 1 refused) would be mixed. The target of the TargetPortMisconfig
problem is `user-mention-service`. The 9B's plan command there, a target port patch on
`compose-post-service`, returned `patched (no change)` and did not recover.

## Result against the rule

**Not promising.** No development problem has mixed outcomes for either model, and the 9B has no
working candidate on TargetPortMisconfig.

## Reading

Three problems, one run each: a signal, not a test.

- On the problem the 4B fails, the 9B reached a diagnosis within the turn cap and gave 4 distinct
  executed commands, against none, but on the wrong services, so none worked. Of the 4, the API
  server rejected 2 ("The request is invalid"); they count as executed under
  `src/remvalgate/harness/outcome.py`. At turn 11 the 9B read `text-service` logs reporting
  "Failed to connect user-mention-service", and still named `compose-post-service`. It changed
  the failure, and it did not fix it.
- On the two problems the 4B solves, the 9B also solves them, in fewer turns (ScalePod 1
  against 3, AssignNonExistentNode 2 against 16).
- The 9B run took 34 minutes and the 4B run 16, with the cluster running. Swap was about 5 of 6 GB
  just after the run, on this 24 GB host.


## Added on 2026-11-12: fault-type text defect

The planner prompt of this check used fault-type descriptions adapted from the AIOpsLab paper. #162 rewrites
all five from the change each injector in the pinned AIOpsLab makes, so every fault-type line of
the planner prompt differs from the one this check used. The defect that led to it was found after the
pool runs, in the review of the tool ablation run on the pool (#160): RevokeAuth read "revoked
authentication causes database connection failures", while the injector removes the database
user's readWrite role, so the user can still log in and its queries are refused; a failed login is
the effect of UserUnregistered. This check ran on three development problems (TargetPortMisconfig, ScalePod and
AssignNonExistentNode), none of them RevokeAuth, and #165 does not rerun it, so its result holds
for the old fault-type texts only.


## Added on 2026-11-12: telemetry advice and ticket time

The planner prompt of this check told the planner that logs, metrics and traces "are noisy and slow, so use them after
kubectl". That advice steers the planner away from the telemetry its tools fetch; #163 removes it,
so arms that differ in their tools get the same text otherwise. #163 also writes the ticket time
(the injection time) into every task. #165 does not rerun this check, so its result holds for the old prompt only.
