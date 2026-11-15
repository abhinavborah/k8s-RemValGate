# Preparation for the project evaluations

Story E10-S5 (#64, BRD R10.5). Evaluations run 2026-11-16 to 2026-11-19. Every group member should
be able to explain the design, the results, the limits and the claim, and to run the replay. This
page is built from the merged result pages and the outline of the final report (#184); each number
names the page it comes from. Nothing here is a new result. On 2026-11-15 the safety, tool-call,
retrieval and judge answers, the worked failure examples and the future work were checked against
the final report draft (#192 at `d90ce60`). When the final report (#62) is merged and differs from
this page, the report wins.

Model for every result below unless a line says otherwise: Qwen3.5-4B (revision
`851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`), GGUF Q8_0.

## 1. Demo: replay of recorded runs

No cluster, model or GPU is needed. The replay reads saved run files only (`scripts/replay_run.py`,
#173). Checked on 2026-11-15 from `main` at `d541c42` on Windows: all three runs below play back,
and their output matches the saved `.replay.txt` files.

### Before the session (with network)

1. Pull `main` and run `uv sync --locked` once. The first `uv run` builds the package, which can
   need the network, so do not leave this to the venue.
2. Download the folder `k8s-RemValGate/run-logs/replay145_20261115/` from the shared drive to a
   local folder, below called `<folder>`. It holds each run's manifest, transcript, labels or
   result row, the cut terminal capture (`.cast`), the saved playback (`.replay.txt`), `PLAYBACK.md`
   and a screen recording (`replay145_screen_recording_20261115.mp4`, 165 MB).
3. Do one dry run of each command below.

### In the session

From the repository root:

```
uv run python scripts/replay_run.py <folder>/<run id>.json --step
```

On Windows, if another project's virtual environment is active, clear it first
(`Remove-Item Env:VIRTUAL_ENV` in PowerShell), or `uv` warns and may use the wrong environment.

`--step` waits for Enter between the six parts: the injected fault, the observation, the
diagnosis, the K candidates with any refusal reason, the chosen action or the escalation, and the
outcome.

| Order | Run id | What it shows | Say |
|---|---|---|---|
| 1 | `notools-k8s_target_port-misconfig-mitigation-1-20261113t010400z` | TargetPortMisconfig on `user-service`; one patch, admitted, applied; RECOVERED in 126.6 s | The happy path: diagnose, check, apply, verify recovery. |
| 2 | `oracle-revoke_auth_mongodb-mitigation-1-20261113t204016z` | RevokeAuth diagnosed right on `geo`; K = 8 candidates, 5 refused by the allow-list (FLAG_DENIED 3, SHELL_METACHAR 2), 3 replies not parsed; nothing applied | The main failure: right diagnosis, fix refused. The allow-list blocks more fixes than the planner misdiagnoses. |
| 3 | `baseline7-user_unregistered_mongodb-mitigation-1-20261113t034101z` | UserUnregistered on `geo`; one `&&` command admitted by the pool runner; RECOVERED in 135.2 s | The only database run of #165 that recovered. Say up front that the planner's own check refused the chain as a whole (SHELL_METACHAR), while the pool runner splits a chain and admits it only when the allow-list admits every part (`check_chain` in `src/remvalgate/harness/allowlist.py`). The two paths treat chains differently; see the question on unapproved actions. |

Run 1 against 3c's run on the same problem and seed is the first example of the failure analysis:
3c named the wrong service and its `kubectl edit` was refused (`docs/failure_analysis.md`).

### Fallbacks, in order

1. The saved output: `<folder>/<run id>.replay.txt`.
2. The screen recording `.mp4` (runs 1 and 2 only; run 3 was added after it).
3. The terminal capture, if `asciinema` is installed (it was not on the machine that checked this
   page; the captures were played under WSL for #173):
   `asciinema play --idle-time-limit 2 --speed 2 <run id>.cast`. The captures were checked headless
   at `--speed 20` only, and each may open with the last line of the run before it (cut by time).

Limits to state if asked: the oracle run has no pod watch (the step 2 watch has no time stamps),
and the baseline7 run has none either (the step 1 watch ended at 01:20:29Z). Run 3's own check
refused its `&&` command; the pool runner admitted it, and the replay shows the runner's decision.

## 2. The story in five sentences

1. An agent that fixes Kubernetes faults must not apply a harmful command. We built a planner
   (Qwen3.5-4B with read-only tools and retrieval over past incidents) whose every command passes a
   deterministic allow-list, runs with a snapshot for rollback, and is judged by a recovery check.
2. The plan was a small fine-tuned gate that picks among the planner's K candidates. The headroom
   check (gate G3) failed twice: 0, then 1 problem where a better pick among the candidates could
   help, against 5 needed. So the gate was not trained.
3. After that failure the group fine-tuned the planner instead, as an exploratory ablation, decided
   after the results and disclosed as post hoc (BRD 1.17), with its rule fixed before its result
   (BRD 1.18).
4. That claim was falsified: 3cft 5 against 3c 7 problems at both seeds, 0 wins and 2 losses.
5. What holds: retrieval helps the planner, the deterministic check removes a quarter to a third
   of candidates, and the bottleneck is the allow-list and the planner's commands, not the choice
   among candidates.

## 3. Design

- **Problems.** AIOpsLab mitigation problems on a local kind cluster, two applications (Social
  Network, Hotel Reservation), five fault types: TargetPortMisconfig, ScalePodZero,
  AssignNonExistentNode, RevokeAuth (MongoDB), UserUnregistered (MongoDB).
- **Pool.** 16 problems in 4 folds; a 3-problem confirmation slice, sealed and opened once (#59);
  development problems for building; singleton types for an out-of-pool check (#73). Every pool
  read is a logged touch (`runs/touch_log.jsonl`).
- **Planner.** Qwen3.5-4B, thinking on, temperature 0.7, top-p 0.8, top-k 20, presence penalty 1.5,
  turn cap 16. Tools: telemetry tools or read-only kubectl, and optionally the error bundle.
- **Retrieval.** Configuration (iii): dense retrieval with a cross-encoder reranker over past
  incidents and a runbook, fixed under gate G5 before any evaluation run
  (`src/remvalgate/retrieval/fixed.json`).
- **Deterministic check.** An allow-list of verbs, flags, resources and MongoDB methods, applied to
  every candidate, with a reason for each refusal (R2.1).
- **Execution.** Snapshot, apply, 120 s settle, harm check across the namespace, recovery check,
  restore on failure (R2.2 to R2.4). Rollback covers Kubernetes objects only for every reported
  run; the database rollback (#187, BRD 1.21) was built afterwards and used by no reported result.
- **Run protocol (#161).** One commit, arms interleaved by problem, seeds 1000 and 1001, the model
  build recorded in every manifest. A problem counts as recovered only if it recovers at both
  seeds (#56).

## 4. Results to know by heart

### The fine-tuning comparison (`docs/primary_analysis.md`)

Run 2026-11-14 (#165 step 3), llama.cpp `61dbf49` on one A100 SXM 80 GB, each pool problem served
by the adapter of the fold that held it out.

| | 3c | 3cft | wins | losses | mid-p | Bonett-Price 95% |
|---|---|---|---|---|---|---|
| both seeds (the claim) | 7 | 5 | 0 | 2 | 0.25 | [-0.32, 0.10] |
| seed 1000 | 7 | 7 | 1 | 1 | 0.75 | [-0.22, 0.22] |
| seed 1001 | 10 | 8 | 1 | 3 | 0.375 | [-0.37, 0.15] |

Rule: supported with at least 5 wins and no loss, or 7 wins and 1 loss; falsified with 2 or more
losses or no more wins than losses. Result: **falsified.** Each loss is one seed's failure on a
problem the planner can solve (a turn cap; a refused `kubectl set`).

### Baselines

| Baseline | What | Result | When and source |
|---|---|---|---|
| 1 | fault-type rule, no model | 8 of 16 | 2026-11-09, seed 1000 only, before the #165 fixes (#150, `docs/rag_ablation.md`) |
| 2 | retrieval only | 8 of 16 | same run as baseline 1 |
| 3a, 3b, 3c | untuned planner; no retrieval, dense, dense plus reranker | 2, 6, 6 of 16 at both seeds | 2026-11-13, #165 step 1, LM Studio (`docs/ablations.md`) |
| 5 | fine-tuned ranking over 3c's candidates | 7.0 of 16 | 2026-11-14 (`docs/baseline5.md`) |
| 6 | prompted judge over 3c's candidates | 8 of 16, 3 escalated | 2026-11-15 (#185, `docs/primary_analysis.md`) |
| 7 | 3c told to escalate when unsure | 3 of 16 at both seeds | #165 step 1 (`docs/baselines_pool.md`) |

### Capability ablations (`docs/ablations.md`, descriptive, no test)

- **Retrieval, untuned:** 3a 2, 3b 6, 3c 6 of 16 at both seeds. **Fine-tuned (#34):** top pick
  solves 8 of 16 with retrieval, 3 without; working candidates 55 of 120 against 32 of 96. Not
  interleaved, so cluster drift is a confound.
- **Tool calling (2 by 2):** 3c 6, toolsbundle 8, kubectlonly 7, notools 9 of 16 at both seeds.
  The error bundle helps most; notools never hits the turn cap.
- **Deterministic check (R5.4):** removes 44 of 128 untuned candidates (34.4%) and 29 of 120
  fine-tuned (24.2%) that would otherwise reach the cluster unvetted.

### Failure analysis (`docs/failure_analysis.md`)

- Over all arms the allow-list refuses the fix more often than the planner misdiagnoses: 56 refused
  runs against 37 with a wrong fault type or service. Reasons: FLAG_DENIED 23, SHELL_METACHAR 18,
  VERB_NOT_ALLOWED 12.
- The MongoDB problems are blocked: the planner reaches for `adminCommand`, `dropUser`, `updateOne`,
  `sh -c`, which the allow-list does not admit. 37 of their 56 runs were refused.
- On TargetPortMisconfig the planner often names the wrong service.
- The fine-tuned planner hits the turn cap more often (5 against 2) and does not reduce refusals.

### Confirmation slice and singletons

- **Confirmation (#59, `docs/confirmation_check.md`):** 3c, opened once on 2026-11-15. 2 of 3
  problems at both seeds (`k8s_target_port` recovered at seed 1001 only); 5 of 6 runs. Every
  command admitted, none harmful. No inferential weight.
- **Singletons (#73, `docs/singleton_check.md`):** 0 of 8 runs (3c 0 of 4, baseline 2 0 of 4) on 2
  types. The planner's fault-type list holds the 5 pool types only.

## 5. Limits (say them before you are asked)

- Injected faults from one benchmark, two applications, n = 16. Only a large gain can pass.
- One run per arm, problem and seed; one arm's seed-to-seed change is 3 problems, the size of many
  differences reported.
- The fine-tuning comparison is exploratory: its rule was pre-specified, the decision to run it was
  post hoc. The BRD's primary claim (O1, the gate against baseline 5) was never tested and remains
  open (BRD 1.15, pending group confirmation).
- Training data is small: 6 to 9 working records per fold, none for MongoDB.
- Rollback covered Kubernetes objects only in every reported run; database user state was not
  restored.
- Baseline 6's judge shares the planner's model family, a confound, and it was not validated
  against human labels, so no Cohen's kappa is reported for it.
- Tool-call accuracy was not computed: the scorer of R2.7 exists in the harness but was not run on
  these results.
- The pool runner and the planner's check treat `&&` chains differently.
- Harm is detected from pod status only.
- MongoDB command output was not kept before #174, so failed database commands show only an exit
  code.

## 6. Likely questions

**What is your claim, and did it hold?** The reported claim: 3cft recovers more pool problems than
3c and loses at most one that 3c recovers. Falsified, 0 wins and 2 losses. The original claim, the
gate beating baseline 5, was never tested because the headroom check failed.

**Why did you not train the gate?** The headroom check counts problems where 3c's top pick fails
but one of its 8 candidates works. That bounds what any selector can gain. It was 0, then 1 after
the rerun; 5 were needed for a test at n = 16 to have a chance. Training a gate with at most 1
problem to gain cannot show anything.

**Was fine-tuning the planner a post-hoc change?** Yes, and it is disclosed as one (BRD 1.17). Its
pass and fail rule was fixed before the batch ended (BRD 1.18): two group members confirmed the
threshold and the both-seeds rule on #56, and the labels were adopted on #181 by a third group
member at 15:48 UTC, before the batch ended at 16:02 UTC.

**Your no-model rule recovers 8 of 16 and your planner 6 or 7. Why build a planner?** Baselines 1
and 2 ran once, at seed 1000, on 2026-11-09, before the fixes of #165, so they are not on the
both-seeds unit and not from the same commit, so they are not directly comparable. The honest
reading: on these five injected fault types a lookup by fault type is a strong baseline, and the
planner has not been shown to beat it. The planner was meant to reach types a rule does not
cover; the singleton check (0 of 8 for both 3c and baseline 2) shows neither does yet.

**Is 3c 6 or 7?** Both, from different configurations. 6 of 16 is #165 step 1, served by LM Studio
on the run host (`docs/ablations.md`). 7 of 16 is the 3c control of #165 step 3, served by
llama-server on the A100 (`docs/primary_analysis.md`). They are not pooled.

**Why is 3c the final system, when notools recovered 9?** 3c was chosen by the group, unanimously,
before the confirmation slice was opened (BRD 1.20, 1.23), so the slice would not confirm an arm
picked for being best on the pool.

**Why should anyone trust the confirmation result?** It is 3 problems, opened once, sealed before.
2 of 3 at both seeds, 5 of 6 runs. It says 3c handled each at least once; it carries no
inferential weight.

**How do you know you did not tune on the test problems?** Folds hold problems out of training and
retrieval, every pool read is logged in the touch log, retrieval configuration (iii) was frozen
before any evaluation run, and the confirmation slice was sealed and opened once.

**What does the allow-list do, and is it too strict?** It admits a fixed set of verbs, flags,
resources and MongoDB methods and refuses the rest with a reason. It is the largest failure class:
it refuses more fixes than the planner misdiagnoses, and it blocks every MongoDB fix the planner
proposes. Loosening it trades recovery for safety; we did not tune it on the pool.

**What is the unsafe or unapproved action rate?** An unsafe action is an applied command that
leaves a healthy workload unhealthy (APPLIED_HARMFUL, R2.4): 0 of the 224 runs of #165 step 1 and 0
of the 64 runs of step 3; 2 of the 120 fine-tuned candidates in the labels were harmful, both on
one problem. An unapproved command is one the allow-list refuses: 56 of 224 runs in step 1 and 15
of 64 in step 3 proposed one, and the allow-list stopped every one before it ran, so no unapproved
command was executed. Without the check, 34.4% of the untuned candidates and 24.2% of the
fine-tuned ones would reach the cluster unvetted. Harm is read from pod status only. One known
inconsistency: the pool runner admits a `&&` chain
when the allow-list admits every part, while the planner's own check refuses any chain
(SHELL_METACHAR). Demo run 3 is such a case: each part was an admitted command, but the planner path
would have refused it.

**What is the tool-call accuracy?** The scoring is defined and built (R2.7,
`src/remvalgate/harness/toolcall.py`): each call is compared with an expected call and is
malformed (unknown tool, missing or undefined argument, wrong type), wrong operation (wrong tool or
kubectl verb), wrong target (namespace, kind or name), or correct, checked in that order.
Accuracy is the share of correct calls. It covers operation and target, not parameter values such
as a patch body, since whether an action works is judged by executing it; the allow-list verdict
is a separate check. Answer plainly: tool-call accuracy was not computed. The scorer exists but
was not run on these results; the tool ablation reports diagnostics per arm (turns, refused turns,
tool calls by name, tokens) and its metrics are diagnosis accuracy and recovery
(`docs/tool_ablation.md`). Running the scorer is listed as future work.

**Why AIOpsLab?** Public, peer reviewed (MLSys 2025), maintained, and its mitigation task is
verified by execution and checks the whole system's health, not only the targeted service. The
cost: the agent writes free-form shell commands, not a short list of named actions, which is why
the allow-list exists.

**Why Qwen3.5-4B?** The fine-tuning recipes the design followed were validated on Qwen, and the
work it copied used Qwen3.5-4B; Qwen3.5 was the newest generation with a small size when chosen
(2026-10-02). It fits the local machine and one A100 for LoRA.

**Why not a larger planner?** The proposal's risk plan named one. Qwen3.5-9B ran on the 3
development problems (2026-11-10, seed 0, K = 8) against a rule fixed before the run: promising
only if it gave mixed outcomes on at least one problem. It gave none; on the hard target-port
problem it named the right fault but the wrong service, at about twice the time. A signal on 3
problems, not a test on the pool, and the pool was not spent on it.

**Is the idea new?** Checking an action before running it exists in self-adaptive systems, with a
learner proposing and a model checker verifying, but that needs a formal or simulatable model of
the system. The narrower aim here was a learned scorer for open-ended shell commands, where no such
model exists, with escalation and rollback, on real injected Kubernetes faults.

**Your main result is negative. Is that a failed project?** The course guidance, as a group member
reports from the TA, is that the rubric checks whether enough experiments were run with
fine-tuning, retrieval and the other components, and that a result showing one is not useful is
acceptable when reported. Each capability has an experiment: retrieval (3a, 3b, 3c and #34), tool
calling (the 2 by 2), the deterministic check (R5.4) and fine-tuning (3cft against 3c).

**Some results were run twice. Why?** A review of the tool ablation found three defects in code
every planner run shares: one fault type's description did not match its injector, the arms with
interactive tools got advice that discouraged reading telemetry, and the retrieval query held pod
age and restart counts, so repeated runs retrieved different fixes. Every planner result was made
again once (#165) after the fixes, under one run protocol. The old results stay in their pages,
marked superseded. Baselines 1 and 2 were not rerun, since the fixes do not touch them.

**What exactly does the allow-list check, and what is the sandbox?** kubectl is the only binary.
Verbs, resources and flags are each an allow-list in a data file, so an unlisted flag (kubeconfig,
context, impersonation, file input) is refused without a deny list to keep complete. The sandbox is
namespace confinement: exactly one namespace flag, equal to the problem's namespace; no
all-namespaces form. The text scan follows bash, not Python's `shlex`: operators (so `&&`),
redirection, globs, braces and comments outside quotes are refused, and `$`, backticks and
backslashes anywhere, because AIOpsLab's kind mode hands the command to a host shell where single
quotes are not inert. `exec` is admitted only as a mongo shell call using a short list of additive
methods (create a user, grant a role, read users). A patch body must be JSON with no denied keys.
Each refusal carries one reason, tested in a fixed order. Known limit: which roles a grant may give
is not restricted. Rejected: a server-side dry run (needs a cluster), `shlex` (reads `$(id)` as
plain text that the host shell would run), a deny list.

**How is an outcome classified?** Six classes (R2.4). An applied run is harmful if harm was detected
(pod status across the namespace, before and after, read after the 120 s settle window), else
recovered if the injected fault is gone, else ineffective. Harm is tested first, so an action that
fixes the fault and breaks something else is harmful, not recovered. An escalation is "with a
working candidate" or "with none" by the oracle label, or "unlabelled" when there is no label.
Escalations count as not recovered. A restored action is never recovered, and its harm is read
before the restore. "Recovered" is not AIOpsLab's own success flag, which can fail on a pod that
was unhealthy before the action.

**How do you know each result is what you say it is?** Every run writes a manifest that is never
overwritten: commit and whether the tree was clean, model name, version and date, seed, K, the
pinned AIOpsLab commit, the cluster, the problem and its slice, and a UTC time. A run on a pool or
confirmation problem first passes one entry point that appends to the touch log and reads it back;
it refuses an unknown problem, a reused run id, an unpinned AIOpsLab, a dirty tree or a damaged log.
Known limit: parallel runs appending to one log are not coordinated.

**How was the confirmation slice protected?** Three new problems, one each for TargetPortMisconfig,
ScalePod and AssignNonExistentNode, all on `social-graph-service`, which no other problem uses.
Sharing one service left enough free services for the pool extension, at the cost of less
independent outcomes. Each problem is pinned by a SHA-256 over its definition, fault, service,
namespace, known fix and the AIOpsLab commit; a test fails if a definition changes. Each was
validated once with its known fix only, then sealed, and opened once.

**Which retrieval models?** Dense: `BAAI/bge-small-en-v1.5` (33M parameters), cosine similarity.
Reranker: `BAAI/bge-reranker-base`, over the dense top 5, since a fold corpus has under 20 entries
and a full rerank would make (iii) and (iv) rank alike. Both pinned to a revision. All
configurations rank the same entry text (fault type, service, summary, no fix command) from the
fold that holds the problem out.

**Intrinsically, plain dense retrieval scored best. Why use the reranker?** True. On the 16 pool
queries in the query form the runs use (#164), a retrieved entry of the right fault type is first
for 3 queries (Hit@1) and in the top 3 for 7 with dense retrieval (MRR 0.381), and 3 and 6 with
the reranker (MRR 0.359): the reranker does not improve on dense retrieval here. On the raw query
form of 2026-11-05 Hit@3 was 9, 7 and 6 for dense, dense plus reranker and hybrid
(`docs/intrinsic_retrieval.md`). Configuration (iii) was fixed under gate G5 before any query was
scored, and with 16 queries one query moves Hit@3 by 6 points, so no claim about the reranker is
made and (iii) was not changed after seeing results. Extrinsically, the untuned planner recovers 2
problems at both seeds without retrieval and 6 with either.

**What are baselines 6 and 7?** Baseline 6 is a prompted judge: each admitted candidate gets a
verbalised probability from 0 to 1, seeing the task, the diagnosis and the command, never an
oracle label; the top score at or above a threshold is applied, else it escalates. It is the
planner's own model at temperature 0, so they share a model family. Baseline 7 adds the "Specified
Quit" instructions of Bonagiri et al. 2025 (arXiv 2510.16492, appendix C.3) to the planner's
prompt, so it may escalate with a reason; with the option off the prompt is unchanged byte for
byte.

**How was the planner fine-tuned?** Per fold, LoRA rank 16, alpha 32, dropout 0.05, on the
attention and MLP projections; AdamW, learning rate 1e-4, batch size 1, 3 epochs, seed 0; no
hyperparameter search. Trained only on records whose command recovered the problem (label 1),
since a language-model loss on a failed command raises its likelihood (`docs/baseline5.md`,
`docs/finetuned_planner.md`). The same adapters score baseline 5.

**Why did the database problems fail? Was the login missing?** No: the commands carry the login.
They use methods and characters the allow-list does not admit (`adminCommand`, `dropUser`, `sh -c`,
`&&`), and before #174 a failed command kept only its exit code.

**Give one failure you can walk through.** Two worked examples from the report:
- The bundle names the service. On the first TargetPortMisconfig problem at seed 1000, 3c named
  `social-graph-service`, not the faulty `user-service`, and proposed
  `kubectl edit service social-graph-service`, which the allow-list refused as an interactive verb.
  notools, on the same problem and seed, read from the error bundle that the `user-service` Service
  forwards to target port 9999 while its pods listen on 9090, sent a JSON patch setting the target
  port to 9090, and recovered.
- A right diagnosis, a refused fix. On the first RevokeAuth problem at seed 1001, 3c named the
  fault and the geo database correctly, logged in as root, and called `db.adminCommand`, which is
  not among the admitted MongoDB methods (`getSiblingDB`, `createUser`, `grantRolesToUser`,
  `getUser`, `getUsers`); nothing ran.

**What would you do with more time?** The report's future work:
- Review and widen the MongoDB methods the allow-list admits, and admit a chained fix as a
  candidate when each part is admitted, as the runner does.
- Make the error bundle the planner's default, since it led on the problems where the planner named
  the wrong service.
- Fine-tune on fixes verified by execution for the fault types the planner never solves, such as
  the database faults, not only on its own successes.
- Compute tool-call accuracy with the existing scorer, and validate any judge against human labels
  with Cohen's kappa.

Then rerun the headroom check before training a gate.

**How much compute?** One A100 pod for about 12.7 hours at $2.19 per hour (training, merging,
serving); training 0.07 to 0.13 GPU hours per fold; the rest on a local machine with LM Studio
(`docs/compute.md`).

**Can you reproduce a number?** Every run has a manifest with commit, model build hash and seed;
archives with SHA-256 sums are on the shared drive (listed in `docs/primary_analysis.md`); the
replay above plays any recorded run from its files.

## 7. Rehearsal checklist

- [ ] Each group member ran the replay of runs 1 and 2 on their own machine.
- [ ] Each group member can tell the five-sentence story without notes.
- [ ] Each group member answered every question in section 6 once, aloud, to another member.
- [ ] Each group member can name the claim's numbers: 7, 5, 0 wins, 2 losses, mid-p 0.25.
- [ ] The final report (#62) is checked against this page, and differences are fixed here
  (draft #192 at `d90ce60` checked on 2026-11-15; recheck once it is merged).
