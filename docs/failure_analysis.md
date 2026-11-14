# Failure analysis

Story E9-S7 (#58). Why the planner runs of #165 fail, and where the arms disagree. The classes and
counts come from `scripts/failure_cases.py` (`src/remvalgate/harness/failures.py`); the examples
are read from the transcripts by hand. This covers #165 step 1 (224 runs, 7 arms, seeds 1000 and
1001). The fine-tuned arm (3cft) and its control are added when step 3 has run.

## Classes

Each run is put in one class, in this order: recovered; escalated by the planner; turn cap with no
final answer; a final answer with no command; the command refused by the allow-list; the command
admitted but not sent, because it could not be sent unchanged; the command applied and not
recovering. The diagnosis is scored against the problem's fault type and target
service (`rag_ablation.score`).

Runs per arm, both seeds (32 each):

| Arm | Recovered | Refused | Admitted, not sent | Applied, not recovered | Turn cap | Escalated |
|---|---|---|---|---|---|---|
| 3a | 8 | 15 | 0 | 5 | 4 | 0 |
| 3b | 12 | 12 | 0 | 6 | 2 | 0 |
| 3c | 15 | 10 | 0 | 4 | 3 | 0 |
| 7 | 15 | 1 | 0 | 6 | 7 | 3 |
| toolsbundle | 19 | 7 | 1 | 4 | 1 | 0 |
| kubectlonly | 17 | 6 | 0 | 3 | 6 | 0 |
| notools | 20 | 5 | 0 | 7 | 0 | 0 |

Refusal reasons: FLAG_DENIED 23, SHELL_METACHAR 18, VERB_NOT_ALLOWED 12, RESOURCE_NOT_ALLOWED 2,
OUT_OF_SANDBOX 1, over the 56 refused runs of all arms.

Diagnosis, runs per arm:

| Arm | Right | Wrong service | Wrong fault type | None |
|---|---|---|---|---|
| 3a | 22 | 1 | 5 | 4 |
| 3b | 19 | 7 | 4 | 2 |
| 3c | 24 | 4 | 1 | 3 |
| 7 | 19 | 2 | 1 | 10 |
| toolsbundle | 29 | 2 | 0 | 1 |
| kubectlonly | 19 | 2 | 5 | 6 |
| notools | 29 | 3 | 0 | 0 |

## What fails

1. **Over all arms, the allow-list refuses the fix more often than the planner misdiagnoses:** 56
   refused runs against 37 with a wrong fault type or service. Per arm this holds for 3a, 3b, 3c,
   toolsbundle and notools, not for baseline 7 (1 refused, 3 wrong) or kubectlonly (6 and 7).
   - Refusal is the largest failure class for 3a, 3b and 3c (15, 12 and 10 of 32 runs).
   - It is also the largest class on the 4 MongoDB problems: 37 of their 56 runs. Of the rest, 12 were applied without recovering, 1 was admitted and not sent (toolsbundle, `user_unregistered_mongodb-mitigation-2`, seed 1001: a command ending in a double quote does not survive the parser), 3 escalated, 2 reached the turn cap, and 1 recovered (baseline 7, `user_unregistered_mongodb-mitigation-1`, seed 1000).
   - The refused commands carry the login, but use methods the allow-list does not admit: `adminCommand`, `dropUser`, `updateOne` with `$set`, `sh -c` scripts. The admitted methods are `getSiblingDB`, `createUser`, `grantRolesToUser`, `getUser` and `getUsers`.
   - No MongoDB candidate of the run 3 labels works (`docs/oracle_labels.md`).
2. **On TargetPortMisconfig the planner names the wrong service.** All 6 problem and seed pairs where notools recovered and 3c did not are TargetPortMisconfig problems.
   - 3c named the wrong service in 3 of them: `social-graph-service` for `user-service` (problem 1, seed 1000), `compose-post-service` for `post-storage-service` (problem 3, seed 1000), and `nginx-web-server` for `compose-post-service` (problem 4, seed 1000).
   - In the other 3 it misdiagnosed the fault type (problem 5, seed 1000) or reached the turn cap (problems 1 and 2, seed 1001).
   - In the first and third, the command was also refused: `kubectl edit` (VERB_NOT_ALLOWED) and `kubectl create service` (FLAG_DENIED).
3. **The turn cap ends runs without a diagnosis,** mostly where the planner keeps calling tools: 7 runs of baseline 7, 6 of kubectlonly, 4 of 3a, 3 of 3c, 2 of 3b and 1 of toolsbundle. notools, which has no tools beyond read-only kubectl and starts with the bundle, never reaches it.

## Examples

- **The bundle names the service.** `k8s_target_port-misconfig-mitigation-1`, seed 1000.
  - 3c diagnosed TargetPortMisconfig on `social-graph-service`; the target is `user-service`. It proposed `kubectl -n test-social-network edit service social-graph-service`, which the allow-list refused (VERB_NOT_ALLOWED).
  - notools, on the same problem and seed, diagnosed "the user-service Service forwards traffic to targetPort 9999 but the pods listen on port 9090". It sent `kubectl -n test-social-network patch svc user-service --type=json -p '[{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 9090}]'`, which recovered.
  - (`baseline3c-k8s_target_port-misconfig-mitigation-1-20261113t003950z`, `notools-k8s_target_port-misconfig-mitigation-1-20261113t010400z`)
- **A right diagnosis, a refused fix.** `revoke_auth_mongodb-mitigation-1`, seed 1001.
  - 3c named the RevokeAuth fault on the geo database.
  - Its fix, `kubectl -n test-hotel-reservation exec deploy/mongodb-geo -- mongo admin -u root -p ROOT_PASSWORD_REF --authenticationDatabase admin --eval 'db.adminCommand({...})'`, was refused (FLAG_DENIED), since `adminCommand` is not an admitted method.
  - (`baseline3c-revoke_auth_mongodb-mitigation-1-20261113t115756z`)
- **An escalation that names the block.** The same problem, baseline 7, seed 1000, escalated with: "direct MongoDB command execution is blocked by security restrictions (FLAG_DENIED, SHELL_METACHAR). I cannot safely execute the fix without understanding how to properly grant roles in this environment."
  - (`baseline7-revoke_auth_mongodb-mitigation-1-20261113t021952z`)
- **A wrong service, applied.** `k8s_target_port-misconfig-mitigation-3`, seed 1000.
  - 3c patched the target port of `compose-post-service`; the fault is on `post-storage-service`. The command was admitted, ran, and did not recover.
  - At seed 1001, 3c named `post-storage-service`.
  - (`baseline3c-k8s_target_port-misconfig-mitigation-3-20261113t014239z`)

## Limits

- One run per arm, problem and seed. A class count is descriptive, and the seed-to-seed change of
  one arm (3c: 3 problems) is of the same size as many of the differences above.
- The classes stop at the first failure: a refused command whose diagnosis was also wrong counts as
  refused. The diagnosis table gives the second view.
- What a failed MongoDB command printed is not in these records: AIOpsLab's shell kept only the
  exit code. Runs from #174 on keep both streams.

## Reproduce

```
uv run python scripts/failure_cases.py runs/baselines_pool/<run id>.result.json ...
```

with the 224 result files of #165 step 1 (shared drive, `rerun165_step1_20261113/step1.tar.gz`).
