# Baseline-only run on the pool

Story E9-S2 (#53), gate G3. Gate G3 fixes three values before any gate or planner training: R,
the minimum gain and the headroom result. This file states how they are computed, before any
result is read. The baseline-only pool runs have since been made: the results of 3a, 3b and 3c
are in `docs/rag_ablation.md`, and the labels the headroom is counted from in
`docs/oracle_labels.md`.

## The run

`scripts/run_baselines_pool.py` runs the chosen baselines (`--baseline`, repeatable: 1, 2, 3a,
3b, 3c, 7, and the tool ablation arms notools, toolsbundle and kubectlonly of
`docs/tool_ablation.md`) on the 16 pool problems. Baseline 7 is 3c told to escalate when unsure
(#44); its result row keeps the plan's `stop` (`final`, `turn_cap` or `escalate`) and `escalation`,
so an escalation is told apart from a turn cap or an unparsed reply, and its manifest records
`escalate_when_unsure`. No summary script reads baseline 7 yet: `summarize_rag_ablation.py` and
`summarize_tool_ablation.py` list it among the arms they skip. Each baseline on each problem is one run with one touch, so the
run needs gate G1 and a clean checkout. Development ids run the same way with no touch, as a dry
run; one run takes pool or development ids, never both.
The Hotel Reservation problems need the two MongoDB password variables of the labelling run
([oracle_labels.md](oracle_labels.md)), with the same values; the run stops before any touch
if one is missing.

Each run executes the baseline's single action once, with the procedure of the oracle labelling
run ([oracle_labels.md](oracle_labels.md)), so every baseline is compared with the labels on one
procedure:

1. Get the command on its own injection. For 3a, 3b and 3c the untuned planner diagnoses once,
   with retrieval configuration (i), (ii) or (iii) over the pool's fold corpora, and no
   candidates are sampled. For 1 and 2 the state is read with `observe` once the fault shows, and
   the fault-type rule or retrieval only gives the action. The cluster is reset after.
2. Admit the command with `check_chain`, as 3c's action path admits it.
3. When it is admitted and can be sent unchanged, execute it on a new injection: check the objects
   equal the saved post-injection state, wait until the fault shows, send the command through
   `exec_shell`, wait the settle window, classify, reset.

A run that applies nothing is classified ESCALATED_UNLABELLED: this run does not read the oracle
labels. Namespaces, observations, the workload and the health checks come from the problem, so the
four database problems run in `test-hotel-reservation`.

All baselines run at one pre-stated seed, 1000, with the labelling run's thinking setting and model
build. The G3 part is the 3c run alone; baselines 1, 2, 3a and 3b can run later with the same code
and settings (#150).

## Headroom

From the oracle labels only (R6.6: both counts come from one set of executions). Baseline 3c's
top pick is the plan's own command in the labels file.

- A problem is solved when the top pick's execution is RECOVERED. A plan with no command, or one
  whose command was rejected or could not be sent, does not solve it.
- A problem is recoverable when the top pick does not solve it and at least one of the K
  candidates worked.
- If the top pick solves 12 or more of 16, the project stops. Otherwise the check passes only
  with at least 5 recoverable problems. The recoverable count is an upper bound on the wins any
  selector over the K can gain.

## R

Per pool problem, whether 3c's top pick recovered (RECOVERED against any other outcome) is
compared between the labelling run (seed 0) and the 3c rerun here (seed 1000). Both use the same
retrieval path, thinking setting and model build, so they differ in the seed only.

- If the outcome flips on 0 or 1 of the 16 problems, R = 1.
- If it flips on 2 or more, R = 3, the cap, and each system's per-problem outcome is the majority
  of its 3 runs. With two outcomes, R = 2 cannot break a 1 to 1 split.

Only 3c is used: baselines 1 and 2 run no model, and 3a and 3b take 3c's R. The rule never reads a
gate result or the confirmation slice. With R = 1 the rerun is 3c's reported row, and 3a and 3b
are paired with it on the same seed. Disagreement between the two 3c runs is the R check itself.

## Minimum gain

At problem level, n = 16 paired problems, the gate must recover at least 5 problems that baseline
5 does not, with no loss, or at least 7 with one loss. That is the smallest win count at which the
two-sided McNemar mid-p (proposal XI) falls below 0.05:

| Losses | Fewest wins | mid-p | mid-p with one win fewer |
|---|---|---|---|
| 0 | 5 | 0.0312 | 0.0625 |
| 1 | 7 | 0.0391 | 0.0703 |

Two or more losses fail the falsifier whatever the wins. The exact test would need 6 wins with no
loss; it is not used, since it is conservative on sparse discordant counts. Wins are possible only
on recoverable problems, so the recoverable count bounds them. The values are `MIN_GAIN` in
`src/remvalgate/harness/g3.py`, and a test checks them against `mcnemar_midp`.

## Before the first touch

- The 3c rerun runs the same code as the labelling run on every path that decides 3c's outcome:
  the planner and retrieval, the allow-list and its policy, the folds and the slice registry, the
  executor, reset and harm check, the outcome classification, the send path, and the scripts the
  rerun imports them from. With C the commit the labelling run's manifests name, this prints
  nothing:

  ```
  git diff C HEAD -- src/remvalgate/planner src/remvalgate/retrieval src/remvalgate/overlay \
      src/remvalgate/harness/allowlist.py src/remvalgate/harness/allowlist_policy.json \
      src/remvalgate/harness/folds.py src/remvalgate/harness/slices.json \
      src/remvalgate/harness/execute.py src/remvalgate/harness/cluster_state.py \
      src/remvalgate/harness/snapshot.py src/remvalgate/harness/harm.py \
      src/remvalgate/harness/outcome.py src/remvalgate/harness/toolcall.py \
      src/remvalgate/harness/toolcall_schemas.json scripts/run_oracle_labels.py \
      scripts/run_baselines_smoke.py scripts/run_dev_end_to_end.py scripts/run_planner_dev.py
  ```

  Otherwise the R check also measures the code change, not only the seed, and is confounded.
  So every change to these paths (the namespace prompt, #129, and the stray brace strip, #146,
  among them) is merged before the labelling run starts; a change that lands after it means the
  labelling run is repeated.
- The labelling run's touch log lines are committed before this run starts in another checkout,
  so the two touch logs never diverge.
- Read the headroom result from the labels first (below, with no result files). If it says stop,
  the 3c rerun is not needed.

## Computing the values

```
uv run python scripts/summarize_g3.py --labels runs/oracle/<run id>.labels.json ...
uv run python scripts/summarize_g3.py --labels runs/oracle/<run id>.labels.json ... \
    --results runs/baselines_pool/<run id>.result.json ...
```

The first prints the headroom result only. Both need no cluster. With result files it refuses files that do not cover each pool problem once, a 3c rerun at the
labelling run's seed, with more than one seed, or with another model name or version (which
holds the thinking setting), and prints the headroom result, R, the minimum gain and the problems
each baseline solved. The committed values are never changed using gate results or the sealed
slice.

## Recording

Every pool run is recorded with a terminal capture and a timestamped pod watch, uploaded to the
shared drive beside its manifests. The steps are in the docstring of
`scripts/run_baselines_pool.py`.

## Added on 2026-11-12: fault-type text defect

The planner prompt of the 3a, 3b and 3c pool runs (results in `docs/rag_ablation.md`) and of
the oracle labelling run (`docs/oracle_labels.md`) used fault-type descriptions adapted from the AIOpsLab paper. #162 rewrites
all five from the change each injector in the pinned AIOpsLab makes, so every fault-type line of
the planner prompt differs from the one these runs used. The defect that led to it was found after the
pool runs, in the review of the tool ablation run on the pool (#160): RevokeAuth read "revoked
authentication causes database connection failures", while the injector removes the database
user's readWrite role, so the user can still log in and its queries are refused; a failed login is
the effect of UserUnregistered. Those results stay as they were run and are superseded by the rerun of #165.


## Added on 2026-11-12: telemetry advice and ticket time

The planner prompt of the 3a, 3b and 3c pool runs (results in `docs/rag_ablation.md`) and of
the oracle labelling run (`docs/oracle_labels.md`) told the planner that logs, metrics and traces "are noisy and slow, so use them after
kubectl". That advice steers the planner away from the telemetry its tools fetch; #163 removes it,
so arms that differ in their tools get the same text otherwise. #163 also writes the ticket time
(the injection time) into every task. Those results stay as they were run and are superseded by the rerun of #165.


## Added on 2026-11-12: run protocol for the reruns

From #161, for every run after this date:
- `--seed` is repeatable and defaults to 1000 and 1001, the seeds of the reruns.
- The arms run interleaved: seed by seed, every arm on one problem before the next problem, with the
  first arm rotated (`src/remvalgate/harness/protocol.py`).
- Each manifest of a planner run records `model_build`, read from the LM Studio host at run start
  (`src/remvalgate/harness/model_build.py`): app version, CLI commit, selected GGUF engine, loaded
  model, quantization, context length, parallel slots, the GGUF file's SHA-256 and the server's
  sampling defaults (the hub `model.yaml` config fields and any per-model override). GPU offload is
  not reported by `lms ps` and is left out. A run stops before its touch if the model is not loaded.
- `planner/chat.py` sent temperature, top-p and top-k only. Two sources say the presence penalty of
  the LM Studio runs on the run host was 1.5: the hub `model.yaml` of `qwen/qwen3.5-4b` there has
  `presencePenalty` 1.5 enabled (the `server_defaults` field of `model_build` records it for new
  runs), and on 2026-11-12 one prompt at temperature 0 with no presence penalty gave the same output
  as with 1.5 and a different one from 0. One prompt cannot tell 1.5 from another non-zero value
  with the same output, and only the presence penalty was checked, not the hub's other fields. On
  this evidence the penalty was 1.5 for the 4B on LM Studio on this host, in every arm alike; the
  run on the backup host (llama-server, `docs/backup_host.md`) is not covered. `planner/chat.py` now
  sends `presence_penalty: 1.5` explicitly to every server and the manifests record it, so the runs
  no longer depend on the server's default. The reruns of #165 are compared with each other only.
- The summaries refuse runs of one comparison served by different builds, including a mix of runs
  with a recorded build and runs from before this date.
- Runs made before this date (3a, 3b and 3c, results in `docs/rag_ablation.md`, and the oracle
  labels, `docs/oracle_labels.md`) have no `model_build`, so they are not compared with the reruns.
- The build names the base URL and the host, and `read_build` refuses a base URL that is not this
  host's LM Studio server. Manifests record `r` as the number of seeds of the run.

## Added on 2026-11-14: the rerun of #165 and the second headroom check

The rerun of #165 ran 3a, 3b, 3c, 7 and the three tool ablation arms (`toolsbundle`, `kubectlonly`,
`notools`) on the 16 pool problems at seeds 1000 and 1001, interleaved, with the fixes of #162,
#163 and #164 and the protocol of #161: 224 runs from `ffc2af6`, 2026-11-13 00:30 to 19:14 UTC,
touches 313 to 536. Every manifest is `dirty: false` with a full `model_build` and
`sampling.presence_penalty` 1.5. The oracle labels were then rerun from the same commit
([oracle_labels.md](oracle_labels.md), run 3).

Recovered problems of 16:

| Arm | seed 1000 | seed 1001 | both seeds |
|---|---|---|---|
| 3a | 5 | 3 | 2 |
| 3b | 6 | 6 | 6 |
| 3c | 7 | 8 | 6 |
| 7 | 7 | 8 | 3 |
| toolsbundle | 10 | 9 | 8 |
| kubectlonly | 8 | 9 | 7 |
| notools | 11 | 9 | 9 |

No arm recovers 7 problems at both seeds: the 4 MongoDB problems and
`k8s_target_port-misconfig-mitigation-3`, `pool-k8s_target_port-misconfig-mitigation-5` and
`pool-k8s_target_port-misconfig-mitigation-6`. Baseline 7 escalated 3 times in its 32 runs.

Headroom, `scripts/summarize_g3.py` over the 16 new labels and the 32 rows of 3c:

- 3c's top pick solves 7 of 16, below the stop rule of 12.
- 1 problem is recoverable (`k8s_target_port-misconfig-mitigation-2`), against the 5 needed. The
  check fails again: the first labels gave 0.
- R: the labelling run solved 7 and 3c, with its two seeds combined by the rule of #56 (recovered
  only when both seeds recover), solved 6, with 1 flip: `pool-k8s_target_port-misconfig-mitigation-4`,
  recovered at seed 1001 and not at seed 1000. Under #161 every compared system runs at 2 seeds,
  so this R check no longer sets the number of runs.
- Minimum gain, two-sided McNemar mid-p below 0.05 at n = 16: 5 wins with 0 losses, or 7 with 1.

The results of 2026-11-08 above (3c, the headroom check) stay as they were run and are superseded
by these.

After the failed check, the group agreed on 2026-11-14, as a group member reports, to fine-tune the
planner anyway as an exploratory ablation, so that each capability has an experiment. That decision
was made after these results and is disclosed as such; R6.6 says no training follows a failed check.
The ablation is the fine-tuned planner run end to end against 3c (#159, #55). Baselines 5 and 6
only rerank 3c's candidates, so the recoverable count bounds them at 1 problem.

Files: on the shared drive under `k8s-RemValGate/run-logs/rerun165_step1_20261113/` (the 672 run
files, the recording, the stdout log and the pod watch, `step1.tar.gz`, 49096674 bytes, SHA-256
`71f43c442a6c774983b4feddb75ef8ee98a0ca7ec4b0bd0b263c14777af5bb07`) and
`k8s-RemValGate/run-logs/rerun165_step2_20261114/` (the headroom check's output and input lists).
