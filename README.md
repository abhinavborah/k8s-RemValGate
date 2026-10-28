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

After changing dependencies, run `uv lock` and regenerate the pip export:

```
uv export --format requirements-txt --no-hashes --no-emit-project --no-header -o requirements.txt
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

### Overlay

New problem definitions go in `src/remvalgate/overlay/`. AIOpsLab builds its problem registry
inside each `Orchestrator` and has no hook for outside entries, so an overlay entry exists only in
an Orchestrator built with `remvalgate.overlay.new_orchestrator`. A bare `Orchestrator()` and
AIOpsLab's own `cli.py` and `service.py` do not see it. An id that already exists upstream is
refused. The shipped overlay holds the three development problems
([docs/development_problems.md](docs/development_problems.md)) and the pool variant of
AssignNonExistentNode ([docs/problem_inventory.md](docs/problem_inventory.md)), defined in
`src/remvalgate/overlay/dev_problems.py`. The known fixes are in
`src/remvalgate/overlay/fixes.py`.

`scripts/check_overlay_registry.py` is the hand check that a test entry appears in a newly
constructed Orchestrator. `scripts/validate_overlay_problems.py` validates each overlay problem: it
logs a creation touch, injects the fault, checks that the mitigation eval fails, runs the known-fix
`kubectl` command, checks that the eval passes and recovers the fault. Run it with Social Network
deployed and Ready. Both need the cluster, so CI does not run them.

## Large files

Model weights, rollout logs and datasets are not committed. They live on shared drive storage and
are referenced by checksum.

## Runs

Every run calls `start_run` in `src/remvalgate/harness/manifest.py` before it starts, and
`write_manifest` when it finishes.

- **Run manifests** (`<run_id>.json`) are written next to the rollout's logs on shared drive storage.
  The `runs/` folder is ignored by git, except for the touch log.
- **The evaluation-set touch log** is `runs/touch_log.jsonl`, committed, one JSON line per use of a
  primary-pool or confirmation-slice problem, and one per creation touch: `log_creation` logs the
  validation of a new problem in any slice, with a run id that starts with `create-`, a prefix
  `start_run` refuses. Lines are only appended, never edited or removed.
  `.gitattributes` merges it as a union, so appends from two branches keep both lines. A union merge
  may put the added lines in any order; each entry carries its own timestamp, so order is not used.
- **Refusals.** A pool or confirmation run is refused when it has no touch entry, when its run id is
  already in the log, when the AIOpsLab commit is not pinned, or when the work tree has changes git
  does not ignore (new files included). A pool run is also refused while the registry holds no
  confirmation-slice id (gate G1). Development runs are not logged and may run with neither a
  pin nor a clean tree; only their creation is logged. Run ids are lowercase.
- **Slice registry.** `src/remvalgate/harness/slices.json` maps problem ids to slices. It holds the
  9 current pool problems, one of them the overlay variant of AssignNonExistentNode
  ([docs/problem_inventory.md](docs/problem_inventory.md)), and the development problems designated
  in
  [docs/development_problems.md](docs/development_problems.md). The pool extension, the
  confirmation slice and the singleton problems are not in it yet.
