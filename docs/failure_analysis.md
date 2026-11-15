# Failure analysis

Story E9-S7 (#58). Why the planner runs of #165 fail, and where the arms disagree. The classes and
counts come from `scripts/failure_cases.py` (`src/remvalgate/harness/failures.py`); the examples
are read from the transcripts by hand. This covers #165 step 1 (224 runs, 7 arms, seeds 1000 and
1001). The fine-tuned arm (3cft) and its fresh 3c control of #165 step 3 are in "Step 3" below.

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

## Step 3: the fine-tuned planner and its control

Runs per arm, both seeds (32 each), #165 step 3:

| Arm | Recovered | Refused | Admitted, not sent | Applied, not recovered | Turn cap | Escalated |
|---|---|---|---|---|---|---|
| 3c (control) | 17 | 8 | 0 | 5 | 2 | 0 |
| 3cft | 15 | 7 | 0 | 5 | 5 | 0 |

- Refusal reasons: 3c FLAG_DENIED 4, VERB_NOT_ALLOWED 2, RESOURCE_NOT_ALLOWED 1, SHELL_METACHAR 1;
  3cft FLAG_DENIED 4, VERB_NOT_ALLOWED 3.
- Diagnosis: 3c right 26, wrong fault type 2, wrong service 2, none 2; 3cft right 25, wrong service
  2, none 5.
- The fine-tuned planner reaches the turn cap more often (5 against 2) and gives no diagnosis in
  those runs; its refusals and wrong diagnoses are about the same as the control's.
- Its two losses in the claim, read from the result files: on
  `pool-assign_to_non_existent_node_social_net-mitigation-3` it reached the turn cap with no
  diagnosis at seed 1000 and recovered at seed 1001; on
  `pool-scale_pod_zero_social_net-mitigation-2` it recovered at seed 1000 and at seed 1001 wrote
  `kubectl ... set deployment text-service replicas=1`, which the allow-list refused
  (VERB_NOT_ALLOWED). Each loss is one seed's failure on a problem the planner can solve.

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

with the 224 result files of #165 step 1 (shared drive, `rerun165_step1_20261113/step1.tar.gz`), or
the 64 of step 3 (`rerun165_step3_20261114/step3.tar.gz`, the `baseline3c-` and `baseline3cft-`
result files stamped after `20261114t0943`).

## Added on 2026-11-15: call validity, harm in the labels and calls per episode

Counts the final report gives, recomputed from the run archives (no cluster, no model, no touch).

- **Call validity, #165 step 1** (224 runs, 2584 planner turns). 2538 replies are a JSON object
  as the planner reads it (`_json_object` in `src/remvalgate/planner/loop.py`). Of these, 2337
  are tool calls (neither `final` nor `escalate`), and 2294 of them (98.2 percent) name a tool the
  arm offers with arguments valid under its schema; the other 43 were answered "unknown tool".
  The read-only check refused 368 of the 2294 valid calls (16.0 percent): `command rejected` by
  the allow-list, most often SHELL_METACHAR, or a verb other than `get` and `describe` during
  diagnosis. No gold tool sequence exists for these problems, so this is validity, not accuracy.
- **Completion tokens, #165 step 1**: 1034746 over the 224 runs.
- **Calls per episode, #165 step 3**: one model call per turn. 3c used 3 to 16 (median 11.5) and
  3cft 1 to 16 (median 16) over their 32 runs each.
- **Harm in the labels.** Untuned (#165 step 2): 0 of the 73 candidates with an outcome and 0 of
  the 10 top picks with an outcome were harmful. Fine-tuned (#159): 1 of 88 and 1 of 12, both on
  `pool-k8s_target_port-misconfig-mitigation-4`, where a JSON patch added `envFrom` to a
  Deployment and its new pod stayed Pending with `CreateContainerConfigError`.

The archives are `rerun165_step1_20261113/step1.tar.gz`, `rerun165_step2_20261114/step2.tar.gz`
(SHA-256 in [oracle_labels.md](oracle_labels.md)) and `rerun165_step3_20261114/step3.tar.gz` on
the shared drive. Unpack each into `$D/step1`, `$D/step2` and `$D/step3` as in the README's
level 2, then run from the repository root:

```
uv run python - "$D" <<'EOF'
import glob, json, statistics, sys
from remvalgate.planner.loop import _json_object  # the planner's own reply parser

D = sys.argv[1]
turns = replies = calls = valid = refused = tokens = 0
for f in glob.glob(f"{D}/step1/runs/baselines_pool/*.transcript.json"):
    for s in json.load(open(f))["steps"]:
        turns += 1
        tokens += s["completion_tokens"]
        try:
            obj = _json_object(s["reply"])
        except ValueError:
            continue
        replies += 1
        if "final" in obj or "escalate" in obj:
            continue
        calls += 1
        obs = s["observation"]
        read_only = obs.startswith(("Refused: command rejected", "Refused: diagnosis allows"))
        valid += read_only or not obs.startswith("Refused")
        refused += read_only
print(f"step 1: {turns} turns, {replies} valid JSON, {valid} of {calls} tool calls valid, "
      f"{refused} refused by the read-only check, {tokens} completion tokens")
for arm in ("baseline3c", "baseline3cft"):
    n = [len(json.load(open(f))["steps"])
         for f in glob.glob(f"{D}/step3/runs/baselines_pool/{arm}-*.transcript.json")]
    print(f"step 3 {arm}: {min(n)} to {max(n)} calls per episode, median {statistics.median(n)}")
for name, pattern in (("untuned", "step2/runs/oracle/oracle-*.labels.json"),
                      ("fine-tuned", "step3/runs/oracle_3cft/*.labels.json")):
    cands, tops = [], []
    for f in glob.glob(f"{D}/{pattern}"):
        d = json.load(open(f))
        cands += [c["outcome"] for c in d["candidates"] if c["outcome"]]
        tops += [d["plan"]["outcome"]] if d["plan"] and d["plan"]["outcome"] else []
    print(f"{name}: harmful {cands.count('APPLIED_HARMFUL')} of {len(cands)} candidates with an "
          f"outcome, {tops.count('APPLIED_HARMFUL')} of {len(tops)} top picks with an outcome")
EOF
```

Output on 2026-11-15:

```
step 1: 2584 turns, 2538 valid JSON, 2294 of 2337 tool calls valid, 368 refused by the read-only check, 1034746 completion tokens
step 3 baseline3c: 3 to 16 calls per episode, median 11.5
step 3 baseline3cft: 1 to 16 calls per episode, median 16.0
untuned: harmful 0 of 73 candidates with an outcome, 0 of 10 top picks with an outcome
fine-tuned: harmful 1 of 88 candidates with an outcome, 1 of 12 top picks with an outcome
```
