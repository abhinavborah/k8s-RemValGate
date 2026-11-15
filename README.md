# k8s-RemValGate

An agent that fixes injected Kubernetes faults on [AIOpsLab](https://github.com/microsoft/AIOpsLab)
mitigation problems, and an evaluation of what each of its capabilities is worth. A small planner
(Qwen3.5-4B) reads the cluster with read-only tools, retrieves past fixes, gives a diagnosis and
proposes one command. A deterministic allow-list admits or refuses it, the harness snapshots the
touched objects, applies it, checks for harm after a settle window and restores on failure.

The project set out to train a validity gate that scores K sampled fixes and applies the best one
only above a threshold. The headroom check that had to pass before any training failed twice, so
the gate was never trained. After that the planner was fine-tuned instead, as an exploratory
ablation whose comparison rule was fixed before its runs (#56), and that claim was falsified. The proposed final system is baseline 3c, the untuned
planner with retrieval configuration (iii).

Group project for Language Models and Agents (CL3410): [requirements](docs/brd.md),
[mid-submission report](docs/mid-submission/monkey-see-monkey-do-mid-report.pdf),
[final report outline](docs/final-report/outline.md).

## Status

Complete. The final report, with the method and every result, is
[docs/final-report/monkeysee-monkeydo-final-report.pdf](docs/final-report/monkeysee-monkeydo-final-report.pdf),
built from the LaTeX source in [docs/final-report/](docs/final-report/).
[Map from the report to the source](#map-from-the-report-to-the-source) below says where each of
its numbers comes from. To build the PDF from its LaTeX source (TeX Live with pgfplots; no shell
escape):

```
cd docs/final-report
latexmk -pdf -interaction=nonstopmode -halt-on-error final-report.tex
```

## Results

Pool of 16 problems, seeds 1000 and 1001; "both seeds" counts a problem only if it recovered at
both (#56). Each linked page gives the runs, commits and per-problem tables.

| Result | Value | Run | Page |
|---|---|---|---|
| Fine-tuned planner (3cft) against 3c, both seeds | 3c 7, 3cft 5 of 16; 0 wins, 2 losses; mid-p 0.25; **falsified** | #165 step 3, 2026-11-14, llama.cpp on one A100 | [primary_analysis](docs/primary_analysis.md) |
| Retrieval, untuned planner, both seeds | 3a (none) 2, 3b (dense) 6, 3c (dense plus reranker) 6 of 16 | #165 step 1, 2026-11-13, LM Studio | [ablations](docs/ablations.md) |
| Tools, both seeds | toolsbundle 8, kubectlonly 7, notools 9 of 16 | #165 step 1 | [tool_ablation](docs/tool_ablation.md) |
| Without the deterministic check | removes 44 of 128 untuned and 29 of 120 fine-tuned candidates | labelling runs, seed 0 | [ablations](docs/ablations.md) |
| Headroom check (gate G3) | 1 recoverable problem, 5 needed: failed (first run: 0) | #165 step 2 labels | [baselines_pool](docs/baselines_pool.md) |
| Baselines 1 (fault-type rule), 2 (retrieval only) | 8 of 16 each, seed 1000 | #150 | [rag_ablation](docs/rag_ablation.md) |
| Baseline 5 (fine-tuned ranking of 3c's candidates) | 7 of 16 | 2026-11-14 | [baseline5](docs/baseline5.md) |
| Baseline 6 (prompted judge of 3c's candidates) | 8 of 16, 3 escalated | 2026-11-15 | [primary_analysis](docs/primary_analysis.md) |
| Baseline 7 (3c told to escalate when unsure), both seeds | 3 of 16, 3 escalations in 32 runs | #165 step 1 | [baselines_pool](docs/baselines_pool.md) |
| Confirmation slice, 3c, opened once | 2 of 3 at seed 1000, 3 of 3 at seed 1001 | 2026-11-14 | [confirmation_check](docs/confirmation_check.md) |
| Singleton fault types, 3c (descriptive) | none recovered; 3 of 5 types not run | 2026-11-15 | [singleton_check](docs/singleton_check.md) |

3c is 6 of 16 in step 1 and 7 of 16 in step 3: different servers and runs, reported apart. Why the
planner fails: [docs/failure_analysis.md](docs/failure_analysis.md). The planner is Qwen3.5-4B
(`qwen/qwen3.5-4b`), GGUF Q8_0, context 32768, thinking on; fine-tuning used `Qwen/Qwen3.5-4B` at
revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`. Each run's server, build and date are in its
manifest; compute is in [docs/compute.md](docs/compute.md).

## Layout

```
src/remvalgate/    the package: harness, planner, retrieval, baselines, overlay problems
tests/             unit tests (pytest), run by CI
scripts/           hand-run scripts: cluster runs, summaries, GPU jobs (table below)
docs/              requirements, one page per story or result, reports
runs/              ignored by git except touch_log.jsonl and confirmation_seal.json
external/          AIOpsLab, a pinned submodule, unmodified
.github/           CI; .devcontainer/ is the x86 backup host (docs/backup_host.md)
pyproject.toml     dependencies, pytest and ruff settings; uv.lock is the lockfile
requirements*.txt  pip exports of the lockfile and the retrieval group, for Colab
```

## What each level of reproduction needs

| Level | Needs | Time |
|---|---|---|
| 1. Install and test | Python 3.11 or 3.12, [uv](https://docs.astral.sh/uv/), git | about 20 minutes, most of it the clone |
| 2. Recompute the results from the saved runs | level 1 and the shared drive (read only) | minutes |
| 3. Rerun on a cluster | Docker (12 GB), kind, kubectl, helm, an OpenAI-compatible model server | about 5 minutes per planner run (224 runs in 19 hours, #165 step 1) |
| 4. Fine-tune and serve | a CUDA GPU (an A100 80 GB was used), llama.cpp | 0.07 to 0.13 GPU hours of training per fold |

## 1. Install and test

```
git clone --recurse-submodules https://github.com/abhinavborah/k8s-RemValGate.git
cd k8s-RemValGate
git submodule update --init --recursive   # in an existing clone, and after pulling a pin change
uv sync --locked                          # install from the lockfile; fails if the lock is stale
uv run pytest                             # unit tests
uv run ruff check .                       # lint
uv run ruff format --check .              # formatting
```

AIOpsLab is installed as an editable path dependency, so the environment cannot be built without
the submodule. Unit tests and lint need only `external/AIOpsLab` itself, which is what CI checks
out; the nested `aiopslab-applications` charts are needed for a cluster run, and they make the
recursive clone slow (16 minutes and about 340 MB on 2026-11-15). **The checkout path
must contain no spaces**: AIOpsLab builds `helm` and `kubectl` commands as unquoted strings, and a
symlink does not help.

The retrieval models (embedder and reranker, which pull in torch) are the optional `retrieval`
group, so CI never installs them: `uv sync --group retrieval`. On Linux the locked torch is the
CUDA build (several GB). The CPU recipe, for a CPU-only Linux machine:

```
pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu
grep -v -E '^(nvidia|cuda|triton)' requirements-retrieval.txt > retrieval-cpu.txt
pip install -r retrieval-cpu.txt
```

On Colab or Kaggle, `pip install -r requirements.txt`, `pip install -e .` and `pytest` run the
tests, and `pip install -r requirements-retrieval.txt` adds the retrieval group at the locked
versions. After a dependency change, run `uv lock` and regenerate both exports:

```
uv export --format requirements-txt --no-hashes --no-emit-project --no-header -o requirements.txt
uv export --only-group retrieval --format requirements-txt --no-hashes --no-emit-project --no-header -o requirements-retrieval.txt
```

## 2. Recompute the results from the saved runs

Model weights, run logs, transcripts and recordings are never committed. They are on the shared
drive, readable by anyone with the link:
<https://drive.google.com/drive/folders/1wL9GGsXx6s88FxRwUMHz1gBWZ_YaD1nd>. The archives are under
`run-logs/`, and each result page lists its archives with their SHA-256 (for example the Files
table of [docs/primary_analysis.md](docs/primary_analysis.md)). Group members adding a file follow
the drive's read-first file and never overwrite one.

| Archive | SHA-256 | Holds |
|---|---|---|
| `run-logs/rerun165_step1_20261113/step1.tar.gz` | `71f43c442a6c774983b4feddb75ef8ee98a0ca7ec4b0bd0b263c14777af5bb07` | 3a, 3b, 3c, 7 and the tool arms, 224 runs |
| `run-logs/rerun165_step3_20261114/step3.tar.gz` | `12200ed8f809e75dd35f4cb101177e7d6cc32482ca7887a3678051d6bc80292b` | 3c and 3cft, 64 runs, the 3cft labels, recordings |

Download both into a folder outside the repository (files inside it would make the tree dirty),
here `../k8s-runs`, then run from the repository root. No cluster, no model, and no touch is
logged:

```
D=../k8s-runs                                   # holds step1.tar.gz and step3.tar.gz
shasum -a 256 $D/step1.tar.gz $D/step3.tar.gz   # or sha256sum; compare with the table
mkdir -p $D/step1 $D/step3
tar -xzf $D/step1.tar.gz -C $D/step1 && tar -xzf $D/step3.tar.gz -C $D/step3
uv run python scripts/summarize_finetune.py --results $D/step3/runs/baselines_pool/*.result.json
# prints "claim": "falsified", both_seeds 3c 7 and 3cft 5, mid-p 0.25
S1=$D/step1/runs/baselines_pool
uv run python scripts/summarize_rag_ablation.py --results $S1/baseline3[abc]-*.result.json
uv run python scripts/summarize_tool_ablation.py --results $S1/baseline3c-*.result.json \
    $S1/toolsbundle-*.result.json $S1/kubectlonly-*.result.json $S1/notools-*.result.json
uv run python scripts/failure_cases.py $D/step3/runs/baselines_pool/*.result.json
```

The per-seed recovered counts these print match [docs/ablations.md](docs/ablations.md). One run
can be replayed from the injected fault to the outcome, and its terminal recording cut out and
played with [asciinema](https://asciinema.org/):

```
B=$D/step3/runs/baselines_pool; R=$D/step3/runs/recordings
uv run python scripts/replay_run.py $B/<run id>.json [--step] \
    [--cast $R/rerun165_step3_batch_20261114.cast --pods $R/rerun165_step3_20261114.pods.log]
asciinema play --idle-time-limit 2 --speed 2 $B/<run id>.cast
```

`summarize_g3.py`, `summarize_oracle_labels.py` and `summarize_baseline5.py` read the labels and
score files listed on their pages.

Five other scripts read evaluation-set files, so each logs one touch per pool problem in
`runs/touch_log.jsonl` and needs gate G1 and a clean tree: `ablate_no_check.py` (the allow-list
ablation, from labelling-run manifests), `score_baseline6.py` (which also needs the model server),
`export_training_data.py`, `export_baseline5_heldout.py` and `run_intrinsic_retrieval.py` (16
touches, starts on call). Run these only to redo the study, not to check it: their outputs are in
the archives and the result pages.

### Map from the report to the source

| Report item | Script | Result file |
|---|---|---|
| Headroom check (Methodology) | `summarize_g3.py` | [docs/baselines_pool.md](docs/baselines_pool.md), [docs/oracle_labels.md](docs/oracle_labels.md) |
| Runs table and compute | the run scripts in [Run order](#run-order) | [docs/baselines_pool.md](docs/baselines_pool.md), [docs/primary_analysis.md](docs/primary_analysis.md), [docs/baseline5.md](docs/baseline5.md), [docs/finetuned_planner.md](docs/finetuned_planner.md), [docs/compute.md](docs/compute.md) |
| Retrieval metrics (Hit@1, Hit@3, MRR, Recall@3) | `run_intrinsic_retrieval.py` | [docs/intrinsic_retrieval.md](docs/intrinsic_retrieval.md) |
| Baselines table | `run_baselines_pool.py`, `summarize_g3.py`, `summarize_baseline5.py`, `score_baseline6.py` | [docs/baselines_pool.md](docs/baselines_pool.md), [docs/baseline5.md](docs/baseline5.md), [docs/primary_analysis.md](docs/primary_analysis.md) |
| Fine-tuning table | `summarize_finetune.py` | [docs/primary_analysis.md](docs/primary_analysis.md), [docs/finetuned_planner.md](docs/finetuned_planner.md) |
| Failure classes table | `failure_cases.py` | [docs/failure_analysis.md](docs/failure_analysis.md) |
| Confirmation slice and singleton types | `run_baselines_pool.py` | [docs/confirmation_check.md](docs/confirmation_check.md), [docs/singleton_check.md](docs/singleton_check.md) |
| Ablations table and recovery figure | `summarize_rag_ablation.py`, `summarize_tool_ablation.py` | [docs/ablations.md](docs/ablations.md), [docs/rag_ablation.md](docs/rag_ablation.md), [docs/tool_ablation.md](docs/tool_ablation.md) |
| Deterministic check ablation | `ablate_no_check.py` | [docs/ablations.md](docs/ablations.md) |
| System figure | the planner and harness code | `src/remvalgate/planner/`, `src/remvalgate/harness/` |

## 3. Rerun on a cluster

A pool run appends to `runs/touch_log.jsonl` (rules below); development problems log no touch.
Keep the machine awake and plugged in: a sleep once stalled a run and left a fault injected.

### Local cluster

A two node kind cluster from AIOpsLab's ARM config on Docker Desktop (12 GB of memory), from the
repository root:

```
cd external/AIOpsLab
kind create cluster --config kind/kind-config-arm.yaml    # context kind-kind; do not pass --name
cp aiopslab/config.yml.example aiopslab/config.yml        # then set k8s_host: kind (gitignored upstream)
cd ../..
```

Every cluster script reads `aiopslab/config.yml` on import, so without it even `--help` fails.
Verified with kind v0.33.0, kubectl v1.36.1, helm v4.3.1, Docker Desktop 4.96.0 (engine 29.9.1,
aarch64), where two images with no arm64 build run under Docker's amd64 emulation (Rosetta). On
x86 use `kind/kind-config-x86.yaml` ([docs/backup_host.md](docs/backup_host.md)). After the nodes
are stopped, `docker start kind-control-plane kind-worker` brings the cluster back.

### Deploy and health check

Deploy with the scripts, which inject no fault (`Orchestrator.init_problem` would inject one):

```
uv run python scripts/deploy_social_network.py
kubectl --context kind-kind wait --for=condition=Ready pods --all -n test-social-network --timeout=900s
uv run python scripts/deploy_hotel_reservation.py    # the four MongoDB pool problems run here
kubectl --context kind-kind wait --for=condition=Ready pods --all -n test-hotel-reservation --timeout=900s
```

The health check passes when `kubectl wait` exits 0; AIOpsLab's own `deploy()` waits 300 s and
may raise on a slow first image pull, which is not a failure. The `media-frontend` init container
clones from GitHub on every start, so the cluster needs GitHub access, and that pod once took about
15 minutes. Social Network is 27 pods, Hotel Reservation 19 pods and 6 volume claims, about 3.2 GiB
for both. A deploy leaves `git status --porcelain -- external/AIOpsLab` empty.

### Telemetry for `get_metrics` and `get_traces`

`get_metrics` needs Prometheus. Install it once with AIOpsLab's deploy, after creating the
StorageClass its volume claim names on kind's local-path provisioner (instead of OpenEBS):

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

The run scripts start `kubectl port-forward -n observe svc/prometheus-server 32000:80` themselves.
For traces they drive 100 requests per second for 30 s, since the Jaeger client keeps 1 percent.

### Model server and secrets

The planner talks to any OpenAI-compatible server serving `qwen/qwen3.5-4b`: LM Studio on port
1234 by default, `--base-url` otherwise. LM Studio honours `reasoning_effort` (the thinking
switch). Its chat endpoint lists no `logprobs` parameter, yet it returned them for all 128
candidates of the #165 step 2 labels; check on a new server. `--model-version` is required and
goes into the manifest. Secrets come from the environment, never from a file in the repository:

- `REMVALGATE_PLANNER_API_KEY`: the key of a remote server, such as the GPU host's.
- `REMVALGATE_MONGO_ADMIN_PASSWORD`, `REMVALGATE_MONGO_ROOT_PASSWORD`: the passwords of AIOpsLab's
  MongoDB setup scripts, for the Hotel Reservation problems ([docs/oracle_labels.md](docs/oracle_labels.md)).

### Run order

| Step | Command | Page |
|---|---|---|
| Harness checks, development problems | `run_dev_end_to_end.py`, `run_restore_check.py`, `run_execution_check.py`, `run_harm_check.py`, `run_pilot.py` | [compute](docs/compute.md) |
| Planner and baselines 1, 2, development problems | `run_planner_dev.py` (`--baseline 3a\|3b\|3c`), `run_baselines_smoke.py` | [development_problems](docs/development_problems.md) |
| Pool queries, intrinsic retrieval | `record_pool_queries.py`, `run_intrinsic_retrieval.py` | [pool_queries](docs/pool_queries.md), [intrinsic_retrieval](docs/intrinsic_retrieval.md) |
| Oracle labels of 3c's K candidates | `run_oracle_labels.py`, then `summarize_oracle_labels.py` | [oracle_labels](docs/oracle_labels.md) |
| Baselines and ablation arms on the pool | `run_baselines_pool.py --baseline <arm>`, repeatable: 1, 2, 3a, 3b, 3c, 7, notools, toolsbundle, kubectlonly | [baselines_pool](docs/baselines_pool.md) |
| Headroom check | `summarize_g3.py` | [baselines_pool](docs/baselines_pool.md) |
| Training data, baseline 5 inputs | `export_training_data.py`, `export_baseline5_heldout.py` | [training_data](docs/training_data.md) |
| Fine-tune, merge, serve (GPU) | `baseline5_kaggle.py`, `merge_baseline5_adapter.py`, `serve_on_gpu_host.sh` | [baseline5](docs/baseline5.md), [finetuned_planner](docs/finetuned_planner.md) |
| 3cft arm, baseline 6, no-check ablation | `run_baselines_pool.py --baseline 3c --baseline 3cft --fold-servers ...`, `score_baseline6.py`, `ablate_no_check.py` | [primary_analysis](docs/primary_analysis.md), [ablations](docs/ablations.md) |
| Summaries | level 2 above, plus `summarize_baseline5.py` | the pages above |

Each script's docstring and `--help` give its inputs, outputs and what it refuses. Outputs go
under `runs/`, which git ignores; copy them to the shared drive with their SHA-256.

A rerun is a new experiment, not the reported one. The confirmation slice is closed, so a rerun
of it is refused by design. The report's pool runs were made in this order, with the planner
(`qwen/qwen3.5-4b`, GGUF Q8_0) served in LM Studio by default and the retrieval group installed:

1. Baselines 1 and 2, which use no model, ran first at seed 1000. Step 1 then ran the seven
   planner arms at seeds 1000 and 1001 (the defaults), interleaved by problem:

   ```
   uv run python scripts/run_baselines_pool.py --baseline 1 --baseline 2 --seed 1000
   uv run python scripts/run_baselines_pool.py --model-version "<format and quantization>" \
       --baseline 3a --baseline 3b --baseline 3c --baseline 7 \
       --baseline notools --baseline toolsbundle --baseline kubectlonly
   ```

2. Step 2, the oracle labels of 3c's candidates (seed 0 and K = 8 by default), then the headroom
   check with `summarize_g3.py`:

   ```
   uv run python scripts/run_oracle_labels.py --model-version "<format and quantization>"
   ```

3. Step 3, the fine-tuned planner. Export the per-fold training data and held-out files from the
   labels (`export_training_data.py`, `export_baseline5_heldout.py`), train each fold on a GPU with
   `baseline5_kaggle.py --train ... --heldout ... --out ...`, merge each adapter with
   `merge_baseline5_adapter.py --base ... --adapter ... --out ...`, convert it with llama.cpp's
   `convert_hf_to_gguf.py --outtype q8_0`, and serve each model on the GPU host with
   `serve_on_gpu_host.sh <name> <gguf> <port> <host label> [<fold> <adapter folder>]`, which also
   writes its record file. Then run both arms in one interleaved batch, the 3c control on the
   untuned model served the same way (`--base-url` and `--served-build` name its server and
   record; `--fold-servers` maps each fold to its fine-tuned server and record):

   ```
   uv run python scripts/run_baselines_pool.py --model-version "<format and quantization>" \
       --baseline 3c --baseline 3cft --fold-servers <file> \
       --served-build <base record> --base-url <base server>
   ```

   The fine-tuned labels come from
   `run_oracle_labels.py --fold F --base-url <fold server> --served-build <record>`, with
   `--no-retrieval` for the run without retrieval.

4. Baseline 6 and the singleton types. Baseline 6 has the served model judge the step 2
   candidates, reading the labelling manifests (it logs touches and needs the model server). The
   singleton types run baselines 2 and 3c only; their ids are in
   `src/remvalgate/harness/slices.json` ([docs/singleton_check.md](docs/singleton_check.md)).

   ```
   uv run python scripts/score_baseline6.py --model-version "<format and quantization>" runs/oracle/<run id>.json ...
   uv run python scripts/run_baselines_pool.py --model-version "<format and quantization>" \
       --baseline 2 --baseline 3c <singleton ids>
   ```

## 4. Fine-tune and serve

`scripts/baseline5_kaggle.py` trains and scores one LoRA adapter per fold (r 16, alpha 32, 3
epochs; the runs used an A100). For the 3cft arm, `scripts/merge_baseline5_adapter.py` merges it
into the base model, llama.cpp converts it to GGUF Q8_0 and `scripts/serve_on_gpu_host.sh` serves
it. The four adapters are `adapters.tar` on the drive ([docs/baseline5.md](docs/baseline5.md),
[docs/finetuned_planner.md](docs/finetuned_planner.md)).

## Evaluation-set rules

- **Slices.** `src/remvalgate/harness/slices.json` maps each problem to a slice: 16 pool problems
  in 4 folds, 3 development problems, a 3-problem confirmation slice and 5 singleton types, which
  are in no fold and no corpus and reported descriptively only
  ([docs/problem_inventory.md](docs/problem_inventory.md)).
- **Touch log.** `runs/touch_log.jsonl` (committed, append only, merged as a union) gets one line
  per use of a pool, confirmation or singleton problem, and per creation, seal, authoring or
  fixture touch, through `start_run` in `src/remvalgate/harness/manifest.py`.
- **Refusals.** A pool or singleton run is refused without a touch entry, with a run id already in
  the log, with an unpinned AIOpsLab, or with any uncommitted or untracked file. It is also refused
  until the confirmation slice is sealed (gate G1, `runs/confirmation_seal.json`).
- **Confirmation slice.** Opened once on 2026-11-14 and closed on 2026-11-15: no further
  confirmation run is admitted ([docs/confirmation_check.md](docs/confirmation_check.md)).
- **Retrieval configuration (iii)** was fixed before any evaluation run (gate G5).
  `src/remvalgate/retrieval/fixed.json` records it, and `configuration_iii` refuses to build when
  the live settings differ, so a change needs a new entry with its reason.

## AIOpsLab pin and overlay

Decided in #65: an unmodified submodule pinned at a commit, with new problems in an overlay here;
a fork was rejected because the code would then live in a second repository.

AIOpsLab commit: `576c3f080c7ae2459a972afd4a2f8d5f01828430` (upstream `main` on 2026-10-20), at
`external/AIOpsLab`. Its nested `aiopslab-applications` is pinned by AIOpsLab at `8038be6`. The
run manifest records this hash: callers pass `remvalgate.aiopslab_pin.pinned_commit(repo)` as
`aiopslab_commit` to `start_run`, which reads the committed link and not the checkout.

New problems are in `src/remvalgate/overlay/` (known fixes in `fixes.py`) and exist only in an
Orchestrator built with `remvalgate.overlay.new_orchestrator`, not in a bare `Orchestrator()`.
`scripts/validate_overlay_problems.py` checks on a cluster that each fault breaks the eval and its
known fix repairs it.

Image digests of the verified runs (BRD 7.3); several tags are mutable, so a later pull can differ.
Regenerate with `kubectl --context kind-kind get pods -n <namespace> -o jsonpath='{range .items[*].status.containerStatuses[*]}{.image} {.imageID}{"\n"}{end}' | sort -u` (and `initContainerStatuses`).

- node image `jacksonarthurclark/aiopslab-kind-arm`: `sha256:9723c6d45f5e1b9af0975987801543f36f529e6caff2575d9ba55d048f38977c`
- node image `jacksonarthurclark/aiopslab-kind-x86`: `sha256:d631857278d3f8ce5c36364c75ec25695ceb22e311ec6621a4ebf5506b86774d`
- `deathstarbench/social-network-microservices:latest`: `sha256:a845266e98ce15d204ee7b98d41a2a95820af525013ce70f14104f5b9b5e92b6`
- `jacksonarthurclark/media-frontend:latest` (Social Network on ARM): `sha256:825c601f648aadd5eec08bf0cf2b4154712deec9dcd331796852c879dc658039`
- `yg397/media-frontend:xenial` (Social Network on x86): `sha256:272049995f2929435b91da34f20fc16216a7f974d1b8f9afa0ccbb529f1a9840`
- `jaegertracing/all-in-one:1.57`: `sha256:8f165334f418ca53691ce358c19b4244226ed35c5d18408c5acf305af2065fb9`
- `library/memcached:1.6.7`: `sha256:007b9ca55759c6ef3dcc9a08be6b45b31147bdd1db28358280fd62360387b9dd`
- `library/memcached` (no tag, Hotel Reservation): `sha256:aea0d81d101a9b64fe30cf2cf2f96144e284f8e9552bb084a03f4e12e9043bab`
- `library/mongo:4.4.6`: `sha256:6efa052039903e731e4a5550c68a13c4869ddc93742c716332883fd9c77eb79b`
- `library/redis:6.2.4`: `sha256:6bc98f513258e0c17bd150a7a26f38a8ce3e7d584f0c451cf31df70d461a200a`
- `yg397/openresty-thrift:xenial`: `sha256:6c295fda3c2f6b941d85ebf16421bafac0dcf72450c96f53a2d031b29d8c81ef`
- `yinfangchen/hotelreservation:latest`: `sha256:c46c018d412ab466c4cb3ae93b1aaf1a373968e2fafc2767f15539f50cd1683a`
- `hashicorp/consul:latest`: `sha256:259fd02f2544fe1aa03fa49efd3b105c8dd67106d710a72f5e898110266356f7`
- `alpine/git:latest` (init container): ARM `sha256:e8ceedc73bd80992ae8d58a7d24c58b8322146c354f1d2e81a97db4452068ec0`, x86 `sha256:6a1fdb962814ca11508ca8d1bab94375b5b7ac4e4a9a40981c29fc32c02f095d`
