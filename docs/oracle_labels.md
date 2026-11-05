# Oracle labels

Story E3-S5 (#27), requirement R3.5. Every candidate of baseline 3c is executed once on each pool
problem, with a harness reset between candidates, so that each candidate has an outcome label and
each problem a flag for whether any candidate worked. Gate G3 counts its headroom from these
labels (R6.6, #53). This file states the method and the rules before any label is read. No oracle
run has been made yet (see Results).

## What is labelled

- **The candidates.** Baseline 3c is the untuned planner with retrieval configuration (iii)
  (`src/remvalgate/baselines/untuned.py`), run with the settings of `scripts/run_planner_dev.py`
  (model, thinking, sampling, turn cap). On each problem it diagnoses once, and K = 8 candidate
  commands are sampled from that diagnosis in one draw (`sample` in
  `src/remvalgate/planner/loop.py`). Nothing is resampled: R5.2 belongs to the full system.
- **The plan's own command.** The action of 3c is the plan's own command, which is not one of the
  K. R6.6 counts both headroom numbers from one set of executions that includes 3c's top pick, so
  the plan's command is labelled in the same run, apart from the K. It is executed only when it
  is admitted and duplicates no candidate, and it never counts toward n, c or the problem's flag.
  It is admitted as 3c's action path admits it (`check_chain`), so a chain such as `a && b`, the
  form of the corpus fixes of several commands, is executed. The K are admitted as `sample` admits
  them (`check`, one command), so a chained candidate is rejected and has no outcome.
- **Scope.** The 16 problems of the primary pool, never the confirmation slice. These are 3c's
  candidates. The candidates the gate scores are labelled in M2 (R3.5). If baseline 5 uses a
  fine-tuned planner, these labels are only a proxy for the pool the gate will score (R6.6).
- **Queries.** No query is recorded. The pool queries of #29 ([pool_queries.md](pool_queries.md))
  stay the queries of the intrinsic retrieval metrics. The planner retrieves with its own first
  observation of the live cluster, as 3c does in the baseline-only run. A recorded observation
  would name pods that no longer exist after a new injection. This departs from R3.7 as first
  written, and from a criterion of #29, which had oracle labelling reuse the recorded queries. R3.7
  and pool_queries.md are updated to match (BRD version 1.10). Proposed by one group member, not
  yet group-confirmed.
- **Touches.** Each problem is one run: `start_run` appends one touch (run id
  `oracle-<problem id>-<UTC time>`) and the run writes one manifest. Every execution of the problem
  is recorded in its labels file. A problem whose run stopped is run again from the start, with a
  new touch and a new diagnosis, and only complete runs are reported.

## How a problem is labelled

`scripts/run_oracle_labels.py`, per problem:

1. Inject the fault and save the Services and Deployments as the injection left them. Run the
   problem's workload, run 3c's planner and sample the K candidates. Check that the diagnosis
   changed nothing, then reset.
2. Merge near-identical commands (below). The plan's own command is matched against the K the same
   way.
3. For each command to execute, the K in draw order and then the plan's own: inject, check the
   reset (below), wait until the fault shows in pod status, take that pod status, send the command
   through the Orchestrator's `exec_shell` handler, wait the 120 s settle window, difference the pod
   status (`detect_harm`), ask whether the fault remains, classify, and reset.

Every command therefore starts from a new injection, not from the cluster as the diagnosis left
it. The pod status before the action is the reading in which the fault shows (an unhealthy pod for
AssignNonExistentNode, no pod for ScalePod, a `geo` or `rate` client that runs and is not ready
for the database faults), so a faulted workload is never read as healthy before the action and
counted as harm after it. For the database faults a client pod still being created does not count:
AIOpsLab's injector deletes the client's pods whatever its script did, so only a client that
started and fails shows that the fault took. A fault that has not shown after 180 s stops the run.

## The reset between executions

- **Every fault type.** AIOpsLab's recover step (`recover_fault`). Then each Service and Deployment
  that still differs from the clean state is put back with `kubectl replace`: the recover step
  undoes the injected fault only, so a candidate that patched, scaled or restarted another part of
  a Deployment or Service would otherwise leave that change in place. A Deployment put back must
  finish its rollout. Then every workload must be healthy, and the Services and Deployments of the
  namespace must equal the clean state. Before
  the next command runs, the new injection must leave the Services and Deployments equal to the
  state saved after the first injection. That is the check of the reset against the saved
  post-injection state. After each problem, every namespace of the run must equal its state
  before the run.
- **RevokeAuth and UserUnregistered.** These 4 problems change MongoDB users and roles, which no
  Kubernetes object holds. Their reset is AIOpsLab's recover step followed by its mitigation eval
  on `test-hotel-reservation`, which waits up to its settle window (120 s by default) for every
  pod in the namespace to be healthy. Their reset is checked this way and not against a saved
  object snapshot. The eval reads the health of the pods, those of the services that use the
  databases among them, not the users and roles themselves.
- **Not the system's rollback.** A command is applied as a baseline's single action and classified
  with `single_action_result`. The rollback of `apply_with_check`
  (`src/remvalgate/harness/execute.py`) is never used. The put back reads the clean state of the
  whole namespace, not the objects a command names. A put back that failed shows in the comparison
  that follows it.
- **A failed check stops the run.** Labels are never taken on a cluster in an unknown state. The
  labels files of the problems already done stay, and the rest are run again by id.

## Near-identical candidates

Two admitted commands are near-identical when they make the same kubectl call once the shell's
quoting is removed (`dedup_key` in `src/remvalgate/harness/oracle.py`):

- flags compare by name and value in any order, and `--flag=value` reads as `--flag value`; a flag
  given twice keeps the order of its values;
- a JSON value, such as a patch body, compares as parsed JSON with its keys sorted, and a JSON list
  keeps its order;
- positional arguments and the command an exec runs after `--` compare token for token.

The rule merges only commands that make the same call, so commands that differ in a resource name's
spelling (`deploy` and `deployment`) or a flag's name (`-p` and `--patch`) are executed apart. A
merged candidate is not executed. It takes the label of the first candidate it matches, and its
record names that candidate's seed.

## Labels, the flag and mixed outcomes

- **Outcome.** Each execution is classified by the outcome classifier (`single_action_result`, then
  `classify`): RECOVERED, APPLIED_INEFFECTIVE or APPLIED_HARMFUL.
- **Worked.** A candidate worked when its outcome is RECOVERED. APPLIED_HARMFUL is not working even
  when the fault is gone, since the classifier puts harm first.
- **No outcome.** A candidate the allow-list rejected, a reply with no command, and a command the
  Orchestrator's parser would change are never executed, have no outcome and have not worked.
- **The flag.** A problem's flag is true when at least one of its K candidates worked
  (`any_working_candidate`, the input of the six-class classifier). It is false for a problem with
  no candidates, when the planner reached its turn cap with no diagnosis.
- **Mixed outcomes.** A problem has mixed outcomes when at least one candidate worked and at least
  one candidate with an outcome did not. Candidates with no outcome are left out, since the gate
  scores admitted candidates only. The summary reports the number of such problems.

## Coverage and the rule for K

- **Coverage.** For k = 1 to 8, the estimated number of problems with a working candidate among k
  candidates is the sum over the problems of pass@k = `1 - C(n - c, k) / C(n, k)`, the unbiased
  estimator of Chen et al. 2021 (arXiv 2107.03374, equation 1). Here n = 8 sampled candidates and c
  is the number that worked; a deduplicated candidate takes the label of the candidate it
  duplicates, and a candidate with no outcome counts as not working. A problem with no candidates
  adds 0. The sum is exact (`coverage` in `src/remvalgate/harness/oracle.py`), and the estimator is
  tested against the paper's reference implementation (its Figure 3). At k = 8 the coverage is the
  number of problems with a working candidate.
- **The rule for K, stated before the labels are read.** K is cut to the smallest k whose coverage
  equals the coverage at k = 8, and only if the budget fails. The summary reports that k
  (`smallest_k`) whether or not the budget fails. This file states the rule before any oracle run.
- **What this checks.** This checks oracle coverage only: whether a working candidate is still
  among k candidates. The effect of K on the gate's choice is not checked before M2.
- **Status.** The coverage criteria and the rule for K were added to #27 on 2026-10-30 by one group
  member and are not yet group-confirmed. If the group rejects the edit to #52, they are removed
  with it.

## Limits

- One execution per command (R = 1), so a command's label does not show how much its outcome
  varies between runs.
- The fault-remains check is the problem's own: the Service's `targetPort` for TargetPortMisconfig,
  the problem's mitigation eval for the rest. That eval also reads the other pods of the namespace.
  The AssignNonExistentNode, RevokeAuth and UserUnregistered evals wait for every pod to be
  healthy. The ScalePod eval takes one reading with no wait: the Deployment has 1 desired and 1
  available replica, and every container in the namespace is ready, not crash-looping and not
  terminated with a reason other than Completed. So an execution record can show the fault
  remaining after a command that removed it but broke another workload. Its outcome is
  APPLIED_HARMFUL either way, since harm comes first.
- For the database faults the reset is checked through pod health only (above). The allow-list's
  exec shape leaves no room for `-u` and `-p`, so a MongoDB call in an admitted exec carries no
  user, and it would be refused where authorization is enforced
  (`src/remvalgate/retrieval/pool_corpus.py`). A RECOVERED label on a database problem should
  therefore be read against its execution record before it is reported.
- Log-probabilities are recorded for each candidate only when the server returns them. LM Studio
  likely returns none (README), so these labels cannot rank the K by log-probability.

## Run

```
uv run python scripts/run_oracle_labels.py --model-version "<format and quantization>"
```

It needs `aiopslab/config.yml`, the kind cluster with Social Network and Hotel Reservation deployed
and Ready, Prometheus installed (README, Telemetry), the planner model served (default LM Studio on
port 1234) and `uv sync --group retrieval`. Each pool problem is a pool run with a touch, so it
needs gate G1 and a clean checkout. Development problem ids run the same way with the synthetic
stub corpus and no touch, as a dry run, for example with `--candidates 2`; one run takes pool or
development ids, never both.

The outputs go to `runs/oracle/`, which git ignores, and to the shared drive under
`k8s-RemValGate/run-logs/`: per problem the manifest, the transcript (the plan, its retrieved
entries and the K candidates) and the labels file (the labels, the counts and one record per
execution), and a summary of the run's problems. A run
finished in two parts is summarized with `scripts/summarize_oracle_labels.py <labels files>`, which
needs no cluster. It refuses files of more than one slice, so a glob over `runs/oracle/` that picks
up a dry run's files stops.

As arithmetic only: at the pilot's 144.8 s per rollout ([compute.md](compute.md)), with no model
time, 128 executions take about 5.1 hours. Each problem adds its diagnosis and its K samples, the
plan's own command adds at most 16 executions, and a merged candidate saves one.

## Results

Not run yet.
