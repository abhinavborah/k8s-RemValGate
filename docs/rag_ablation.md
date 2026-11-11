# Extrinsic RAG ablation (E4-S4, #33)

The baseline-only results of the untuned planner, read as an ablation of retrieval (BRD R4.4,
proposal version 2, section VI). No new rollout was run, so no touch is logged. The intrinsic
metrics of the same configurations are in [intrinsic_retrieval.md](intrinsic_retrieval.md).

## Configurations and runs

| Baseline | Retrieval | Problems | Commit | Date |
|---|---|---|---|---|
| 3a | (i) none | 16 | `9825b68` | 2026-11-08 and 2026-11-09 |
| 3b | (ii) dense | 16 | `9825b68`, `dc5214f`, `3b30bc4` | 2026-11-09 and 2026-11-11 |
| 3c | (iii) dense plus reranker | 16 | `f1c10ef` | 2026-11-08 |

Every run used `qwen/qwen3.5-4b`, GGUF Q8_0, context 32768, thinking on, one run per problem at
seed 1000 (the script reports the seeds), and every manifest records no uncommitted change.
Nothing under `src/` or `scripts/` that the run imports differs between these commits (`3b30bc4`
adds only the training data export, which the run does not import). 3b's run of
`revoke_auth_mongodb-mitigation-1` comes from a retry on 2026-11-11: the first part of its run
stopped there on a harness assertion on 2026-11-08 (the fault did not show in pod status in
time), and the first retry stopped at the planner's first model call because the model server
was not running. Neither produced model output; all three touches are logged (#150).

The 3c runs are the rerun of the baseline-only run (#53), and 3a and 3b come from #150. The run
logs are on the shared drive under `k8s-RemValGate/run-logs/` as
`baselines-pool-3c-20261108.tar.gz`, `baselines-pool-150-20261108t215120z.tar.gz`,
`baselines-pool-150b-20261109t002656z.tar.gz` and `baselines-pool-150c-20261111.tar.gz`.

## Rules

Fixed before the diagnoses were tabulated (`src/remvalgate/harness/rag_ablation.py`):

- **Diagnosis accuracy** is the share of problems whose diagnosed fault type and service both
  match the injected fault, as proposal version 2 defines it. The truth is the fault type read
  from the problem id and the target service of the problem's known fix.
- The service is compared ignoring case. For RevokeAuth and UserUnregistered the target is
  `mongodb-<client>`, and `<client>` also counts, because AIOpsLab's own localization eval of
  these two faults accepts either (`problems/revoke_auth/revoke_auth.py` and
  `problems/storage_user_unregistered/storage_user_unregistered.py` at `576c3f0`).
- **Fault-type accuracy**, the type alone, is reported beside it.
- A problem with no diagnosis (an escalation, the turn cap, a reply that never parsed) counts as
  wrong, so every rate is over the problems run.
- **Single-action recovery** is the outcome RECOVERED.
- Intervals are 95 percent Wilson. Paired comparisons use the 16 problems, which all three
  configurations ran, with the McNemar mid-p value. The issue sets no threshold: the results are descriptive.

## Results

| Baseline | With a diagnosis | Fault type | Fault type and service | Recovered |
|---|---|---|---|---|
| 3a, none | 13 of 16 | 11 of 16, 0.69 [0.44, 0.86] | 9 of 16, 0.56 [0.33, 0.77] | 4 of 16, 0.25 [0.10, 0.49] |
| 3b, dense | 12 of 16 | 10 of 16, 0.63 [0.39, 0.82] | 8 of 16, 0.50 [0.28, 0.72] | 6 of 16, 0.38 [0.18, 0.61] |
| 3c, dense plus reranker | 15 of 16 | 10 of 16, 0.63 [0.39, 0.82] | 7 of 16, 0.44 [0.23, 0.67] | 6 of 16, 0.38 [0.18, 0.61] |

Paired on the 16 problems, first against second (n12: only the first is right, n21: only the
second).

| Pair | Shared | Diagnosis n12 / n21, mid-p | Recovered n12 / n21, mid-p |
|---|---|---|---|
| 3a vs 3b | 16 | 2 / 1, 0.63 | 1 / 3, 0.38 |
| 3a vs 3c | 16 | 3 / 1, 0.38 | 1 / 3, 0.38 |
| 3b vs 3c | 16 | 1 / 0, 0.50 | 0 / 0, 1.00 |

Per problem. T+S: fault type and service right; T: type right, service wrong; wrong: type
wrong; none: no diagnosis. R: recovered; -: not recovered.

| Problem | 3a | 3b | 3c |
|---|---|---|---|
| `k8s_target_port-misconfig-mitigation-1` | T, - | T, - | T, - |
| `k8s_target_port-misconfig-mitigation-2` | T+S, R | none, - | T, - |
| `k8s_target_port-misconfig-mitigation-3` | T, - | T, - | wrong, - |
| `pool-assign_to_non_existent_node_social_net-mitigation-1` | T+S, - | T+S, R | T+S, R |
| `pool-assign_to_non_existent_node_social_net-mitigation-2` | T+S, - | T+S, R | T+S, R |
| `pool-assign_to_non_existent_node_social_net-mitigation-3` | T+S, - | T+S, R | T+S, R |
| `pool-k8s_target_port-misconfig-mitigation-4` | none, - | none, - | T, - |
| `pool-k8s_target_port-misconfig-mitigation-5` | none, - | none, - | none, - |
| `pool-k8s_target_port-misconfig-mitigation-6` | none, - | none, - | wrong, - |
| `pool-scale_pod_zero_social_net-mitigation-2` | T+S, R | T+S, R | T+S, R |
| `pool-scale_pod_zero_social_net-mitigation-3` | T+S, R | T+S, R | T+S, R |
| `revoke_auth_mongodb-mitigation-1` | wrong, - | wrong, - | wrong, - |
| `revoke_auth_mongodb-mitigation-2` | T+S, - | wrong, - | wrong, - |
| `scale_pod_zero_social_net-mitigation-1` | T+S, R | T+S, R | T+S, R |
| `user_unregistered_mongodb-mitigation-1` | wrong, - | T+S, - | T+S, - |
| `user_unregistered_mongodb-mitigation-2` | T+S, - | T+S, - | wrong, - |

## Reading

One run per problem on 16 problems; no difference is significant, and every interval is wide.

- **Diagnosis.** Retrieval did not improve diagnosis: 9 of 16, 8 of 16 and 7 of 16 for (i),
  (ii) and (iii). With the reranker the planner gave a diagnosis more often (15 of 16, against
  13 of 16 and 12 of 16), but the added diagnoses were wrong on the service or the type.
- **Recovery.** Retrieval added two recoveries: 4 of 16, against 6 of 16 for both. Both
  configurations with retrieval recovered the three AssignNonExistentNode problems, which 3a
  diagnosed correctly but answered with `kubectl edit deployment ...`, an interactive verb the
  allow-list refuses (VERB_NOT_ALLOWED). On this evidence retrieval helps the form of the action
  more than the diagnosis.
- 3a's only TargetPort recovery (`k8s_target_port-misconfig-mitigation-2`) has no counterpart
  with retrieval.
- No configuration recovered a MongoDB problem.
- The simplest expert rule (baseline 1) and retrieval only (baseline 2) recovered 8 of 16 each
  on the same problems (results comment on #150).

## Limits

- One seed per problem: a different draw can change a cell, and 3b and 3c are not more than one
  problem apart on any accuracy or recovery count.
- The service truth is the known fix's target. A diagnosis naming a related service, for
  example a caller of a TargetPort service, counts as wrong.
- 3b's run of `revoke_auth_mongodb-mitigation-1` is its third touch of that problem, after two
  stopped attempts with no model output; its other 15 problems have one touch each.
- With retrieval, the corpus of each problem's fold holds the other two AssignNonExistentNode
  problems, whose recorded fixes have the same form. The recoveries there partly mean that
  retrieval supplied a sibling problem's fix. This is the designed setting (the problem itself is
  held out), not a leak, but it bounds what the result says about retrieval in general.

## How to reproduce

```
uv run python scripts/summarize_rag_ablation.py --results \
  <3c run>/baselines_pool/baseline3c-*.result.json \
  <150 run>/baselines_pool/baseline3[ab]-*.result.json \
  <150b run>/baselines_pool/baseline3b-*.result.json \
  <150c run>/baselines_pool/baseline3b-*.result.json
```

Each result file must sit next to its manifest and transcript, as in the tarballs. A run id
found in two folders is read once.
