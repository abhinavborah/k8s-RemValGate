# Ablation without interactive tools (E5-S3, #38)

The untuned planner of baseline 3c, run with its telemetry tools removed and compared with 3c
(BRD R5.3). Proposal version 2, section VI: "Without interactive tools, the untuned planner keeps
retrieval configuration (iii) but receives a fixed telemetry bundle collected at injection and
proposes an action without calling get_logs, get_metrics or get_traces; it is compared with
baseline 3c on diagnosis accuracy and single-action recovery."

The planner keeps read-only `kubectl`, so this is an ablation of the interactive telemetry tools,
not of tool calling as a whole (see What is removed).

This file fixes the rules before the first touch. Slice: the primary pool. Threshold: none, the
results are descriptive. Each run on a pool problem logs a touch. Unlike the retrieval ablation
(R4.4, [rag_ablation.md](rag_ablation.md)), this one needs new runs.

## The two arms

| Arm | Tools | Telemetry | Runs |
|---|---|---|---|
| 3c | `exec_shell`, `get_logs`, `get_metrics`, `get_traces` | fetched by the planner | 16, on 2026-11-08 |
| notools | `exec_shell` | a fixed bundle, collected once at injection | none yet |

**3c** is the reference and is not run again. Its runs are the 3c rerun of the baseline-only run
(#53): the 16 pool problems at seed 1000, commit `f1c10ef`, 2026-11-08, with `qwen/qwen3.5-4b`,
GGUF Q8_0, served by LM Studio, context 32768, thinking on. The logs are on the shared drive under
`k8s-RemValGate/run-logs/` as `baselines-pool-3c-20261108.tar.gz`. Its results
([rag_ablation.md](rag_ablation.md)): a diagnosis on 15 of 16 problems, the fault type right on 10
of 16, the fault type and service right on 7 of 16, and 6 of 16 recovered.

**notools** is the same planner, with retrieval configuration (iii) (`pool_retriever` in
`src/remvalgate/retrieval/fixed.py`), seed 1000, model build, thinking setting, turn cap of 16 and
single-action execution path. Only the tools and the bundle differ. It is the arm `notools` of
`scripts/run_baselines_pool.py`.

## What is removed

`get_logs`, `get_metrics` and `get_traces` are removed. `exec_shell` stays, read only as in 3c:
during diagnosis it runs `kubectl get` and `kubectl describe` only. The proposal names the three
telemetry tools, while the issue's first text said "no tool calls". Where the two differ, the
proposal governs ([brd.md](brd.md)). Chosen 2026-11-12, not yet group-confirmed. A run with no
tools at all, one model call over a fixed bundle, would also take away `kubectl get` and
`describe`, which is how the planner finds the faulty object, so it would change two things at
once. It is a different comparison and would be a separate arm, not this one.

The system prompt changes in two places. Its tool list holds `exec_shell` only, and one sentence
changes:

- from "Logs, metrics and traces are noisy and slow, so use them after kubectl, and read logs for
  one service at a time."
- to "Logs, metrics and traces were collected once at injection and are given with the task; no
  tool fetches them."

With no bundle the prompts are byte-identical to the ones 3c used, and a test pins them. A call to
a removed tool is refused as an unknown tool, and the refused turn counts toward the turn cap.

## The bundle

### When it is collected

On the problem's own injection, after the workload and the wait until the fault shows in pod
status, right after the first observation, `kubectl -n <namespace> get deployments,pods`. That is
the point 3c's diagnosis starts from. The workload is the one 3c's run used (`start_workload` in
`scripts/run_planner_dev.py`: 100 requests per second for 30 s, then 12 s for the trace flush). The
namespace comes from the problem, so the 4 MongoDB problems run in `test-hotel-reservation`.

### What it holds

The harness makes these calls once, in this order:

1. `get_metrics` of the namespace;
2. `get_traces` of the namespace;
3. `get_logs` of the 3 Deployments with the most error lines logged since the injection, ties by
   name, leaving out any with none (`ERROR_WORDS`, `error_lines`, `noisiest_services` and
   `LOG_SERVICES` in `src/remvalgate/harness/tool_ablation.py`). An error line holds error, fail,
   refused, timeout, the zerolog level marks ERR or FTL, panic, fatal, not authorized,
   unauthorized or denied, ignoring case and terminal colour codes. The level marks and "not
   authorized" were added on 2026-11-12 after the first pool problem, `revoke_auth_mongodb-mitigation-1`:
   its `geo` client logged only `FTL ... not authorized on geo-db ...`, which counted 0, so its log
   was left out. That problem is run again with a new touch, and the first touch is reported as
   spent on the earlier rule. The lines since the injection are read with
   `kubectl logs deploy/<name> --since-time=<injection>`, one pod per Deployment, and every
   Deployment's count is kept in the transcript as `bundle_error_lines`.

Only the lines since the injection count, because a reset does not restart the services that
called the faulty one, so the errors an earlier problem of the run made them log stay in their
logs. The bundle then shows each chosen Deployment's `get_logs` output as the tool returns it, the
whole log, and the cut below keeps its end, the most recent lines.

Metrics and traces get the summaries the tools give the planner in 3c (`preview` in
`src/remvalgate/planner/telemetry.py`), and the trace summary keeps only spans that started after
the injection. A log, or an output with an error, is kept as the tool returned it. No part reads
the problem's fault type or target. Only the moment of collection depends on them, through the wait
that 3c's diagnosis also starts after.

The planner gets the bundle in the task prompt, after the cluster state and before the past fixes,
under the heading "Telemetry collected at injection:". Each part is the call in the planner's JSON
form, then its output, cut at 4000 characters as the loop cuts a tool's output: the end of a log is
kept, the start of the others. The retrieval query is still the first observation only.

The log rule was set in review on 2026-11-12, before any run, and is not yet group-confirmed. It
replaced a first rule, the logs of the Deployments whose pods were not healthy, which gave the
TargetPortMisconfig problems no logs, although a caller's error log is the deciding evidence there:
in the larger planner check the 9B read `text-service` logs reporting "Failed to connect
user-mention-service" ([larger_planner_check.md](larger_planner_check.md)). Counting the lines since
the injection follows evidence packages that rank entities by what changed during the fault (Bi et
al., arXiv 2610.35767, read in part). Considered and not chosen: the count over the whole log,
which old errors from earlier problems can lead; the logs of each application's entry service
(`nginx-thrift`, `frontend`); and no logs at all.

### Known gaps, stated in advance

What each fault is expected to leave in the logs, from how it shows in pod status (`fault_shows` in
`src/remvalgate/harness/oracle.py`). The transcripts record the real bundle and every count.

- TargetPortMisconfig changes a Service only, so its pods stay healthy and log nothing about it. The
  services that call it are expected to log failed connections, and their logs to be chosen (6
  problems).
- ScalePod leaves the scaled Deployment with no pod to read. Its callers are expected to log failed
  connections (3 problems).
- An AssignNonExistentNode pod is Pending, so `kubectl logs` reads nothing from it and it counts no
  error line. Callers that log failures are chosen instead (3 problems).
- In the 4 MongoDB problems the geo or rate client cannot log in to its database and is expected to
  log that. That is evidence 3c could fetch with one call. `get_logs` finds the pod by the label
  `io.kompose.service`, which equals the Deployment's name in the geo and rate manifests at the
  pinned aiopslab-applications commit `8038be6` (Social Network's base Deployment template sets
  `app` the same way).
- A service that logs error words in normal operation can take one of the 3 places, and where no
  caller logs a failure the bundle holds no logs.

## Rules

Fixed before the run. The scoring is that of [rag_ablation.md](rag_ablation.md), with the same
code (`score`, `summary` and `paired` in `src/remvalgate/harness/rag_ablation.py`):

- **Diagnosis accuracy** is the share of problems whose diagnosed fault type and service both match
  the injected fault, with the service rule of that file. **Fault-type accuracy** and the count
  with a diagnosis are reported beside it.
- **Single-action recovery** is the outcome RECOVERED.
- A problem with no diagnosis counts as wrong, so every rate is over the problems run.
- Intervals are 95 percent Wilson.
- The arms are paired on the problems both ran, with the McNemar mid-p value and the Bonett-Price
  interval of the difference. The issue sets no threshold: the results are descriptive.

Table I of the proposal also names tool-call and schema validity for the tool calling capability,
but its list of metrics names only diagnosis accuracy and single-action recovery for this
ablation. The refused turns of each arm, below, count the replies the loop could not accept.

Diagnostics are reported per arm and not tested (`diagnostics` in
`src/remvalgate/harness/tool_ablation.py`): turns, refused turns, tool calls by name, completion
tokens, model seconds, the largest prompt in tokens as the server counted it, and the bundle size.
An attempted call to a removed tool is refused and counted under its name. Each turn records its
prompt tokens from this story on, so 3c's transcripts have none and its largest prompt is empty.

## Touches

Each notools run on a pool problem logs one touch through `start_run`, which needs gate G1 and a
clean checkout. Development ids run the same way with no touch, as a dry run; one run takes pool or
development ids, never both. A run that stops is run again by problem id, with a new touch. The 3c
runs are read, not rerun. The summary logs no touch: the touches were logged when the runs ran.

## Before the first touch

- The code review is done. A code change after the first touch means the run is repeated.
- The code that decides the outcome is 3c's, apart from the `--model` flag of #154, this story's
  opt-in notools arm and bundle, off by default, and the prompt token count each turn now records,
  which changes no prompt. With `<run commit>` the commit the run's manifests name, this shows
  nothing else:

  ```
  git diff f1c10ef <run commit> -- src/remvalgate/planner src/remvalgate/retrieval \
      src/remvalgate/overlay src/remvalgate/harness/allowlist.py \
      src/remvalgate/harness/allowlist_policy.json src/remvalgate/harness/folds.py \
      src/remvalgate/harness/slices.json src/remvalgate/harness/execute.py \
      src/remvalgate/harness/cluster_state.py src/remvalgate/harness/snapshot.py \
      src/remvalgate/harness/harm.py src/remvalgate/harness/outcome.py \
      src/remvalgate/harness/toolcall.py src/remvalgate/harness/toolcall_schemas.json \
      scripts/run_oracle_labels.py scripts/run_baselines_smoke.py scripts/run_dev_end_to_end.py \
      scripts/run_planner_dev.py scripts/run_baselines_pool.py \
      src/remvalgate/harness/tool_ablation.py
  ```

  The paths are those of [baselines_pool.md](baselines_pool.md), plus
  `scripts/run_baselines_pool.py`, which ran 3c and runs this arm, and the new
  `src/remvalgate/harness/tool_ablation.py`.
- The run uses the host and model server of the 3c run, LM Studio. The backup host
  ([backup_host.md](backup_host.md)) is a separate configuration. `--model-version` is the
  `model_version` of a 3c manifest in the 3c archive without the `; thinking=on` the script
  appends, so both arms record the same version.
- On the healthy cluster, with no injection and so no touch, every Deployment's name equals the
  pod label `get_logs` looks it up by. On each line of these two the two words match:

  ```
  kubectl --context kind-kind -n test-social-network get deploy -o \
    jsonpath='{range .items[*]}{.metadata.name} {.spec.template.metadata.labels.app}{"\n"}{end}'
  kubectl --context kind-kind -n test-hotel-reservation get deploy -o \
    jsonpath='{range .items[*]}{.metadata.name} {.spec.template.metadata.labels.io\.kompose\.service}{"\n"}{end}'
  ```

- The touch log on the branch, `runs/touch_log.jsonl`, is committed and current.
- `REMVALGATE_MONGO_ADMIN_PASSWORD` and `REMVALGATE_MONGO_ROOT_PASSWORD` are set, with the values of
  the labelling run ([oracle_labels.md](oracle_labels.md)). The run stops before any touch if one
  is missing. Never print them in the recorded terminal.
- The development dry run passes.

## The development dry run

```
uv run python scripts/run_baselines_pool.py --baseline notools \
  --model-version "<format and quantization>" dev-k8s_target_port-misconfig-mitigation-1 \
  dev-scale_pod_zero_social_net-mitigation-1 dev-assign_to_non_existent_node_social_net-mitigation-1
```

The 3 development problems use the synthetic stub corpus and take no touch. The dry run checks
that:

- the bundle holds metrics and trace summaries, not errors;
- the counts in `bundle_error_lines` look sound, and on the TargetPortMisconfig problem a caller of
  the misconfigured service is among the chosen logs, with its log and not
  `Error: Your service/namespace does not exist. Use kubectl to check.`;
- the planner answers;
- the cluster is unchanged after each problem;
- the largest prompt of any turn, `max_prompt_tokens` in the diagnostics (the server's own count),
  stays well under the 32768-token context. LM Studio's behaviour past the context was never
  checked ([backup_host.md](backup_host.md)), so a prompt near the limit stops the run for a look
  before any touch.

AIOpsLab's `get_logs` returns that error text whenever it cannot read a log, also when no pod
matches the name (`aiopslab/orchestrator/actions/base.py` at `576c3f0`). The 3 problems all run in
`test-social-network`, so no dry run reads a log in `test-hotel-reservation`. The label lookup
there is checked before the first touch instead (above), and its first logs come from a MongoDB
pool problem, with a touch.

## The run

```
uv run python scripts/run_baselines_pool.py --baseline notools \
  --model-version "<format and quantization>" [problem id ...]
```

With no problem id it runs the 16 pool problems, in about 2 to 3 hours (an estimate). Run ids are
`notools-<problem id>-<stamp>`, and the result files say `"baseline": "notools"`. The outputs go to
`runs/baselines_pool/`, which git ignores: per problem the manifest, the transcript, which holds the
bundle, and the result file.

Added on 2026-11-12, during the run: on the 4 MongoDB problems the arm waits until every MongoDB
Deployment of the namespace answers a ping before each injection (`wait_for_mongod` in
`scripts/run_baselines_pool.py`). The reset deletes the MongoDB pod and waits until the pods run, but
those Deployments have no readiness probe, so the execution's injection reached the new pod before
mongod listened, failed with "Connection refused", and the harness stopped because the fault did not
show. This happened twice on `revoke_auth_mongodb-mitigation-1`. The wait changes no injection,
planner, bundle or outcome step, and 3c's shared code is unchanged; it is a code change after
touches, so it is disclosed here. `revoke_auth_mongodb-mitigation-1` took 3 touches before its
completed run: 1 with the earlier log rule and 2 that stopped at the injection check, all in the
touch log. The 12 problems that are not MongoDB problems ran before the wait was added.

Changed on 2026-11-13: the wait moved into `diagnose` and `execute` of
`scripts/run_oracle_labels.py`, so every arm of the #165 batch (3a, 3b, 3c and 7 as well as the
ablation arms), baselines 1 and 2 (`observe_fault`) and the oracle labels wait before each
injection of a MongoDB problem, and the arms of one batch run the same steps. A test
(`tests/test_mongod_wait.py`) checks that every injecting function of the two scripts waits first. 3b's stop on `revoke_auth_mongodb-mitigation-1` on 2026-11-08 (the
fault did not show in time, `docs/rag_ablation.md`) was probably the same race on the tool path.
The wait is still not exercised by a development dry run: no development problem is a MongoDB fault.

The run is recorded with a terminal capture and a timestamped pod watch, following the steps in the
docstring of `scripts/run_baselines_pool.py`, with the title `E5-S3 <run name>`. Both files go to
`k8s-RemValGate/run-logs/<run name>/` on the shared drive, beside the run's manifests, never to git.
One recording and one pod watch cover the batch of 16 run ids, as they did for the 3c run of #53,
so they are named after the batch and not after one run id.

By default the API server closes a watch after a randomized time above 30 minutes. In the
labelling runs the pod watch stopped after about 45 and 49 minutes with no error line
([oracle_labels.md](oracle_labels.md)), and this run takes hours. So start the pod watch in the
loop of [backup_host.md](backup_host.md), which starts it again; each restart prints the current
pods again before new events:

```
while :; do kubectl --context kind-kind get pods -A -w --output-watch-events; sleep 1; done \
  | while IFS= read -r line; do printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$line"; done \
  > runs/recordings/<run name>.pods.log
```

The summary:

```
uv run python scripts/summarize_tool_ablation.py --results \
  <3c run folder>/baseline3c-*.result.json runs/baselines_pool/notools-*.result.json
```

It needs no cluster and no model, and logs no touch. Each result file must sit next to its manifest
and transcript, as in the tarballs. It refuses a non-pool problem, a dirty manifest, two runs of
one arm on one problem, an arm with no run, and arms that differ in seed or in model name or
version.

## Results

Not run yet.

## Limits

- One seed per problem: a different draw can change a cell. The two 3c runs that differed only in
  the seed, the labelling run at seed 0 and the rerun at seed 1000, differed in recovery on 1 of 16
  problems (the R check of #53). That is the noise of one draw, before the date and injection
  differences below.
- 3c ran on 2026-11-08 on its own injections. notools runs later on new ones, so the two arms differ
  in date and injection as well as in the tools.
- The comparison needs the host and model server of the 3c run.
- The bundle rule is one choice, set in review before the run. A service that logs error words in
  normal operation can take a place, and a fault whose callers log nothing leaves the bundle with no
  logs.
- The bundle adds up to about 20 thousand characters, five parts of at most 4000, to the prompt of
  every turn. The notools run records its prompt tokens, and 3c's run did not, so the two arms'
  context use is not compared.

## Added on 2026-11-12: the 2 by 2

The run above changed two things at once: it removed the telemetry tools and added the bundle. Its
diagnosis gain sits on the 6 TargetPort problems, where the bundle's top log names the faulty
service and 3c never read that log (0 of 6 runs). So it cannot tell whether the tools hurt or the
bundle helps. Two arms are added so each factor is varied alone:

| | no bundle | bundle |
|---|---|---|
| telemetry tools | 3c | `toolsbundle` (new) |
| read-only kubectl only | `kubectlonly` (new) | `notools` |

- `scripts/run_baselines_pool.py`: `--baseline toolsbundle` keeps 3c's tools and adds the bundle;
  `--baseline kubectlonly` keeps `exec_shell` only, with no bundle. Both use the notools path
  (injection, wait, first observation, reset, and the MongoDB wait on the database faults).
- `src/remvalgate/planner/loop.py`: a bundle may now come with the telemetry tools; the prompt then
  says the tools can fetch more, instead of that no tool fetches telemetry.
- `scripts/summarize_tool_ablation.py`: reads any of the 4 arms (3c and at least one other) and
  compares each pair that differs in one factor: the tools with and without the bundle, the bundle
  with and without the tools, and both together. On the runs above it gives the same numbers.

All 4 arms run in the interleaved batch of #165, at 2 seeds, with the fixes of #162, #163 and #164
and the protocol of #161. The run above stays as the first run and is superseded by that batch; the
log rule of the bundle stays as it is, chosen after the dry run, as disclosed above.

## Added on 2026-11-14: results of the 2 by 2

From the interleaved batch of #165 step 1 (`ffc2af6`, seeds 1000 and 1001, 16 pool problems per
arm and seed), by `scripts/summarize_tool_ablation.py`. Counts with rates and Wilson 95 percent
intervals; the ablation is reported, not tested (R9.4).

| Arm | Seed | Fault type | Fault type and service | Recovered |
|---|---|---|---|---|
| 3c | 1000 | 15 of 16, 0.94 [0.72, 0.99] | 11 of 16, 0.69 [0.44, 0.86] | 7 of 16, 0.44 [0.23, 0.67] |
| 3c | 1001 | 13 of 16, 0.81 [0.57, 0.93] | 13 of 16, 0.81 [0.57, 0.93] | 8 of 16, 0.50 [0.28, 0.72] |
| toolsbundle | 1000 | 15 of 16, 0.94 [0.72, 0.99] | 14 of 16, 0.88 [0.64, 0.97] | 10 of 16, 0.62 [0.39, 0.82] |
| toolsbundle | 1001 | 16 of 16, 1.00 [0.81, 1.00] | 15 of 16, 0.94 [0.72, 0.99] | 9 of 16, 0.56 [0.33, 0.77] |
| kubectlonly | 1000 | 10 of 16, 0.62 [0.39, 0.82] | 10 of 16, 0.62 [0.39, 0.82] | 8 of 16, 0.50 [0.28, 0.72] |
| kubectlonly | 1001 | 11 of 16, 0.69 [0.44, 0.86] | 9 of 16, 0.56 [0.33, 0.77] | 9 of 16, 0.56 [0.33, 0.77] |
| notools | 1000 | 16 of 16, 1.00 [0.81, 1.00] | 16 of 16, 1.00 [0.81, 1.00] | 11 of 16, 0.69 [0.44, 0.86] |
| notools | 1001 | 16 of 16, 1.00 [0.81, 1.00] | 13 of 16, 0.81 [0.57, 0.93] | 9 of 16, 0.56 [0.33, 0.77] |

Discordant problems per pair, first against second (n12: only the first, n21: only the second):

| Pair | Recovered, 1000 | Recovered, 1001 | Fault type and service, 1000 | Fault type and service, 1001 |
|---|---|---|---|---|
| bundle with tools: 3c vs toolsbundle | 1 / 4 | 0 / 1 | 1 / 4 | 1 / 3 |
| bundle without tools: kubectlonly vs notools | 0 / 3 | 2 / 2 | 0 / 6 | 0 / 4 |
| tools with bundle: toolsbundle vs notools | 1 / 2 | 1 / 1 | 0 / 2 | 2 / 0 |
| tools without bundle: 3c vs kubectlonly | 1 / 2 | 2 / 3 | 4 / 3 | 6 / 2 |
| both: 3c vs notools | 0 / 4 | 1 / 2 | 0 / 5 | 3 / 3 |

Reading: on fault type and service, toolsbundle is ahead of 3c at both seeds (14 and 15 against
11 and 13), and notools at seed 1000 only (16 against 11, then 13 against 13). On recovery both
bundle arms lead 3c by 3 or 4 problems at seed 1000 and by 1 at seed 1001. The net change per pair
ranges from 0 to 4 problems (the table above). These are counts, reported and not tested (R9.4).
For scale only: 3c alone recovers 7 at seed 1000, 8 at seed 1001 and 6 at both, so 3 problems
change between its two seeds, and one pair (3c against notools at seed 1000, 0 / 4) changes by
more than that. The longest prompts were 21168 tokens (toolsbundle) and 18348
(3c), under the context of 32768. The first run's result of 2026-11-08 to 2026-11-11 (3c against
notools only) is superseded by this one.
