# Singleton check

Story E9-S11 (#73), R9.5. An exploratory check of whether the untuned planner (3c) and retrieval
only (baseline 2) handle fault types that are in no pool problem. Counts only, with no test and no
inferential weight. The scope, the rules for when each fault shows and the two types not run were
recorded in BRD 1.22 before any singleton run.

## Setup

- Runs: 2026-11-15, 00:05 to 00:27 UTC, from commit `c9c1055`, clean tree, AIOpsLab `576c3f0`,
  on the local kind cluster (2 nodes).
- Model: Qwen3.5-4B (`qwen/qwen3.5-4b`), GGUF Q8_0, context 32768, thinking on, served by LM
  Studio on the run host; the 3c settings of #165 (temperature 0.7, top-p 0.8, top-k 20, presence
  penalty 1.5, turn cap 16).
- Seeds 1000 and 1001, one invocation per singleton, 3c and baseline 2 interleaved.
- Retrieval: configuration (iii) over every pool incident and the runbook (BRD 1.20), since these
  problems are in no fold and the corpus holds no record of them.
- The planner's fault-type list holds the 5 pool types only, so it cannot name a singleton type.
- Each invocation was bracketed by a snapshot of both namespaces (Deployments, Services, images
  and the Social Network Helm values); every run left them as before, except where noted below.

## Results

| Singleton | 3c, seed 1000 | 3c, seed 1001 | Baseline 2, seed 1000 | Baseline 2, seed 1001 |
|---|---|---|---|---|
| `wrong_bin_usage` | turn cap, no command | command refused (FLAG_DENIED) | no command | no command |
| `misconfig_app_hotel_res` | command refused (VERB_NOT_ALLOWED) | applied, ineffective | no command | no command |
| `auth_miss_mongodb` | not run: fault did not show | not run | not run | not run |
| `redeploy_without_PV` | not run (BRD 1.22) | | | |
| `astronomy_shop_kafka_queue_problems` | not run (BRD 1.22) | | | |

Recovered: 0 of 8 runs (3c 0 of 4, baseline 2 0 of 4), on 2 singleton types.

- Baseline 2 gave no command on any run; its recorded reason is "nothing was observed to query
  with".
- 3c on `misconfig_app_hotel_res`, seed 1001, patched an environment variable of `geo`
  (`DB_HOST`); the fault is a wrong image, so the patch did not fix it. At seed 1000 it proposed
  `kubectl edit`, which the allow-list refuses (interactive verb).
- 3c on `wrong_bin_usage`, seed 1001, proposed revoking the `readWrite` role of the `root` MongoDB
  user on another service's database, unrelated to the fault; the allow-list refused it
  (FLAG_DENIED) before execution, so nothing ran.
- `auth_miss_mongodb`: the injection (a Helm upgrade that makes `url-shorten-mongodb` require TLS)
  ran, but within the wait the client `url-shorten-service` never became not-ready, so the rule of
  BRD 1.22 did not see the fault and the run stopped after its first touch (3c, seed 1000), before
  the planner.
  The rule was not tuned and the run was not repeated. The recovery left the Helm release at a new
  revision with different values, so it was rolled back to revision 1, after which the snapshot
  matched.

## Limits

- 8 runs on 2 types: no rate is meaningful, and the result says only that neither arm handled
  these two faults here.
- The fault-shows rules of the singleton types were first seen on the cluster in these runs.
- No oracle labels exist for the singletons, so an escalation cannot be told apart from one with a
  working candidate (ESCALATED_UNLABELLED).
- The run files are on the shared drive under `run-logs/singleton73_20261115/`, as
  `single73_20261115.tar.gz` (SHA-256 `c45a3d6c...`) with `SHA256SUMS`.
