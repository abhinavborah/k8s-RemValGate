# k8s-RemValGate

An evaluation of a small language-model agent that repairs Kubernetes incidents on 16 AIOpsLab
mitigation problems. A Qwen3.5-4B planner retrieves past fixes of the same fault type, reads the
cluster through read-only tools and gives one command, which a deterministic allow-list checks
before it runs. Retrieval, tool calling and LoRA fine-tuning of the planner are each ablated
against baselines, at two seeds with the arms interleaved by problem.

The validity gate of the original design was not trained: a pre-specified headroom check found 1
of 16 problems where a selector over the planner's candidates could help, against the 5 a paired
test needs. The study then turned to the planner itself. The pre-specified fine-tuning claim was
tested and falsified ([docs/primary_analysis.md](docs/primary_analysis.md)).

This is a group project for the course Language Models and Agents (CL3410).

## Status

Complete. The final report, with the method and every result, is
[docs/final-report/monkeysee-monkeydo-final-report.pdf](docs/final-report/monkeysee-monkeydo-final-report.pdf),
built from the LaTeX source in [docs/final-report/](docs/final-report/). [Reproducing the final report](#reproducing-the-final-report)
below says how to check each of its numbers. To build the PDF from its LaTeX source (TeX Live
with pgfplots; no shell escape):

```
cd docs/final-report
latexmk -pdf -interaction=nonstopmode -halt-on-error final-report.tex
``` The requirements, epics and acceptance criteria are in
[docs/brd.md](docs/brd.md).

## Layout

```
src/remvalgate/   the package
tests/            unit tests (pytest)
scripts/          hand-run scripts outside the package: cluster runs, summaries, replay, GPU jobs
docs/             requirements, one result file per experiment, the final report
external/         AIOpsLab, a pinned submodule, unmodified
runs/             ignored by git, except the touch log and the confirmation seal below
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

Retrieval configuration (iii), dense with the cross-encoder reranker, is fixed before any
evaluation run (gate G5, story E4-S6). `src/remvalgate/retrieval/fixed.json` records its settings
(both models and revisions, the rerank depth, the entry text and the number of entries the
planner retrieves) as entries, each with a UTC timestamp and a reason. If the merge comes later
than the first entry's timestamp, set it to the merge time, so the touch-log order test checks
from the real freeze. `configuration_iii` in `src/remvalgate/retrieval/fixed.py` refuses to build
when the live settings differ from the last entry, and a unit test fails the same way, so a
change needs a new entry with the group's reason in the same pull request. Run code builds (iii)
only through `configuration_iii`, never `DenseRerank` directly; a test checks that no file under
`scripts/` or `src/` names `DenseRerank` except `configs.py`, `fixed.py` and the model smoke check,
so the other configurations stay free for baselines and metrics. Ablation results are reported
and never used to change it. The planner and baseline 2 hand runs retrieve with (iii) over the
synthetic development corpus, so they need the retrieval group, and the first run downloads both
models. That corpus has no more entries than the rerank depth, so a development run checks the
wiring, not the two-stage ranking.

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

## Reproducing the final report

There are three levels: a quick check with no cluster, the report's numbers recomputed from the
recorded runs, and a rerun on a cluster. The first two need no cluster, model or GPU.

### Quick check, no cluster

```
git clone --recurse-submodules https://github.com/abhinavborah/k8s-RemValGate
cd k8s-RemValGate
uv sync --locked
uv run pytest
uv run ruff check .
```

These are the steps CI runs. The unit tests cover the harness rules the results rest on: the
allow-list, the touch log and its refusals, the folds, the outcome classes and the summary rules.

### Run archives

Run files are not in git. They are in the `run-logs` folder of the group's shared Google Drive
folder `k8s-RemValGate`, which is public and view-only:
<https://drive.google.com/drive/folders/1c08dv-LrF2SfwfqYjMzzY9lRACUPZRyz>. It holds one subfolder
or archive per run batch, with SHA-256 files. The sizes and SHA-256 values are also listed in the
result file that cites each archive (for example the Files paragraphs of
[docs/baselines_pool.md](docs/baselines_pool.md) and
[docs/primary_analysis.md](docs/primary_analysis.md)). Check each archive against its listed value
and unpack it into its own empty folder:

```
shasum -a 256 <archive>.tar.gz       # sha256sum on Linux
mkdir <folder> && tar -xzf <archive>.tar.gz -C <folder>
```

A run's files sit together: `<run id>.json` (the manifest, with commit, model build, seed and
touch entry), `<run id>.transcript.json` and `<run id>.result.json` (a baseline run) or
`<run id>.labels.json` (a labelling run).

### Replaying one run

`scripts/replay_run.py` prints one recorded run from its saved files only: the injected fault, the
observation, the diagnosis, the K candidates with any refusal reason, the chosen action or the
escalation, and the outcome. `--step` waits for Enter between the parts. With `--cast` and
`--pods` it cuts the batch's terminal capture and pod watch down to this run and writes
`<run id>.cast` and `<run id>.pods.log` beside the manifest.

```
uv run python scripts/replay_run.py <folder>/<run id>.json --step
uv run python scripts/replay_run.py <folder>/<run id>.json --cast <batch>.cast --pods <batch>.pods.log
asciinema play --idle-time-limit 2 --speed 2 <folder>/<run id>.cast
```

### Recomputing the numbers

These scripts read finished run files only. They log no touch and need no cluster or model:

```
uv run python scripts/summarize_g3.py --labels <labels files> [--results <3c result files>]
uv run python scripts/summarize_rag_ablation.py --results <baseline3a, 3b and 3c result files>
uv run python scripts/summarize_tool_ablation.py --results <3c, toolsbundle, kubectlonly and notools result files>
uv run python scripts/failure_cases.py <result files>
uv run python scripts/summarize_baseline5.py --pair <heldout file> <scores file> [--pair ...] [--results <result files>]
uv run python scripts/summarize_finetune.py --results <3c and 3cft result files> \
    [--labels <untuned labels files>] [--ft-labels <fine-tuned labels files>] [--baseline5 <summary>.json]
```

`--pair` is given once per fold. The summary scripts refuse files that break their rules, for
example a problem outside the pool, a file read twice or runs served by different model builds.

Four other scripts read evaluation-set files, so each logs one touch per pool problem in
`runs/touch_log.jsonl` and needs gate G1 and a clean tree: `scripts/ablate_no_check.py` (the
allow-list ablation, from labelling-run manifests), `scripts/score_baseline6.py` (baseline 6, which
also needs the model server), `scripts/export_training_data.py` and
`scripts/export_baseline5_heldout.py`. `scripts/run_intrinsic_retrieval.py` takes no arguments
and starts on call; it also logs 16 touches. Run these only to redo the study, not to check it:
their outputs are in the archives and the result files.

### Map from the report to the source

| Report item | Script | Result file |
|---|---|---|
| Headroom check (Methodology) | `summarize_g3.py` | [docs/baselines_pool.md](docs/baselines_pool.md), [docs/oracle_labels.md](docs/oracle_labels.md) |
| Runs table and compute | the run scripts below | [docs/baselines_pool.md](docs/baselines_pool.md), [docs/primary_analysis.md](docs/primary_analysis.md), [docs/baseline5.md](docs/baseline5.md), [docs/finetuned_planner.md](docs/finetuned_planner.md), [docs/compute.md](docs/compute.md) |
| Retrieval metrics (Hit@1, Hit@3, MRR, Recall@3) | `run_intrinsic_retrieval.py` | [docs/intrinsic_retrieval.md](docs/intrinsic_retrieval.md) |
| Baselines table | `run_baselines_pool.py`, `summarize_g3.py`, `summarize_baseline5.py`, `score_baseline6.py` | [docs/baselines_pool.md](docs/baselines_pool.md), [docs/baseline5.md](docs/baseline5.md), [docs/primary_analysis.md](docs/primary_analysis.md) |
| Fine-tuning table | `summarize_finetune.py` | [docs/primary_analysis.md](docs/primary_analysis.md), [docs/finetuned_planner.md](docs/finetuned_planner.md) |
| Failure classes table | `failure_cases.py` | [docs/failure_analysis.md](docs/failure_analysis.md) |
| Confirmation slice and singleton types | `run_baselines_pool.py` | [docs/confirmation_check.md](docs/confirmation_check.md), [docs/singleton_check.md](docs/singleton_check.md) |
| Ablations table and recovery figure | `summarize_rag_ablation.py`, `summarize_tool_ablation.py` | [docs/ablations.md](docs/ablations.md), [docs/rag_ablation.md](docs/rag_ablation.md), [docs/tool_ablation.md](docs/tool_ablation.md) |
| Deterministic check ablation | `ablate_no_check.py` | [docs/ablations.md](docs/ablations.md) |
| System figure | the planner and harness code | `src/remvalgate/planner/`, `src/remvalgate/harness/` |

### Rerunning on a cluster

A rerun is a new experiment, not the reported one, and every pool run is logged and guarded. The
order of the report's runs, with their commands, is in [docs/runs.md](docs/runs.md), and the cluster
setup in [docs/cluster_setup.md](docs/cluster_setup.md).

## Large files

Model weights, rollout logs and datasets are not committed. They live on shared drive storage and
are referenced by checksum. The shared Google Drive folder is named `k8s-RemValGate`; its
`run-logs` folder is public and view-only (link under [Run archives](#run-archives)), and uploads
go through the folder owner. Its read-first file at the folder root lists what is there and the
rules for adding to it: never overwrite an existing file unless that is the intention, and put a
file whose name already exists under a folder named after your GitHub username.

## Runs

How runs are made, recorded and guarded: [docs/runs.md](docs/runs.md).
