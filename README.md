# k8s-RemValGate

A small fine-tuned validity gate for agentic Kubernetes incident remediation. An LLM planner samples
several candidate fixes for an incident, the gate scores whether each one will restore the service,
and the system applies the best one only if it clears a calibrated threshold, otherwise it escalates
to a human. The system is evaluated on AIOpsLab mitigation problems against seven baselines.

This is a group project for the course Language Models and Agents (CL3410).

## Status

Planning. The requirements, epics and acceptance criteria are in [docs/brd.md](docs/brd.md), and the
work is tracked as issues on this repository's GitHub Project. The repository layout and environment
are in place; the system itself is not written yet.

## Layout

```
src/remvalgate/   the package
tests/            unit tests (pytest)
scripts/          standalone scripts run outside the package, such as the GPU smoke test
docs/             requirements and project notes
external/         AIOpsLab, a pinned submodule, unmodified
scripts/          hand-run cluster scripts, not run by CI
pyproject.toml    project metadata, dependencies, pytest and ruff settings
uv.lock           the dependency lockfile
requirements.txt  plain pip export of the lockfile, for Colab
requirements-retrieval.txt  pip export of the optional retrieval group
```

## Setup

Python 3.11 or 3.12 is required, the range AIOpsLab supports. `.python-version` selects 3.12.
Dependencies are managed with [uv](https://docs.astral.sh/uv/) and locked in `uv.lock`.

AIOpsLab is a git submodule at `external/AIOpsLab` and is installed as an editable path
dependency, so the environment cannot be built without it. Clone with the nested submodules, which
hold the application charts a cluster run needs:

```
git clone --recurse-submodules <repository url>
git submodule update --init --recursive   # in an existing clone, and after pulling a pin change
```

Unit tests and lint only need `external/AIOpsLab` itself, which is what CI checks out.

```
uv sync --locked                 # install from the lockfile; fails if the lock is stale
uv run pytest                    # unit tests
uv run ruff check .              # lint
uv run ruff format --check .     # formatting
```

The retrieval models (embedder and reranker, which pull in torch) are in the optional `retrieval`
group, so CI and the unit tests never install or download them. Install them with
`uv sync --group retrieval`. On Linux the locked torch is the CUDA build, which brings about
twenty nvidia packages and downloads several GB. On a CPU-only Linux machine, install the CPU
build first and leave the CUDA lines out of the export:

```
pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu
grep -v -E '^(nvidia|cuda|triton)' requirements-retrieval.txt > retrieval-cpu.txt
pip install -r retrieval-cpu.txt
```

On Colab or Kaggle, `pip install -r requirements-retrieval.txt` installs the group at the locked
versions, torch, transformers and tokenizers included, so a run there loads the models with the
same libraries as the lock. It replaces the notebook's preinstalled torch with the locked one.

After changing dependencies, run `uv lock` and regenerate both pip exports:

```
uv export --format requirements-txt --no-hashes --no-emit-project --no-header -o requirements.txt
uv export --only-group retrieval --format requirements-txt --no-hashes --no-emit-project --no-header -o requirements-retrieval.txt
```

The export includes the dev group, so the tests run on Colab too. It leaves out the project itself,
which is installed separately:

```
pip install -r requirements.txt
pip install -e .
pytest
```

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

Only the ARM config was run. Upstream also ships `kind/kind-config-x86.yaml`, which was not run
here, and its node image digest is not recorded.

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
refuses a pool or confirmation run while the repository has any uncommitted or untracked file, so
check `git status --porcelain` before such a run.

The four RevokeAuth and UserUnregistered pool problems run on AIOpsLab's Hotel Reservation
application, in `test-hotel-reservation` ([docs/problem_inventory.md](docs/problem_inventory.md)).
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
([docs/development_problems.md](docs/development_problems.md)), the pool variant of
AssignNonExistentNode ([docs/problem_inventory.md](docs/problem_inventory.md)), the three
confirmation problems ([docs/confirmation_problems.md](docs/confirmation_problems.md)) and the pool
extension problems ([docs/pool_extension.md](docs/pool_extension.md)), defined in
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
[docs/compute.md](docs/compute.md).

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
[docs/compute.md](docs/compute.md). Run it with Social Network deployed and Ready; CI does not run
it, and it takes about 12 minutes.

`scripts/run_planner_dev.py` runs the planner (stories E5-S1, E5-S5 and E5-S2) on the development
problems. For each problem it injects the fault, runs the problem's `wrk2` workload (traffic for traces), takes one
read-only look at the cluster, and runs the loop in `src/remvalgate/planner/`: at most 8 turns,
one JSON object per turn, tool calls through the Orchestrator's parser. The tools are `exec_shell`
limited to `kubectl get` and `describe`, `get_logs`, `get_metrics` and `get_traces`. The last two
save files and return a path, so the model also gets a bounded summary of what they saved
(`src/remvalgate/planner/telemetry.py`). The planner returns a diagnosis (a fault type from a
closed list and a service name) and one command, which the allow-list checks and the script never
runs. It then samples K candidate commands from that diagnosis (`--candidates`, default 8, the
submitted proposal's value): one model call each, with seeds `--seed` to `--seed` plus K minus 1, on
the diagnosis conversation with the accepted answer's command left out. Earlier turns, a refused
final answer among them, and the retrieved past fixes are still seen by every draw. Each
candidate is admitted by the allow-list or rejected with a reason, and none is run. Each candidate
also records the sum of its token log-probabilities when the server returns them; LM Studio's chat
completions endpoint lists no `logprobs` parameter, so expect none there. If every candidate is
rejected (by the allow-list, or because the reply held no command or was cut), the planner draws
K more exactly once, with seeds `--seed` plus K to
`--seed` plus 2K minus 1, and the case is escalated if the second draw fails too; the gate scores the
last draw only. The transcript records every candidate with its draw number (1 or 2), the number
of draws and whether the case escalated. A plan with no
diagnosis draws nothing and is recorded as escalated; a baseline 7 run draws nothing either, and
is recorded as escalated only when its plan escalated. The manifest
records K and the sampling settings. The script then checks that the cluster did not change,
deletes the workload job and resets.
The model is any OpenAI-compatible server (default LM Studio on port 1234); the thinking switch is
sent as `reasoning_effort: "none"`, which LM Studio honours and ignored the other fields tried.
`--model-version` is required and goes into the manifest with the thinking setting. Run it with
Social Network deployed and Ready, Prometheus installed (below) and the model loaded; CI does not
run it. Keep the laptop awake and plugged in: a sleep stalled the first attempt and left a fault
injected.

`scripts/record_pool_queries.py` records one post-injection query per pool problem (story E3-S7,
R3.7): it injects the fault, runs the workload, takes the planner's first observation the same way
and resets. Each problem is a pool run with a touch, so it needs gate G1 and a clean checkout. The
outputs go to `runs/queries/`, which git ignores, and to the shared drive
([docs/pool_queries.md](docs/pool_queries.md)). Run it with Social Network and Hotel Reservation
deployed and Ready; CI does not run it.

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
after the settle window. The baselines are in `src/remvalgate/baselines/`. Nothing is appended to
the touch log and no manifest is written, since a smoke run reports no result. It needs the cluster,
so CI does not run it; CI tests the baselines on synthetic cluster listings.

## Large files

Model weights, rollout logs and datasets are not committed. They live on shared drive storage and
are referenced by checksum. The shared Google Drive folder is named `k8s-RemValGate`. Access
comes from the folder owner. Its read-first file at the folder root lists what is there and the
rules for adding to it: never overwrite an existing file unless that is the intention, and put a
file whose name already exists under a folder named after your GitHub username.

## Runs

Every run calls `start_run` in `src/remvalgate/harness/manifest.py` before it starts, and
`write_manifest` when it finishes.

- **Run manifests** (`<run_id>.json`) are written next to the rollout's logs on shared drive storage.
  The `runs/` folder is ignored by git, except for the touch log and the confirmation seal.
- **The evaluation-set touch log** is `runs/touch_log.jsonl`, committed, one JSON line per use of a
  primary-pool or confirmation-slice problem, and one per creation touch: `log_creation` logs the
  validation of a new problem in any slice, with a run id that starts with `create-`, a prefix
  `start_run` refuses. Lines are only appended, never edited or removed.
  `.gitattributes` merges it as a union, so appends from two branches keep both lines. A union merge
  may put the added lines in any order; each entry carries its own timestamp, so order is not used.
- **Refusals.** A pool or confirmation run is refused when it has no touch entry, when its run id is
  already in the log, when the AIOpsLab commit is not pinned, or when the work tree has changes git
  does not ignore (new files included). A confirmation run is refused in every case for now: the
  slice is opened once, at the end, by a run path added in #59. A pool run is also refused until
  the confirmation slice is sealed (gate G1): `runs/confirmation_seal.json` must cover the registry's confirmation ids, the
  touch log must hold one `seal-` touch per id at the sealed time, and each checksum recomputed
  under the run's AIOpsLab commit must match the record
  ([docs/confirmation_problems.md](docs/confirmation_problems.md)). Run ids starting with `seal-`
  are kept for the seal touches. Development runs are not logged and may run with neither a
  pin nor a clean tree; only their creation is logged. Run ids are lowercase.
- **Slice registry.** `src/remvalgate/harness/slices.json` maps problem ids to slices. It holds the
  16 pool problems: the 9 current ones, one of them the overlay variant of AssignNonExistentNode
  ([docs/problem_inventory.md](docs/problem_inventory.md)), and the three TargetPortMisconfig, two
  ScalePod and two AssignNonExistentNode problems of the pool extension
  ([docs/pool_extension.md](docs/pool_extension.md)). It also holds the development problems
  designated in [docs/development_problems.md](docs/development_problems.md), and the three
  confirmation problems ([docs/confirmation_problems.md](docs/confirmation_problems.md)). The
  singleton problems are not in it yet.
