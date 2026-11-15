# Cluster setup

How to set up the local kind cluster, deploy AIOpsLab's applications, apply the project's problem
overlay and install the telemetry the planner's tools read. The [README](../README.md) links here
from its setup and reproduction sections.

## AIOpsLab

Decided in #65: AIOpsLab is an unmodified git submodule pinned at a commit, and new problem
definitions live in an overlay in this repository. No tracked file inside the submodule is
changed. A fork was rejected because our code would then live in a second repository, against R1.6.

AIOpsLab commit: `576c3f080c7ae2459a972afd4a2f8d5f01828430` (upstream `main` on 2026-10-20), at
`external/AIOpsLab`. Its nested `aiopslab-applications` is pinned by AIOpsLab at `8038be6`. The
run manifest records this hash: callers pass `remvalgate.aiopslab_pin.pinned_commit(repo)` as
`aiopslab_commit` to `start_run`, which reads the committed link and not the checkout.

### Local cluster

A two node kind cluster from AIOpsLab's ARM config on Docker Desktop:

```
cd external/AIOpsLab
kind create cluster --config kind/kind-config-arm.yaml    # context kind-kind; do not pass --name
cp aiopslab/config.yml.example aiopslab/config.yml        # then set k8s_host: kind (gitignored upstream)
cd ../..                                                  # back to the repository root
```

After the nodes are stopped, `docker start kind-control-plane kind-worker` brings the cluster back.
The `media-frontend` init container clones a repository from GitHub on every start, so the
cluster needs access to GitHub and the pod can take about 15 minutes to be Ready (measured once).
Then wait for all pods Ready and compare the image digests below with the cluster again.

The verified run used the ARM config. Upstream also ships `kind/kind-config-x86.yaml`, which was
run once, on the backup host (`docs/backup_host.md`); its digests are below.

`helm`, `kind` and `kubectl` must be installed. Versions of the verified run: kind v0.33.0,
kubectl client v1.36.1, helm v4.3.1, Docker Desktop 4.96.0 (engine 29.9.1, aarch64). The cluster
images are mutable tags, several of them `latest`, so a later pull can differ. Their digests are
recorded here (BRD 7.3): the node image, and the `imageID` the cluster reported for every container
of the 27 Social Network pods after the verified run on 2026-10-27.

- node image `jacksonarthurclark/aiopslab-kind-arm`: `sha256:9723c6d45f5e1b9af0975987801543f36f529e6caff2575d9ba55d048f38977c`
- `deathstarbench/social-network-microservices:latest`: `sha256:a845266e98ce15d204ee7b98d41a2a95820af525013ce70f14104f5b9b5e92b6`
- `jacksonarthurclark/media-frontend:latest` (Social Network on ARM): `sha256:825c601f648aadd5eec08bf0cf2b4154712deec9dcd331796852c879dc658039`
- `jaegertracing/all-in-one:1.57`: `sha256:8f165334f418ca53691ce358c19b4244226ed35c5d18408c5acf305af2065fb9`
- `library/memcached:1.6.7`: `sha256:007b9ca55759c6ef3dcc9a08be6b45b31147bdd1db28358280fd62360387b9dd`
- `library/mongo:4.4.6`: `sha256:6efa052039903e731e4a5550c68a13c4869ddc93742c716332883fd9c77eb79b`
- `library/redis:6.2.4`: `sha256:6bc98f513258e0c17bd150a7a26f38a8ce3e7d584f0c451cf31df70d461a200a`
- `yg397/openresty-thrift:xenial`: `sha256:6c295fda3c2f6b941d85ebf16421bafac0dcf72450c96f53a2d031b29d8c81ef`
- `alpine/git:latest` (init container): `sha256:e8ceedc73bd80992ae8d58a7d24c58b8322146c354f1d2e81a97db4452068ec0`

Two of these images have no `linux/arm64` build: `deathstarbench/social-network-microservices` and
`yg397/openresty-thrift`. On the arm64 kind nodes they run under x86 emulation: `crictl inspecti`
reports `amd64` for both, and a `UserService` process maps `/run/rosetta/rosetta`. The other
images, and `media-frontend` which AIOpsLab swaps for an ARM build, are `arm64`. The timing and
memory figures below were measured under that emulation, so Docker Desktop's amd64 emulation is
part of the cluster configuration. The Rosetta handler was registered in the Docker VM and in use,
and no Rosetta key was set in Docker's `settings-store.json`, so the setting was at its default.
Without amd64 emulation in Docker, these pods were not tried and are expected not to start.

The x86 config ran on the backup host on 2026-11-10. Six of the container images above reported the
same digests there: all but `media-frontend`, which AIOpsLab swaps only on ARM, and
`alpine/git:latest`, whose tag had moved.

- node image `jacksonarthurclark/aiopslab-kind-x86`: `sha256:d631857278d3f8ce5c36364c75ec25695ceb22e311ec6621a4ebf5506b86774d`
- `yg397/media-frontend:xenial` (Social Network on x86): `sha256:272049995f2929435b91da34f20fc16216a7f974d1b8f9afa0ccbb529f1a9840`
- `alpine/git:latest` (init container): `sha256:6a1fdb962814ca11508ca8d1bab94375b5b7ac4e4a9a40981c29fc32c02f095d`

Regenerate the list after a redeploy with
`kubectl --context kind-kind get pods -n test-social-network -o jsonpath='{range .items[*].status.containerStatuses[*]}{.image} {.imageID}{"\n"}{end}' | sort -u`
(and `initContainerStatuses` for the init containers).

Docker Desktop was set to 12 GB of memory. Social Network (27 pods) used 2.0 GiB across both
nodes shortly after all pods were Ready and 2.3 GiB about an hour later. The peak during the image pull was not
sampled.

**The checkout path must contain no spaces.** At the pinned commit AIOpsLab builds `helm` and
`kubectl` commands as unquoted shell strings, so a space in the path fails, for example with
`helm install` reporting `expected at most two arguments`. Quoted, `helm` itself handles spaces. A
symlink does not help, because AIOpsLab resolves its own path. Clone or move this repository to a
path without spaces before running anything against the cluster.

### Deploy and health check

Deploy the sample application with the script, which calls `SocialNetwork().deploy()` and injects
no fault. Never use `Orchestrator.init_problem` for this, because it also injects a fault.

```
uv run python scripts/deploy_social_network.py
kubectl --context kind-kind wait --for=condition=Ready pods --all -n test-social-network --timeout=900s
```

The health check passes when `kubectl wait` exits 0. AIOpsLab's own `deploy()` waits 300 s and
raises on a slow first image pull, which is not a failure: `SocialNetwork.deploy` calls
`Helm.assert_if_deployed`, which calls `KubeCtl.wait_for_ready(namespace)` with `max_wait=300`. A
first pull took about 5 minutes, and a repeat about one. All 27 pods were Ready with no restarts.

`Helm.install` runs `helm dependency update` inside the nested `aiopslab-applications` chart
folder. After the deploy, `git status --porcelain -- external/AIOpsLab` at the repository root
printed nothing (checked 2026-10-27), so the deploy does not dirty the pin. The run manifest also
refuses a pool, confirmation or singleton run while the repository has any uncommitted or untracked file, so
check `git status --porcelain` before such a run.

The four RevokeAuth and UserUnregistered pool problems run on AIOpsLab's Hotel Reservation
application, in `test-hotel-reservation` ([docs/problem_inventory.md](problem_inventory.md)).
Deploy it beside Social Network with

```
uv run python scripts/deploy_hotel_reservation.py
kubectl --context kind-kind wait --for=condition=Ready pods --all -n test-hotel-reservation --timeout=900s
```

On 2026-10-31 the first deploy, with every image pulled, took about 4 minutes: 19 pods Ready and 6
PersistentVolumeClaims bound to the default `standard` storage class. Both nodes then used about 3.2
GiB with the two applications deployed. The pin stayed clean. The manifests name three images by a
moving tag, so a later pull can differ; the digests in use were:

- `yinfangchen/hotelreservation:latest`: `sha256:c46c018d412ab466c4cb3ae93b1aaf1a373968e2fafc2767f15539f50cd1683a`
- `library/mongo:4.4.6`: `sha256:6efa052039903e731e4a5550c68a13c4869ddc93742c716332883fd9c77eb79b`
- `library/memcached` (no tag): `sha256:aea0d81d101a9b64fe30cf2cf2f96144e284f8e9552bb084a03f4e12e9043bab`
- `hashicorp/consul:latest`: `sha256:259fd02f2544fe1aa03fa49efd3b105c8dd67106d710a72f5e898110266356f7`
- `jaegertracing/all-in-one:1.57`: `sha256:8f165334f418ca53691ce358c19b4244226ed35c5d18408c5acf305af2065fb9`

### Overlay

New problem definitions go in `src/remvalgate/overlay/`. AIOpsLab builds its problem registry
inside each `Orchestrator` and has no hook for outside entries, so an overlay entry exists only in
an Orchestrator built with `remvalgate.overlay.new_orchestrator`. A bare `Orchestrator()` and
AIOpsLab's own `cli.py` and `service.py` do not see it. An id that already exists upstream is
refused. The shipped overlay holds the three development problems
([docs/development_problems.md](development_problems.md)), the pool variant of
AssignNonExistentNode ([docs/problem_inventory.md](problem_inventory.md)), the three
confirmation problems ([docs/confirmation_problems.md](confirmation_problems.md)) and the pool
extension problems ([docs/pool_extension.md](pool_extension.md)), defined in
`src/remvalgate/overlay/dev_problems.py`. The known fixes are in
`src/remvalgate/overlay/fixes.py`.

`scripts/check_overlay_registry.py` is the hand check that a test entry appears in a newly
constructed Orchestrator. `scripts/validate_overlay_problems.py` validates the overlay problems it
is given, or with none given every overlay problem except the confirmation slice: for each it logs a
creation touch, injects the fault, checks that the mitigation eval fails, runs the known-fix
`kubectl` command, checks that the eval passes and recovers the fault. A confirmation problem is
created once: `log_creation` refuses a second creation touch for it. Run it with Social Network
deployed and Ready. Both need the cluster, so CI does not run them.

`scripts/run_dev_end_to_end.py` runs one development problem end to end (story E1-S3, R1.3). A
scripted agent sends the known fix as `exec_shell` and `submit` actions through the Orchestrator's
parser and action handlers, the evaluator must report success, and the cluster after the reset
must equal its snapshot before the injection (the spec and labels of the Services and
Deployments, with all pods Ready). A second cycle injects and resets without the fix,
because a reset after a correct fix repairs nothing. It times each phase and writes a run manifest
and the timings under `runs/manifests/`, which git ignores. It does not use
`Orchestrator.init_problem` or `start_problem`: they install OpenEBS and Prometheus from the
network, redeploy the application and delete it at the end. The recorded times are in
[docs/compute.md](compute.md).

`scripts/run_restore_check.py` is the hand check for snapshot and restore (story E2-S2, R2.2). For
TargetPortMisconfig, ScalePod and AssignNonExistentNode it injects the fault, then runs the known
fix, a damaging command and a delete of the object, and after each restore checks that the
Services and Deployments equal their state before the command. It compares specs and labels and
does not wait for pods to become Ready. Run it with Social Network deployed and Ready; CI does not
run it.

`scripts/run_execution_check.py` is the hand check for the execution loop (story E8-S2, R8.2):
snapshot, apply, harm after the settle window, the readiness check and probes, and a restore with
escalation when the check fails. It runs the ScalePod known fix (kept, RECOVERED), a wrong target
port (restored, APPLIED_INEFFECTIVE) and a patch that breaks another Deployment during an
AssignNonExistentNode fault (restored, APPLIED_HARMFUL), and checks that each restore leaves the
Services and Deployments as they were after the injection. `--settle` shortens the 120 second
window. Run it with Social Network deployed and Ready; CI does not run it.

`scripts/run_pilot.py` is the five-rollout timing pilot (story E9-S1, gate G2). It runs the known
fix of the three development problems, then the first two again, through the Orchestrator's parser
with a scripted agent, and times each phase: inject, pod status before the action, snapshot,
execute, one `detect_harm` settle window and reset. It writes a run manifest per rollout and a
summary under `runs/manifests/`, which git ignores, and logs no touch. The result is in
[docs/compute.md](compute.md). Run it with Social Network deployed and Ready; CI does not run
it, and it takes about 12 minutes.

`scripts/run_planner_dev.py` runs the planner (stories E5-S1, E5-S5 and E5-S2) on the development
problems. For each problem it injects the fault, runs the problem's `wrk2` workload (traffic for traces), takes one
read-only look at the cluster, and runs the loop in `src/remvalgate/planner/`, which first retrieves
3 past fixes with configuration (iii) (needs `uv sync --group retrieval`): at most 16 turns (8
before 2026-11-08, when 5 of 6 TargetPort pool problems hit the cap),
one JSON object per turn, tool calls through the Orchestrator's parser. The tools are `exec_shell`
limited to `kubectl get` and `describe`, `get_logs`, `get_metrics` and `get_traces`. The last two
save files and return a path, so the model also gets a bounded summary of what they saved
(`src/remvalgate/planner/telemetry.py`). The planner returns a diagnosis (a fault type from a
closed list and a service name) and one command, which the allow-list checks and the script never
runs. It then samples K candidate commands from that diagnosis (`--candidates`, default 8, the
submitted proposal's value): one model call each, with seeds `--seed` to `--seed` plus K minus 1, on
the diagnosis conversation with the accepted answer's command left out. Earlier turns, a refused
final answer among them, and the retrieved past fixes are still seen by every draw. Each
candidate is admitted by the allow-list or rejected with a reason, and none is run. A sampled
command, or the plan's own final command, that ends in one stray closing brace after its last
single quote, with balanced quotes, brackets and braces otherwise, has that brace stripped before
the check; the candidate or the plan records the command as the reply held it in `raw_command`
(None when nothing was stripped), and the run summary prints how many were stripped. The
allow-list reads a deployment or replica set type written with its group (`deployment.apps/web`,
`deployments.v1.apps web`) as the plain type, as kubectl does, and only for those two. Each candidate
also records the sum of its token log-probabilities when the server returns them; LM Studio's chat
completions endpoint lists no `logprobs` parameter, so expect none there. If every candidate is
rejected (by the allow-list, or because the reply held no command or was cut), the planner draws
K more exactly once, with seeds `--seed` plus K to
`--seed` plus 2K minus 1, and the case is escalated if the second draw fails too; later steps
use the last draw's candidates only. The transcript records every candidate with its draw number (1 or 2), the number
of draws and whether the case escalated. A plan with no
diagnosis draws nothing and is recorded as escalated; a baseline 7 run draws nothing either, and
is recorded as escalated only when its plan escalated. The manifest
records K and the sampling settings. The script then checks that the cluster did not change,
deletes the workload job and resets.
The model is any OpenAI-compatible server (default LM Studio on port 1234); the thinking switch is
sent as `reasoning_effort: "none"`, which LM Studio honours and ignored the other fields tried.
`--model-version` is required and goes into the manifest with the thinking setting. `--model`
names the served planner, `qwen/qwen3.5-4b` by default or `qwen/qwen3.5-9b` (#154), here and in
`scripts/run_oracle_labels.py` ([docs/larger_planner_check.md](larger_planner_check.md)). Run
it with
Social Network deployed and Ready, Prometheus installed (below) and the model loaded; CI does not
run it. Keep the laptop awake and plugged in: a sleep stalled the first attempt and left a fault
injected.

`scripts/record_pool_queries.py` records one post-injection query per pool problem (story E3-S7,
R3.7): it injects the fault, runs the workload, takes the planner's first observation the same way
and resets. Each problem is a pool run with a touch, so it needs gate G1 and a clean checkout. The
outputs go to `runs/queries/`, which git ignores, and to the shared drive
([docs/pool_queries.md](pool_queries.md)). Run it with Social Network and Hotel Reservation
deployed and Ready; CI does not run it.

`scripts/run_intrinsic_retrieval.py` scores the four retrieval configurations on those queries
(story E4-S3, R4.3): Hit@1, Hit@3, MRR and fault-type Recall@3 per configuration. It reads the
query files from `runs/queries/`, needs the retrieval dependency group and no cluster, and each
problem is a pool run with a touch, so it needs gate G1 and a clean checkout. The outputs go to
`runs/intrinsic/`, which git ignores ([docs/intrinsic_retrieval.md](intrinsic_retrieval.md)).
CI does not run it.

### Telemetry for `get_metrics` and `get_traces`

- **Traces.** The Jaeger client keeps 1 percent of requests, so the script's workload is 100
  requests per second for 30 s (3000 requests), then it waits 12 s for the tracer to flush. The
  problem's own workload is 10 per second for 10 s and left no traces. The
  `deathstarbench/wrk2-client` image ran on the arm64 nodes. The job and its ConfigMap are in the
  `default` namespace and the script deletes them.
- **Metrics.** `get_metrics` queries Prometheus at `localhost:32000`. The kind config maps no host
  ports, so the script starts `kubectl port-forward -n observe svc/prometheus-server 32000:80`.
  Install Prometheus once with AIOpsLab's own deploy. Its volume claim names the StorageClass
  `openebs-hostpath`; this repository created that class on the cluster's existing local-path
  provisioner instead of installing OpenEBS. The install took about 2.5 minutes and adds the
  `observe` namespace (Prometheus, kube-state-metrics, node exporters, a pushgateway):

```
kubectl --context kind-kind apply -f - <<'YAML'
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: openebs-hostpath
provisioner: rancher.io/local-path
reclaimPolicy: Delete
volumeBindingMode: WaitForFirstConsumer
YAML
uv run python -c "from aiopslab.service.telemetry.prometheus import Prometheus; Prometheus().deploy()"
```

`scripts/run_baselines_smoke.py` runs baselines 1 and 2 (story E6-S1, R6.1) on each development
problem. Each baseline reads the cluster state, returns one command or escalates, the command goes
through the allow-list and the Orchestrator's `exec_shell` handler, and the outcome is classified
after the settle window. Baseline 1 retrieves nothing; baseline 2 retrieves with configuration
(iii), so the script needs `uv sync --group retrieval`. The baselines are in `src/remvalgate/baselines/`. Nothing is appended to
the touch log and no manifest is written, since a smoke run reports no result. It needs the cluster,
so CI does not run it; CI tests the baselines on synthetic cluster listings.

`scripts/run_planner_dev.py --baseline 3a|3b|3c` runs baselines 3a, 3b and 3c (story E6-S2,
R6.2): the untuned planner, zero-shot with no gate, with retrieval configuration (i), (ii) or (iii)
(`src/remvalgate/baselines/untuned.py`). Its action is the planner's own command: no candidates
are sampled, and `--candidates`, `--judge` and `--escalate-when-unsure` are refused. After the
diagnosis check the command goes through the allow-list and the Orchestrator's `exec_shell`
handler and is classified after the settle window, with the same code as baselines 1 and 2; a
turn-capped plan or a rejected command is classified as not applied. The reset is then compared
with the clean state. With configuration (i) the task prompt has no past-fixes section. On the
development problems all three use the synthetic stub corpus, whose 5 entries are no more than the
rerank depth, so 3b and 3c differ only in the reranker's order there and a run checks the wiring,
not the ranking. 3b and 3c need `uv sync --group retrieval`. Their pool results come from the
baseline-only run (#53).

`scripts/run_oracle_labels.py` labels baseline 3c's K candidates on the pool problems (story
E3-S5, R3.5). Per problem it runs 3c's planner once with retrieval configuration (iii) over the
fold corpora (`pool_retriever` in `src/remvalgate/retrieval/fixed.py`), samples K candidates
from its diagnosis, merges near-identical ones, and executes each remaining command, and the
plan's own command apart, on a new injection, with a harness reset after each that is checked
against the clean and the saved post-injection state. Each execution is classified as for the
baselines; the labels, the problem's flag, the mixed outcomes and the coverage for k = 1 to K
are in `src/remvalgate/harness/oracle.py`. Each pool problem is a pool run with a touch, so it
needs gate G1 and a clean checkout; development ids run the same way with no touch, as a dry run.
Run it with both applications deployed and Ready, Prometheus installed and the model loaded; CI
does not run it. `scripts/summarize_oracle_labels.py` summarizes labels files without a cluster.
The method and the rules are in [docs/oracle_labels.md](oracle_labels.md).

`scripts/run_baselines_pool.py` is the baseline-only run on the pool (story E9-S2, #53, gate
G3). It runs the arms chosen with `--baseline`, which repeats: baselines 1, 2, 3a, 3b, 3c and 7,
the tool ablation arms `notools`, `toolsbundle` and `kubectlonly`, and the fine-tuned `3cft`. Each
runs once per problem and seed, at seeds 1000 and 1001 unless `--seed` is given, with the arms
interleaved by problem, and each action is executed with the labelling run's procedure in the
problem's own namespace. Baseline 3c there is the rerun for the R check.
`scripts/summarize_g3.py` reads the labels and the result files with no cluster and prints the
headroom result, R and the minimum gain (`src/remvalgate/harness/g3.py`). The run and the rules
are in [docs/baselines_pool.md](baselines_pool.md).

`scripts/summarize_rag_ablation.py` reads the result files of baselines 3a, 3b and 3c from the
baseline-only run with no cluster and prints diagnosis accuracy and single-action recovery for
each retrieval configuration, with paired comparisons (story E4-S4, #33,
`src/remvalgate/harness/rag_ablation.py`). The rules and the results are in
[docs/rag_ablation.md](rag_ablation.md).

`scripts/export_training_data.py` builds the per-fold training data of the gate and of baseline 5
from the 16 labels files of the pool run (story E7-S1, R7.1), with no cluster and no model: fold
f's files hold the candidates of the 12 problems outside fold f, with held-out records masked in
the retrieved past fixes. Reading the labels logs one touch per fold and training problem, so it
needs gate G1 and a clean checkout. The rules and the file format are in
[docs/training_data.md](training_data.md). `scripts/check_length_proxy.py` compares the
export's tokenizer-free length proxy with a saved tokenizer on synthetic text of the gate input's
shape, with no pool data and no touch; the result is in the same file (Length check).

Baseline 5 (story E6-S4, R6.4) is the planner LoRA-fine-tuned per fold on the label-1 training
records, ranking its own K candidates by log-probability. `scripts/export_baseline5_heldout.py`
writes each fold's held-out scoring input from the same 16 labels files, logging one touch per pool
problem. `scripts/baseline5_kaggle.py` trains and scores one fold on a GPU notebook, and
`scripts/summarize_baseline5.py` gives the rankings, coverage curves and top-1 results beside
baseline 3c's. The rules are in [docs/baseline5.md](baseline5.md). The fine-tuned planner
itself, served from the GPU host as the 3cft arm, is in
[docs/finetuned_planner.md](finetuned_planner.md).

`scripts/summarize_finetune.py` is the exploratory fine-tuning comparison of story E9-S5 (#56): 3cft
against the 3c control, per seed and with both seeds, against the minimum gain of gate G3 (threshold
confirmed on #56), labelled supported, not supported or falsified; see
[docs/primary_analysis.md](primary_analysis.md). The ablations of every capability are
collected in [docs/ablations.md](ablations.md) (#57). `scripts/failure_cases.py` classifies why
each planner run failed (#58, [docs/failure_analysis.md](failure_analysis.md)). The singleton check (#73) is in
[docs/singleton_check.md](singleton_check.md), and the
confirmation check (#59) in [docs/confirmation_check.md](confirmation_check.md).

`scripts/ablate_no_check.py` is the ablation without the deterministic check (story E5-S4, R5.4).
It takes the manifests of planner runs that sampled candidates, one run per problem, all of one
slice, one model and one sampling setup, applies the allow-list again to every sampled candidate in
each transcript, and reports the share removed by reason (`src/remvalgate/harness/no_check.py`).
Nothing is run, so it needs neither the cluster nor a model server. The headline counts the
candidates a run without the check would have drawn: the first draw, plus the second when every
first-draw reply had no command, since such a draw is resampled with or without the check. Both
draws are reported too, and replies with no command are counted apart, since the check never saw
them. The inputs are checked before any touch. For a pool problem, reading its transcript is a
touch: each gets a `nocheck-` run through `start_run` before it is read, so it needs gate G1 and a
clean checkout. A recorded verdict that the policy file no longer gives, or a transcript that
disagrees with its manifest or lacks a field the analysis reads, refuses the report after that
touch, which is kept, and no summary is written. A run whose manifest records no seeds, as when its
plan had no diagnosis, is listed in the summary's `no_candidates_run_ids` and is not touched. A
manifest per problem, its `.nocheck.json` result and a pooled summary that names the runs, the
model and the sampling setup go to `runs/manifests/`. The pool's sampled candidates come from the
oracle labelling run (#27).

`scripts/run_baselines_pool.py --baseline notools` is the ablation without interactive tools
(story E5-S3, #38, R5.3), and `scripts/summarize_tool_ablation.py` compares it with baseline 3c
with no cluster (`src/remvalgate/harness/tool_ablation.py`). The telemetry bundle and the rules
are in [docs/tool_ablation.md](tool_ablation.md).
