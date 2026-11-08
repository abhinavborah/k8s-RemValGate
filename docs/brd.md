# Business Requirements Document: Validity Gate for Agentic Kubernetes Incident Remediation

**Project:** k8s-RemValGate
**Version:** 1.11 (draft for group review)
**Date:** 2026-11-08
**Authors:** project group (four members)
**Baseline:** the project proposal, version 2 (8 pages, created 2026-10-12, sent to the course TA, no reply recorded at this date). The TA's feedback on version 1 asked for an ablation of each capability and, for retrieval, no retrieval, simple retrieval and retrieval with a reranker, evaluated with retrieval metrics; version 2 adds these (R4.2 to R4.4). Where this document and the proposal differ, the proposal governs until the group agrees a change.

## 1. Purpose

This document states what the project must deliver, how success is judged, and how the work is divided into epics and user stories managed on a GitHub Project. It turns the proposal into a plan that can be tracked, and it records the process rules (gates, tests, reproducibility) that protect the validity of the results.

## 2. Background

LLM agents diagnose microservice incidents well but often fail to choose an action that restores the service. On the AIOpsLab mitigation task the best evaluated agent solves 6 of 11 problems (54.55 percent), and the weaker ones solve 36.36, 27.27 and 0 percent. A related benchmark (R2Act) reports root-cause service accuracy of 91.4 to 99.7 percent against recovery validity of only 36.8 to 60.3 percent for five RAG-based LLM backbones. Existing deterministic safety layers check that an action is permitted, not that it works.

The project tests one idea: a small fine-tuned gate (Qwen3.5-4B with LoRA and decision tokens) that scores candidate actions from a retrieval-grounded planner, applies the best one if it clears a calibrated threshold, and otherwise escalates to a human.

### Terms

- **The gate** is the learned scorer. **G1 to G5** are the pre-registration gates in section 7.1.
- **K** is the number of candidate commands the planner samples per problem (8 planned). **R** is the number of runs per system per problem (1 unless baseline outcomes vary, at most 3).
- **Pool** is the 16 problems used for evaluation by cross-validation. **Confirmation slice** is 3 problems sealed before any run and opened once. **Development problems** are kept apart from both, and from the singleton types, for prompt iteration, smoke tests and timing.
- **Touch** is any use of the pool or the confirmation slice, recorded in the touch log.
- **Baseline 5** is the fine-tuned planner choosing by its own ranking, with no gate.

## 3. Scope

**In scope**
- A planner that retrieves similar past incidents and runbook entries, then samples K candidate commands through AIOpsLab's agent-cloud interface.
- A deterministic allow-list and sandbox check, the fine-tuned gate, a calibrated threshold, snapshot-based rollback for Kubernetes-object faults, and escalation.
- AIOpsLab (Microsoft, MIT licence) as the evaluation environment: a live fault-injection framework, installed at a pinned commit and extended with new problem definitions. No dataset is downloaded.
- Seven baselines, ablations of each of the three capabilities (fine-tuning, RAG, tool calling), and an evaluation on AIOpsLab mitigation problems labelled by execution.
- Extending the problem pool from 14 to 24 problems, with a sealed confirmation slice.
- A mid-project submission, a final submission, and preparation for the project evaluations.

**Out of scope**
- Production deployment, monitoring, continuous training, or any user interface.
- Large-scale pretraining, and any paid API spend (every model is open-weight and self-hosted).
- Using R2Act's benchmark for training or evaluation (it is cited as evidence only).

## 4. Stakeholders and roles

| Role | Interest |
|---|---|
| Group members (four) | Build, evaluate and report the system. Division of work is agreed by the group and is not fixed in this document. |
| Course TA and evaluators | Approve the proposal, review the mid and final submissions, run the project evaluations. |
| Target user (on-call site reliability engineer) | Decides whether to trust an AI-proposed fix. The system should act on likely-working fixes and hand over the rest. |

## 5. Business objectives and success measures

| ID | Objective | Measure |
|---|---|---|
| O1 | Answer the research question: does the gate select working candidates better than the planner's own ranking? | Candidate-level AUROC, Brier score and top-1 accuracy against baseline 5, with incident-clustered intervals. |
| O2 | Meet the course evaluation rules. | At least one baseline, a sealed blind slice opened once, a quantitative metric and an ablation for each capability, paired comparisons, model name, version and date recorded for every result. |
| O3 | Deliver on time. | Mid-submission package by 2026-10-30. Final submission by 2026-11-15. |
| O4 | Make results reproducible. | Every reported number traces to a run manifest and a commit. |

## 6. Key dates

| Date | Event | Source |
|---|---|---|
| 2026-10-30 | Mid-submission: progress, datasets, baseline experiments, ablations, results, challenges, mitigation plans, final timeline, and a GitHub repository with all code | Course announcement, as reported by a group member |
| 2026-11-15 | Final submission. No extension is possible. | Same |
| 2026-11-16 to 2026-11-19 | Project evaluations | Same |

## 7. Process and quality requirements

### 7.1 Pre-registration gates

Freezing a decision before a run protects the results in the way a failing test protects code. These gates are blocking: the dependent work must not start until the gate is closed, and each gate is a tracked story.

| Gate | Closes before | What is frozen |
|---|---|---|
| G1 | any rollout on the primary pool | The three-problem confirmation slice is created and sealed. |
| G2 | choosing K and R | A five-rollout pilot has measured the per-rollout time. |
| G3 | any gate or planner training | The baseline-only run has fixed R, committed a numeric minimum-gain threshold, and run the headroom check on baseline 3c standing in for baseline 5 (stop if 3c's top pick already solves 12 or more of 16 problems, and require at least 5 recoverable problems, both counted from the oracle labels, see R6.6). |
| G4 | any per-fold gate or baseline 5 training | The free-form versus template-library decision is made from a small pilot fine-tune of the real gate, run after gate G3 closes and after training data generation (M2), and separate from the G2 timing pilot, which cannot produce an AUROC. It is scored by grouped cross-validation on the training problems of the whole pool and never on the confirmation slice. The decision is made once and logged as a touch of the whole pool. This reuses outcomes that also appear in some outer-fold evaluations, which is disclosed as a limitation. The metric is within-incident AUROC over mixed-outcome incidents. Single-outcome incidents are skipped and their number reported. The template library is chosen only if the whole interval of the pilot's within-incident AUROC lies below the threshold and the minimum number of mixed-outcome incidents is met. Otherwise the result is inconclusive and free-form is kept. A larger pilot is a separate cost for the group to decide. A template outcome regenerates the R7.1 data, and the G4 story counts that cost. This departs from proposal version 2, which decides free-form versus template before any fine-tuning, so that the template library is a pivot and not a patch. Only the pilot fine-tune now precedes G4. Before the pilot runs, the G4 story commits how per-incident values are averaged, the minimum number of mixed-outcome incidents, the interval method and the threshold. The proposal's 0.65 has no stated source and was set for a pooled AUROC, so it is not directly comparable. The blocking scope, the once-over-the-pool scope and the inconclusive zone were proposed on 2026-10-27 and await group confirmation. |
| G5 | any evaluation run | Retrieval configuration (iii) is fixed. Ablation results are reported and never used to change it. |

### 7.2 Test lanes

- **Test-first (deterministic harness):** allow-list and sandbox check, snapshot and restore, harm detection by pod-status differencing, the six-class outcome classifier, fold assignment invariants, retrieval corpus isolation, the incident-clustered bootstrap. Metric code (Hit@k, MRR, AUROC, Brier, AURC, McNemar mid-p, Wilson, Bonett-Price) is tested against reference implementations or published worked examples.
- **Eval-first (model behaviour):** planner and gate output is judged by the frozen evaluation protocol, the sealed slice and the pre-declared metrics, never by unit tests.
- **Integration (few):** live-cluster rollouts, using recorded telemetry for repeat runs. Continuous integration never runs cluster rollouts.
- Prompt iteration, smoke tests and the G2 timing pilot use development problems only. The evaluation pool, the confirmation slice and the singleton types are never used for them. The G4 scoring pilot uses the training problems of the whole pool instead (section 7.1).
- Test fixtures use synthetic data or problems outside the pool. A fixture built from a pool or confirmation problem counts as an evaluation-set touch.
- A few tests are written by someone other than the implementer, or are property based, so an implementation cannot simply fit the visible examples.

### 7.3 Reproducibility

- A run manifest per rollout: commit hash, model name, version and date, random seed, K, R, AIOpsLab commit, cluster configuration.
- A dependency lockfile, and a plain `requirements.txt` export so the code also runs on Colab.
- Large artifacts (model weights, rollout logs, datasets) live on shared drive storage, referenced by checksum, and are not committed.
- An evaluation-set touch log records every use of the primary pool and confirmation slice.

### 7.4 Workflow and Definition of Done

- Short-lived branches, a pull request reviewed by another group member, and continuous integration that runs unit tests and lint on every pull request. Cluster rollouts are run by hand and logged.
- Kanban board with limits on work in progress. Statuses: Backlog, Ready, In progress, In review, Done.
- A story is Done when: its acceptance criteria pass; unit tests exist for deterministic code; an experiment story states its metric, slice, threshold (or "descriptive only") and has its evaluation-set touches logged; the run manifest exists for any reported number; the pull request is reviewed and merged.
- Commits are batched, not spammy. Committed files carry no em dash.

## 8. Requirements by epic

Priority: Must (P0 on the board), Should (P1), Could (P2). Phase: M1 is needed for the mid-submission, M2 for the final. The phasing is proposed and not yet confirmed by the group.

### E1. Foundations and environment

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| R1.1 | Repository layout, dependency lockfile, lint and test commands documented in the README. | Must | M1 |
| R1.2 | Continuous integration running unit tests and lint on pull requests. | Must | M1 |
| R1.3 | A kind cluster running AIOpsLab at a pinned commit recorded in the README and the run manifest, with fault injection and the agent-cloud interface working end to end for one mitigation problem. | Must | M1 |
| R1.4 | Access to GPU and to self-hosted open-weight models for the planner and gate. | Must | M1 |
| R1.5 | Run manifest schema and writer, and the evaluation-set touch log. | Must | M1 |
| R1.6 | Decide how AIOpsLab is pinned and where new problem definitions are kept (a fork, a submodule or an overlay in this repository), so that the repository holds all code. | Must | M1 |

### E2. Evaluation harness (test-first)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| R2.1 | Allow-list and sandbox check applied to every sampled candidate, with reasons for rejection. | Must | M1 |
| R2.2 | Snapshot and restore for the three fault types whose state is a Kubernetes object (TargetPortMisconfig, ScalePod, AssignNonExistentNode). | Must | M1 |
| R2.3 | Harm detection by differencing pre-action and post-action pod status across the target namespace, with the post-action status taken after the 120 second settle window. | Must | M1 |
| R2.4 | Outcome classifier with six classes: recovered, escalated with a working candidate, escalated with none, escalated with no oracle label, applied and ineffective, applied and harmful. An applied candidate that is restored and escalated is classified as applied, with harm read before the restore, and is never recovered. Decided by the group on 2026-10-30 (#84). | Must | M1 |
| R2.5 | Metric library: Hit@1, Hit@3, MRR, Recall@3, AUROC, Brier, AURC, top-1 accuracy, harmful rate at matched coverage. | Must | M1 |
| R2.6 | Statistics: McNemar mid-p, Wilson and Bonett-Price intervals, and the interval method for the primary comparison. The incident-clustered bootstrap in the proposal is provisional pending review point 2. | Must | M1 |
| R2.7 | Tool-call and schema validity scoring (required by the course for tool-using systems). | Must | M1 |

### E3. Dataset and problem pool

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| R3.1 | Inventory of the current mitigation problems (14) with fault type and rollback scope. | Must | M1 |
| R3.2 | Extend TargetPortMisconfig from 3 to 6 instances and ScalePod and AssignNonExistentNode from 1 to 3 each, giving a 16-problem primary pool. | Must | M1 |
| R3.3 | Create and seal the three-problem confirmation slice (gate G1) before the pool extension and before any system run on a pool problem. The new problems are validated with a scripted known-fix command only and logged as a creation touch. | Must | M1 |
| R3.4 | Grouped 4-fold assignment at incident level, with the stated invariants and an automated check. | Must | M1 |
| R3.5 | Oracle labelling: execute all K candidates once per evaluation problem with a harness reset between candidates, checked against the saved post-injection state. Deduplicate near-identical candidates and report how many problems have mixed outcomes. Needed before gate G3 for the recoverable-problem count (R6.6): in M1 it labels baseline 3c's candidates on the primary pool only, never the confirmation slice, and each use is logged as a touch. The labelling of the candidates the gate scores follows in M2. | Must | M1 |
| R3.6 | Singleton fault types set aside for the exploratory generalization check. | Should | M2 |
| R3.7 | Record one post-injection telemetry query per pool incident (16 injections, after gate G1), so intrinsic retrieval metrics do not wait for it. Oracle labelling (R3.5) retrieves with the planner's live first observation, not the recorded query (proposed, pending group confirmation). | Must | M1 |
| R3.8 | Designate development problems, disjoint from the evaluation pool, the confirmation slice and the singleton types, for prompt iteration, smoke tests and the G2 timing pilot. | Must | M1 |

### E4. Retrieval capability (RAG)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| R4.1 | Per-fold corpus built only from that fold's training problems and the runbook entries, so no evaluation incident can retrieve itself. | Must | M1 |
| R4.2 | Retrieval configurations (the ablation the TA requested, plus a hybrid): (i) none, (ii) dense, (iii) dense with a cross-encoder reranker, (iv) hybrid dense and BM25 with the reranker, scored on retrieval metrics only. | Must | M1 |
| R4.3 | Intrinsic metrics (Hit@1, Hit@3, MRR, fault-type Recall@3) over one fixed query per primary-pool incident. | Must | M1 |
| R4.4 | Extrinsic ablation analysis of the untuned planner (baselines 3a to 3c, run once in the baseline-only run) by diagnosis accuracy and single-action recovery, with no new rollouts. | Must | M1 |
| R4.5 | Full system rerun with retrieval removed, reporting the change in candidate yield, AUROC and recovery. | Should | M2 |

### E5. Planner and tool calling

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| R5.1 | Planner that retrieves, diagnoses and samples K candidates through the agent-cloud interface tools. | Must | M1 |
| R5.2 | Resample once if every candidate is rejected, then escalate. | Must | M2 |
| R5.3 | Tool-calling ablation without interactive tools (fixed telemetry bundle). | Must | M1 |
| R5.4 | Tool-calling ablation without the deterministic check (applied offline, share removed reported by reason). | Must | M1 |

### E6. Baselines

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| R6.1 | Baseline 1, fault-type rule, and baseline 2, retrieval only. | Must | M1 |
| R6.2 | Baselines 3a, 3b and 3c, the untuned planner in three retrieval configurations. | Must | M1 |
| R6.3 | Baseline 4, a non-LM classifier over structured features. | Must | M2 |
| R6.4 | Baseline 5, the LoRA-fine-tuned planner choosing by log-probability, trained on the same triples as the gate, after gate G4 closes. | Must | M2 |
| R6.5 | Baseline 6, a prompted judge in place of the gate, and baseline 7, the planner told to escalate when unsure. | Must | M2 |
| R6.6 | Headroom check (gate G3, tracked inside the baseline-only run story), before any gate or planner training, with baseline 3c (the untuned planner with retrieval configuration (iii)) standing in for baseline 5. Both counts come from one set of executions, the oracle labels of 3c's K candidates including the top pick's own outcome (R3.5, about 128 rollouts at K = 8). Stop if 3c's top pick already solves 12 or more of 16 problems. Also require at least 5 recoverable problems: problems where 3c's top pick fails but at least one of its K candidates works. With one set of executions, a pool that triggers the 12 stop also fails the 5 minimum, and the stop is kept as the proposal's rule. The proposal's power arithmetic, with the McNemar mid-p test, needs about 5 net wins with no losses or 7 with one loss at n = 16 (two-sided), and this count bounds what any selector can gain. Passing is a necessary condition, not evidence that headroom exists. If baseline 5 uses a fine-tuned planner, the 3c candidates are only a proxy for the pool the gate will score, and the direction of the difference is not known, so the count is reported again from the M2 labels of the gate's candidates. See review point 7. | Must | M1 |

### E7. Gate fine-tuning

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| R7.1 | Training data generation: K candidate rollouts per training incident per fold, executed and labelled by the evaluator, and regenerated if gate G4 picks the template library. Proposed 2026-11-08, pending group confirmation: the first training data reuses the oracle labels of baseline 3c's K candidates (R3.5) out of fold, with no new rollouts. Fold f's records hold the 12 problems outside fold f, retrieved records of fold-f problems are masked, a candidate that copies a fold-f recorded fix is not trained on, and the held-out text the masking cannot reach is counted per fold. This saves about 256 rollouts (384 per-fold rollouts at K = 8, about 33 hours serial, against the 128 already labelled). Limit: the candidates were sampled while fold-f records were retrievable, which masking cannot undo; it is disclosed beside the results. A per-pair rerun was rejected because most pairs retrieve a same-type fold-f record, so it saves at most about 30 percent of the per-fold cost. | Must | M2 |
| R7.2 | LoRA fine-tuning of Qwen3.5-4B with decision tokens (fallback sizes 0.6B to 1.7B). | Must | M2 |
| R7.3 | Platt calibration split at incident level, with the threshold reported per fold and swept. | Must | M2 |
| R7.4 | Ablations: classification-head variant and a Gemma gate. | Should | M2 |

### E8. Execution loop and rollback

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| R8.1 | Gate scores admissible candidates, applies the top one only if it clears the threshold, otherwise escalates with ranked candidates, evidence and scores. | Must | M2 |
| R8.2 | Snapshot, apply, readiness check with problem-specific probes, restore and escalate on failure. | Must | M2 |
| R8.3 | Latency overhead relative to the ungated planner. | Should | M2 |

### E9. Experiments and analysis

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| R9.1 | Five-rollout timing pilot on development problems (gate G2), which sets K and R and produces no AUROC, and the baseline-only run of baselines 1, 2 and 3a to 3c on the primary pool (gate G3). Refined 2026-10-30 (proposed, see #52): the pilot measures the per-rollout cost and checks the plan against it; K is checked on the oracle labels of #27 and R is set in #53. | Must | M1 |
| R9.2 | Main runs with R fixed by the stated rule, and every primary-pool use logged. | Must | M2 |
| R9.3 | Primary analysis: gate against baseline 5 at candidate level with the confirmed interval method, plus descriptive problem-level recovery, and the number of problems with mixed outcomes. | Must | M2 |
| R9.4 | All capability ablations reported as counts with rates, not tested. | Must | M1 |
| R9.5 | Exploratory singleton-type check, and qualitative failure analysis. | Should | M2 |
| R9.10 | Decision issue: which system stands in for baseline 5 at the headroom check (blocks gate G3). Decided 2026-10-26: baseline 3c, with the rule in R6.6. | Must | M1 |
| R9.11 | Decision issue: what the G4 scoring pilot's AUROC is measured on before a gate exists (blocks gate G4). Decided 2026-10-26: see gate G4 in section 7.1 and review point 8. Gate G4 moves to M2. Refined 2026-10-27 (proposed, see gate G4). | Must | M1 |
| R9.6 | Open the confirmation slice once, as a qualitative check with no inferential weight. | Must | M2 |

### E10. Reporting and submission

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| R10.1 | Mid-submission package: progress, datasets, baseline experiments, ablations, results, challenges, mitigation plans, timeline. | Must | M1 |
| R10.2 | Repository with all code, runnable from the README, submitted with the mid-submission. | Must | M1 |
| R10.3 | Final report with results, limits, and the falsifiable claim stated and judged. | Must | M2 |
| R10.4 | Final repository tag and submission. | Must | M2 |
| R10.5 | Preparation for the project evaluations. | Must | M2 |

## 9. Risks and open questions

**Schedule.** The proposal's compute plan (about 832 rollouts, 69 hours serial or 17 hours at four-way parallelism, plus roughly 150 to 240 GPU-hours of fine-tuning) must fit in the 27 days from 2026-10-19 to the final submission, and the mid-submission needs baselines, ablations and results within the 11 days from 2026-10-19. Agent assistance shortens coding but not live rollouts or training, which this estimate counts. Feasibility is unconfirmed. The pilot (G2) is the first test. The proposal already says K is cut before R is raised. Candidate cuts for the group to decide, in this order: the Gemma gate and classification-head ablations, extrinsic retrieval ablation of configuration (iv), the singleton generalization check. Oracle labelling of baseline 3c's candidates (about 128 rollouts, roughly 11 hours serial at the rate above) now runs before gate G3, so it is on the critical path to the mid-submission. The G4 pilot fine-tune (section 7.1) is not costed in the fine-tuning range above.

**Carried from the proposal**
- Small dataset (11 to 14 problems at the proposal, 16 in the pool once #24 lands, 24 planned). Fallback: inject further faults.
- Free-form commands against a typed-action classifier. Fallback: a bounded template library.
- Live evaluation cost. Fallback: a fixed rollout budget per split.
- Too few successful plans from a small planner. Fallback: a larger open-weight Qwen planner.
- Shared model family between planner and gate. Mitigation: overlap measurement and a Gemma gate.
- Faults are injected, not production incidents. State in the limits.
- RevokeAuth and UserUnregistered state is outside Kubernetes objects, so those 4 pool problems have no automated rollback.

**Review points raised against the proposal (open, for the group)**
1. Retrieval relevance is a fault-type label match over about 5 types and 16 queries, so Hit@3 and Recall@3 may saturate and fail to separate configurations (ii) and (iii). A stricter label (type plus target service) would discriminate more.
2. The primary interval is an incident-clustered bootstrap over 16 clusters (7 today), and the proposal itself argues percentile bootstrap under-covers at this size. Pooled AUROC mixes incident difficulty with within-incident ranking, so per-incident top-1 may be the safer primary.
3. The falsifier says recovery "will not be lower" but fails only when more than one problem is lost, and the minimum gain has no number until gate G3.
4. Section X names "a prompted large model" among the standard baselines, but none of the seven baselines is one.
5. Training rollouts are stated as 64K but each fold trains on 12 problems, giving 48K (conservative by about 128 rollouts).
6. The proposal does not state that model name, version and date are recorded for every reported result, which the course requires. This document adds it as a requirement (R1.5).
7. (Decided 2026-10-26, see R6.6 and R9.10.) The headroom check used baseline 5, itself a fine-tuned planner, "before any fine-tuning". Baseline 3c now stands in for it, before any gate or planner training, with a recoverable-problem minimum. A lightly tuned stand-in was rejected because it is fine-tuning before the check that must precede it. Skipping the check was rejected because nothing would then stop gate training when no gain is possible. Still open: the proposal defines baseline 5 both as ranking by log-probability and as sequence-classification fine-tuning.
8. (Decided 2026-10-26.) The five-rollout timing pilot cannot produce an AUROC, so G4 uses its own pilot: a small fine-tune of the real gate, scored by grouped cross-validation on training problems by within-incident AUROC over mixed-outcome incidents (pooled AUROC would reward learning which incidents are easy). This differs on purpose from the primary metric, candidate-level AUROC pooled over incidents (R9.3), which review point 2 leaves open, so the pilot does not test the primary endpoint. The threshold and its uncertainty handling are committed first (section 7.1). Rejected: a zero-shot or prompted scorer as a proxy, since Safety Sentry (arXiv 2607.13594) reports 52.02 percent three-way accuracy for Qwen3.5-4B before fine-tuning and 91.02 percent after fine-tuning on 7,767 training records, and our training set has about 96 records per fold (12 training problems times K = 8), about 80 times fewer. Also rejected: deciding the fallback by group judgement without an AUROC rule. Proposed 2026-10-27, pending group confirmation: baseline 5 per-fold training waits for G4, the decision is made once over the pool training problems, and an interval that is not wholly below the threshold is inconclusive and keeps free-form. Choosing a design on data that overlaps the evaluation data inflates the estimate (Varma and Simon 2006, BMC Bioinformatics 7:91, abstract read; arXiv 2610.06465, read in part, 0.05 to 0.09 AUC for best-of-25 selection), so the once-over-the-pool scope is disclosed and not claimed to be bias-free. One designated fold would not avoid the overlap, because its training problems contain the other folds' evaluation problems.
9. The proposal defines no development set, but the course requires training, development and blind sets to be kept separate. This document adds one (R3.8). Candidates are extra instances of existing fault types on other services (TargetPortMisconfig is listed for up to 10 services, 6 are in the pool and 1 in the confirmation slice) or lower task levels of existing injections. The group decides.

**Open questions**
- Division of work among members (not yet agreed; assignees stay blank).
- Resolved in #50: R2.3 and R2.4 do not differ. Harm is measured after the 120 second settle window and the check runs after it, so a restore never runs inside that window, and harm is always read before any restore. The execution record keeps the restore result and whether the check passed, so a run log tells a rolled-back action from a fault that remained even though both are APPLIED_INEFFECTIVE. Proposed in #50, pending group confirmation: an escalation is never recovered, also when nothing was restored (for example a pod delete), so R8.2 scores every failed check as not recovered.
- Which GPU and cluster resources are available, and the cost of each.
- Whether the TA's reply to version 2 will change the plan.

## 10. Traceability

| Epic | Proposal sections | Objectives |
|---|---|---|
| E1 | XII | O3, O4 |
| E2 | VII, XI | O1, O2, O4 |
| E3 | IX | O1, O2 |
| E4 | VI, XI | O2 |
| E5 | VI, VII | O2 |
| E6 | X | O1, O2 |
| E7 | VI, XII | O1 |
| E8 | VII | O1 |
| E9 | IX, XI | O1, O2 |
| E10 | none (course milestones) | O3 |

## 11. Change log

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-10-19 | First draft from proposal version 2 and the course milestone dates. |
| 1.1 | 2026-10-19 | Added development problems (R3.8), the AIOpsLab pin and layout decision (R1.6), two gate decision issues (R9.10, R9.11), and raised tool-call scoring (R2.7) to Must, after review. |
| 1.2 | 2026-10-19 | Separated the G4 scoring pilot from the G2 timing pilot, added harm-snapshot timing, oracle reset check, deduplication and mixed-outcome counting, marked the primary interval method provisional, and added a terms section and the TA traceability line, after review. |
| 1.4 | 2026-10-26 | Recorded the headroom check decision: baseline 3c stands in for baseline 5 at gate G3, the check runs before any gate or planner training, at least 5 recoverable problems are required, and oracle labelling (R3.5) moves to M1. Updated G3, R3.5, R6.6, R9.10 and review point 7. After review: both counts come from the oracle labels, the test behind the 5 net wins is named, the M1 scope of R3.5 and the schedule risk are stated, and 3c's retrieval configuration is named. |
| 1.5 | 2026-10-27 | Recorded the gate G4 decision: a pilot fine-tune scored by grouped cross-validation on the training problems of the whole pool with within-incident AUROC, G4 moved to M2, and R9.11 and review point 8 marked decided. After review: the G4 story's commitments are listed, the timing pilot is named in section 7.2 and R3.8, the training record count is stated, the pilot's GPU cost is marked as not costed, and the G4 metric is set apart from the primary metric. Proposed, pending group confirmation: G4 blocks all per-fold training including baseline 5, the decision is made once over the pool training problems and logged as a whole-pool touch, and a pilot interval that is not wholly below the threshold is inconclusive and keeps free-form. R6.4, R7.1, R9.11, section 7.2 and review point 8 updated to match. |
| 1.6 | 2026-10-30 | Recorded the #84 decision: an escalation on a problem with no oracle label is its own outcome class, so R2.4 has six classes; an applied candidate that is restored and escalated is classified as applied, its harm is read before the restore, and it is never recovered. Updated R2.4 and section 7.2, and added an open question for #50 in section 9. |
| 1.7 | 2026-10-30 | Refined R9.1 (proposed, pending group confirmation): the five-rollout pilot measures the per-rollout cost and checks the plan against it, K is checked on the oracle labels and R is set in the baseline-only run, as the edit to #52 states. Updated R9.1. |
| 1.8 | 2026-10-30 | Resolved the section 9 open question left to #50: harm is read after the settle window and before any restore, so R2.3 and R2.4 agree, and the execution record keeps the restore result. Proposed, pending group confirmation: an escalation is never recovered, also when nothing was restored. Updated section 9. |
| 1.9 | 2026-10-31 | Updated the small dataset risk in section 9: "11 to 14 problems today" was the count at the proposal, and the pool reaches 16 once #24 lands. No requirement changed. |
| 1.10 | 2026-11-05 | Proposed, pending group confirmation: oracle labelling (R3.5) retrieves with the planner's live first observation and no longer reuses the recorded pool queries, since a recorded observation names pods that a new injection replaces. The recorded queries stay the queries of the intrinsic retrieval metrics. Updated R3.7. |
| 1.11 | 2026-11-08 | Proposed, pending group confirmation: the first training data (R7.1) reuses the oracle labels out of fold with masking instead of per-fold rollouts, which departs from proposal version 2 and from review point 5's count; the proposal governs until the group agrees. Updated R7.1. |
