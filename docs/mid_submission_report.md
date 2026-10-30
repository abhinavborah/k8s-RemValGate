# Mid-submission report: k8s-RemValGate

Course: Language Models and Agents (CL3410), group of four. Report date: 2026-10-30.
State described: the `main` branch after pull request #107 (merged 2026-10-30 14:16 UTC) and the issue
tracker on the same day. Story and requirement ids refer to [docs/brd.md](brd.md).

**Reading note.** This project has no experimental results on the system yet. The measurements
reported in section 6 are harness timings and problem validations. No planner, gate or baseline has
produced an outcome number, and sections 4, 5 and 6 say so where it applies. Items marked "pending
group confirmation" are proposals that the group has not yet confirmed.

## 1. Project summary

LLM agents diagnose Kubernetes incidents well but often choose an action that does not restore the
service. We test whether a small fine-tuned validity gate helps. A planner retrieves similar past
incidents and samples K = 8 candidate commands. The gate (Qwen3.5-4B with LoRA) scores each
candidate. The best candidate is applied only if it clears a calibrated threshold, and otherwise the
system escalates to a human. We evaluate on AIOpsLab mitigation problems against seven baselines.
Success is judged by executing the fix on a live cluster. The primary comparison is candidate-level
AUROC and Brier score against baseline 5, the fine-tuned planner with no gate.

## 2. Progress

Closed and merged on `main` (29 issues closed in the last 12 weeks, including two epics):

| Area | Delivered |
|---|---|
| Foundations (E1) | Repository layout, lockfile and README commands. Continuous integration running unit tests and lint on pull requests. AIOpsLab pinned as an unmodified submodule at `576c3f0`, with new problems in an overlay in this repository. A two-node kind cluster with all 27 Social Network pods Ready and image digests recorded. Qwen3.5-4B loaded on a Kaggle T4. Run manifest writer and evaluation-set touch log. One development problem run end to end (#13). |
| Harness (E2) | Allow-list and namespace sandbox check. Snapshot and restore of the objects a command touches. Harm detection by pod-status differencing after a 120 s settle window. A six-class outcome classifier. Metric library (Hit@k, MRR, Recall@3, AUROC, Brier, AURC and others). Statistics (McNemar mid-p, Wilson, Bonett-Price, a provisional clustered bootstrap). Tool-call and schema validity scoring. |
| Dataset (E3) | Inventory of the 14 current mitigation problems. Three development problems authored and validated. Three confirmation problems authored, validated and sealed (gate G1, 2026-10-30). |
| Retrieval (E4) | Retriever interface and a development stub. |
| Baselines (E6) | Baselines 1 (fault-type rule) and 2 (retrieval only). |
| Execution loop (E8) | Snapshot, apply, harm check, readiness check, restore and escalate (#50, pull request #107). |
| Experiments (E9) | Five-rollout timing pilot (gate G2). Decisions recorded for the headroom-check stand-in (#67) and the metric of the gate G4 pilot (#68). |

Open pull requests, not yet on `main`: the planner (#106), the grouped four-fold assignment (#108),
the per-fold retrieval corpus (#109) and three new target-port pool problems (#113).

## 3. Datasets

No dataset is downloaded. The evaluation data is live fault injection on AIOpsLab (Microsoft, MIT
licence), pinned at one commit. Outcomes are labelled by executing candidates on the cluster.

| Set | Problems | Status |
|---|---|---|
| Primary pool | 16 planned: 3 TargetPortMisconfig, 2 RevokeAuth, 2 UserUnregistered, 1 ScalePod, 1 AssignNonExistentNode, extended to 6, 2, 2, 3 and 3 by the pool extension. 9 are registered on `main`; the extension is in progress (#24, pull request #113). | In progress |
| Development | 3 problems, one each of TargetPortMisconfig, ScalePod and AssignNonExistentNode, on services no pool problem uses. For prompt work, smoke tests and timing only. | Done |
| Confirmation slice | 3 problems, all on `social-graph-service`. Sealed by checksum on 2026-10-30. Opened once, at the end, as a qualitative check. | Sealed |
| Singleton types | 5 fault types set aside for an exploratory generalization check. | Final phase |

- **Seal.** The harness refuses any pool run until the confirmation slice is sealed, and refuses every
  run on the confirmation slice until the one planned opening at the end.
- **Pool eval override.** The upstream eval of AssignNonExistentNode passes with the fault in place,
  because the Pending pod has no container statuses. We use a stricter eval that also needs an
  available replica. Validated on the cluster on 2026-10-28. Pending group confirmation.

## 4. Baseline experiments

| # | Baseline | Status |
|---|---|---|
| 1 | Fault-type rule | Implemented, merged |
| 2 | Retrieval only | Implemented, merged |
| 3a, 3b, 3c | Untuned planner in three retrieval configurations | Not started (#41, needs the planner) |
| 4 | Non-LM classifier | Final phase |
| 5 | Fine-tuned planner ranking by log-probability | Final phase |
| 6, 7 | Prompted judge, escalate-when-unsure planner | Final phase |

No baseline has been run for a result. Baselines 1 and 2 have a smoke run on the three development
problems that reports no result: it writes no manifest and appends no touch, so there is no outcome
number to quote. The baseline-only run on the primary pool (#53, gate G3) has not started.

## 5. Ablations

Each capability the research question depends on has a planned ablation. None has results yet.

| Capability | Ablation | Status |
|---|---|---|
| Retrieval (RAG) | No retrieval, dense, dense with a cross-encoder reranker, and hybrid dense plus BM25 with the reranker, scored by Hit@1, Hit@3, MRR and fault-type Recall@3. The per-fold corpus keeps an incident from retrieving itself. | Corpus in review (#109); configurations and metrics not started (#31, #32) |
| Tool calling | Without interactive tools (fixed telemetry bundle); without the deterministic check (#38, #39) | Not started |
| Fine-tuning | Classification-head variant and a Gemma gate (#48) | Final phase |

## 6. Results

The numbers below are measured. They describe the harness and the problems, not system performance.
No model ran in any of them.

**Five-rollout timing pilot (gate G2), 2026-10-30.** Development problems, scripted known-fix agent,
two-node kind cluster, commit `c5f2236`. Seconds per rollout, mean and standard deviation over 5:

| Phase | Mean | SD |
|---|---|---|
| Inject the fault | 23.1 | 15.7 |
| Execute the fix | 0.7 | 0.6 |
| Harm window (120 s settle) | 120.3 | 0.0 |
| Reset | 0.4 | 0.6 |
| Whole rollout (wall) | 144.8 | 16.5 |

- All five rollouts were not harmful, left no fault, and reset to the clean snapshot.
- The settle window is 83 percent of the time. The injection time is fixed sleeps inside AIOpsLab
  (6, 31 and 36 s by problem type), so its spread is an upstream constant.
- At the measured 144.8 s, about 832 rollouts is about 33 hours serial. The plan assumed about 69.
- This is a floor with no model latency, so K and R are not yet set. One member recommends K = 8 and
  R = 1; this is not group-confirmed.

**Problem validation.** All 4 overlay problems (3 development, 1 pool variant) failed the eval with
the fault injected and passed it after the known fix, on 2026-10-28. The 3 confirmation problems did
the same on 2026-10-29.

**Compute.** Qwen/Qwen3.5-4B at revision `851bf6e` loaded on one Tesla T4 (peak 8.53 GiB of 14.56
GiB) on 2026-10-27. It generated 44 tokens in 4.3 s, about 10 tokens per second, in emulated
bfloat16. This shows inference fits; it does not show that LoRA training fits.

Run manifests and logs are on the shared Drive folder, referenced by checksum.

## 7. Challenges and mitigation plans

| Challenge | Mitigation |
|---|---|
| Schedule: the pool extension, folds, planner and baseline-only run are not merged, and oracle labelling (about 128 rollouts) must precede gate G3. | Gate order is enforced by the harness. Keep K = 8 and R = 1, and cut K before raising R. If time runs short, cut in this order: the Gemma and classification-head ablations, the extrinsic ablation of retrieval configuration (iv), the singleton check. |
| GPU budget: the plan estimates 150 to 240 GPU-hours of LoRA; one Kaggle account gives about 90 hours at best, on a T4 with emulated bfloat16. | Use several members' accounts or another GPU source, or the cuts above. Fall back to smaller gate sizes (0.6B to 1.7B). Not yet agreed. |
| Python mismatch: the Kaggle image runs Python 3.13 and the project needs 3.11 or 3.12. | Use a 3.12 environment on Kaggle, or change the supported range. |
| Small dataset: 16 pool problems and 3 confirmation problems. | Incident-level statistics, counts with intervals and no significance claims on the confirmation slice. |
| Cluster fragility: `media-frontend` took about 15 minutes to be Ready after a restart, because an init container clones from GitHub without a pin. Two images run under x86 emulation. | Documented in the README. Avoid restarting the cluster between runs. |
| Possible low headroom: if baseline 3c already solves 12 or more of 16 problems, or fewer than 5 are recoverable, a gate has little to gain. | Gate G3 stops gate training in that case, and the result is reported. |
| Limits: rollback covers only the three fault types whose state is a Kubernetes object. The 4 RevokeAuth and UserUnregistered pool problems have no automated rollback. Faults are injected, not production incidents. | Stated in the final report. |

## 8. Timeline to the final submission (2026-11-15)

The order of work follows the dependencies in the tracker. The window labels are a proposed
sequence, not agreed deadlines.

| Window | Work |
|---|---|
| Now | Merge the planner (#36), folds (#26), retrieval corpus (#30) and pool extension (#24). Repository runnable from the README (#61). |
| Next | Retrieval configurations and intrinsic metrics (#31, #32). Untuned planner baselines 3a to 3c (#41). Oracle labelling, baseline-only run, headroom check and parameter freeze (gate G3, #53). |
| Then | Ablations (#38, #39, #57) and the free-form versus template decision (gate G4, #54). Training data generation, LoRA fine-tuning and calibration (#45, #46, #47). Baselines 4 to 7. |
| Last | Main runs and primary analysis (#55, #56). Failure analysis and the singleton check. Open the confirmation slice once (#59). Final report and repository tag (#62, #63). |
| 2026-11-16 to 2026-11-19 | Project evaluations. |

## 9. Repository

All code for the closed stories is merged on `main`. The README gives the clone, setup, test and lint
commands and the cluster recipe. Large artifacts (manifests, run logs) are on the shared Drive folder
by checksum and are not in git.
