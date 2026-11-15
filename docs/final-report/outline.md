# Final report outline (#62)

Outline of the final report, written while the last runs finish. The report itself is LaTeX in
this folder, in the IEEEtran conference layout of the mid-submission report
(`docs/mid-submission/`), with a copy of its `refs.bib`. Format, as agreed in the group on
2026-11-15: IEEE two-column (IEEEtran conference), under 8 pages, with figures, tables and charts.
Each section names the pages and numbers it draws on, so the text can be written from them without
new runs. The draft of the report is `final-report.tex` in this folder.

R10.3 and #62 ask that the report state the falsifiable claim and whether it held, the limits
(injected faults, small pool, exploratory checks, rollback scope), and the model name, version
and date for every reported result.

## 1. Introduction

- The problem: an agent that fixes Kubernetes faults must not apply a harmful command.
- What changed since the mid-report: the gate was not trained (headroom check failed twice), the
  planner was fine-tuned instead as an exploratory ablation, and every planner result was rerun
  after three shared defects were fixed (#165).
- Contributions, stated as what was measured, not what was built.
- Sources: `docs/brd.md` (O1, 1.15, 1.17, 1.18), the mid-report introduction.

## 2. System

- The pipeline: AIOpsLab problems on a kind cluster, the planner (Qwen3.5-4B) with tools and
  retrieval configuration (iii), the allow-list check, execution with rollback and the recovery
  check.
- One figure, carried from the mid-report and updated (no gate in the loop).
- The model chooses the next step in one place only, the planner's diagnosis loop: each turn it
  picks the next read-only tool call or gives its diagnosis and one command. Retrieval, the
  allow-list, execution and reset, harm detection and the outcome classes run the same procedure
  whatever the fault.
- Sources: README, `docs/brd.md`, `docs/finetuned_planner.md`.

## 3. Data and evaluation protocol

- Pool of 16 problems in 4 folds, the 3-problem confirmation slice (sealed, opened once), the
  development problems, the singleton types; the touch log.
- Run protocol (#161): one commit, interleaved arms, seeds 1000 and 1001, the model build recorded.
- The unit: a problem counts only if it recovers at both seeds (#56).
- Sources: `docs/pool_extension.md`, `docs/confirmation_problems.md`, `docs/development_problems.md`,
  `docs/baselines_pool.md`, `runs/touch_log.jsonl` (count of touches).

## 4. Baselines

| Baseline | What | Result | Source |
|---|---|---|---|
| 1, 2 | fault-type rule, retrieval only | from step 1 | `docs/baselines_pool.md` |
| 3a, 3b, 3c | untuned planner, retrieval (i), (ii), (iii) | 2, 6, 6 of 16 at both seeds | `docs/rag_ablation.md` |
| 5 | fine-tuned ranking over 3c's candidates | 7.0 of 16 | `docs/baseline5.md` |
| 6 | prompted judge over 3c's candidates | 8 of 16, 3 escalated (#185) | `docs/primary_analysis.md` |
| 7 | 3c told to escalate when unsure | 3 of 16 | `docs/baselines_pool.md` |

## 5. The headroom check and why the gate was not trained

- G3 failed twice: 0 and then 1 recoverable problem, against 5 needed.
- Consequence: no selector over the untuned candidates can gain more than 1 problem.
- The design assumed the validity gate, a learned scorer that applies the top admitted candidate
  only above a threshold (BRD R8.1), and it was never built (#46 and #49, closed as not planned):
  the planner acts on its own command and the allow-list is the only check before it runs.
- Sources: `docs/oracle_labels.md`, `docs/baselines_pool.md`, BRD R6.6 and 1.17.

## 6. Results

### 6.1 The fine-tuning comparison (pre-specified, exploratory)

- Claim: 3cft recovers more pool problems than 3c and loses at most one problem that 3c recovers.
- Result: 3c 7, 3cft 5 at both seeds, 0 wins and 2 losses, mid-p 0.25, Bonett-Price [-0.32, 0.10].
  **Falsified.** Per seed: 7 and 7 at 1000, 10 and 8 at 1001.
- Who confirmed the rule and when (#56, BRD 1.18), and that the decision to fine-tune came after
  the failed headroom check.
- Candidates beside it: 3cft labels solve 8 at seed 0 against 7 for 3c, with 0 recoverable.
- Source: `docs/primary_analysis.md`.

### 6.2 Capability ablations (descriptive)

- Retrieval, untuned: 3a 2, 3b 6, 3c 6. Fine-tuned (#34): top pick solves 8 with retrieval, 3
  without; working candidates 55 of 120 against 32 of 96; with the run-order confound.
- Tool calling: the 2 by 2 of tools and the error bundle (`docs/tool_ablation.md`). Recovered at
  both seeds: 3c 6, kubectlonly 7, toolsbundle 8, notools 9 of 16 (`docs/ablations.md`), reported
  and not tested (R9.4).
- Deterministic check (R5.4): removes 34.4% of untuned and 24.2% of fine-tuned candidates.
- Source: `docs/ablations.md`.

### 6.3 Failure analysis

- The allow-list refuses more fixes than the planner misdiagnoses, over all arms (56 against 37);
  the MongoDB problems are blocked by the methods the allow-list admits.
- Fine-tuning raises turn caps (5 against 2) and does not reduce refusals.
- Two worked examples from the transcripts.
- Source: `docs/failure_analysis.md`.

### 6.4 Confirmation slice and singleton check

- #59 and #73, run with 3c, no inferential weight. 3c as the system of both was confirmed by the
  group after the slice was opened and before its outcomes were read (BRD 1.20, 1.23).
- Confirmation slice (#59): opened once on 2026-11-14, 6 runs at seeds 1000 and 1001; recovered 5
  of 6 runs, so 2 of 3 problems at both seeds (target_port at seed 1001 only); every command
  admitted, none harmful; not the clean 3 of 3 that #59 frames (`docs/confirmation_check.md`, #188, merged).
- Singleton check (#73): 0 of 8 runs recovered on 2 types (3c 0 of 4, baseline 2 0 of 4); 3 of the
  5 types not run, for the reasons in BRD 1.22 (`docs/singleton_check.md`, #188, merged).

## 7. Limits

- Injected faults from one benchmark (AIOpsLab), two applications, a small pool (n = 16).
- One run per arm, problem and seed; seed-to-seed change of one arm is 3 problems.
- The fine-tuning comparison is exploratory and post hoc in its decision, pre-specified in its rule.
- #34 is not interleaved with #159; the top pick at the labelling seed only.
- Rollback scope (BRD R2): what the restore covers and what it cannot (database user state).
- Judge and planner share a model family (baseline 6).
- MongoDB command output not kept before #174.
- The worst action is an admitted command that leaves a healthy workload unhealthy
  (`APPLIED_HARMFUL`). The allow-list acts before execution and harm detection after it: none in
  the 288 runs of #165 steps 1 and 3, 2 fine-tuned commands in the labels, both on one problem.
- Harm detection sees pod status only, so it misses harm that leaves every pod healthy, such as
  a changed Service port or selector.

## 8. Compute and reproducibility

- Model: Qwen3.5-4B (revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`), GGUF Q8_0; LM Studio
  on the run host for steps 1 and 2 and baselines 1 to 3, 7 and 6; llama.cpp `61dbf49` on an A100
  SXM 80 GB for step 3 and #34 (2026-11-14). Dates per result from the manifests.
- GPU: one A100 pod, about 12.7 hours at $2.19 per hour (training, merging, serving); training
  0.07 to 0.13 GPU hours per fold.
- Archives and checksums on the shared drive (`rerun165_step3_20261114/`: step 3, #34, baseline 6, baseline 5 scores, GPU host logs, adapters), listed in `docs/primary_analysis.md`; the touch log; the replay (#145).
- Model calls per episode: one planner call per turn, at most 16 by design, plus one retrieval.
  Measured in step 3 on 32 episodes per arm: 3c 3 to 16 calls (median 11.5), 3cft 1 to 16
  (median 16), no retries.
- Source: `docs/compute.md`, `docs/finetuned_planner.md`, the manifests.

## 9. Conclusion

- What the evidence supports: retrieval helps the planner, untuned and fine-tuned; the
  deterministic check removes a quarter to a third of candidates; fine-tuning on this data does
  not beat the untuned planner; the bottleneck is the allow-list and the planner's commands, not
  the choice among candidates.
- What it does not: any claim about the gate, which was not trained.

## Still to fill

- The final tag of #63, named in section 8 once it is cut.
