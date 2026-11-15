# Runs

How the evaluation runs are made, recorded and guarded. The [README](../README.md) gives the
checks that need no cluster; this page is for rerunning on one.

## Rerunning the study on a cluster

A rerun is a new experiment, not the reported one: every pool run appends a touch to
`runs/touch_log.jsonl`, and the runner refuses it unless the tree is clean and the confirmation
slice is sealed. The confirmation slice was opened once and is closed, so a rerun of it is refused
by design. Set up the cluster, both applications and Prometheus as in [AIOpsLab](cluster_setup.md), install
the retrieval group, and serve the planner (`qwen/qwen3.5-4b`, GGUF Q8_0, in LM Studio by default).
The report's runs were made in this order:

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
   `src/remvalgate/harness/slices.json` ([docs/singleton_check.md](singleton_check.md)).

   ```
   uv run python scripts/score_baseline6.py --model-version "<format and quantization>" runs/oracle/<run id>.json ...
   uv run python scripts/run_baselines_pool.py --model-version "<format and quantization>" \
       --baseline 2 --baseline 3c <singleton ids>
   ```

The sections below give each script's details and the cluster setup.

## Runs

Every run calls `start_run` in `src/remvalgate/harness/manifest.py` before it starts, and
`write_manifest` when it finishes.

- **Run manifests** (`<run_id>.json`) are written next to the rollout's logs on shared drive storage.
  The `runs/` folder is ignored by git, except for the touch log and the confirmation seal.
- **The evaluation-set touch log** is `runs/touch_log.jsonl`, committed, one JSON line per use of a
  primary-pool, confirmation-slice or singleton problem, and one per creation touch: `log_creation` logs the
  validation of a new problem in any slice, with a run id that starts with `create-`, a prefix
  `start_run` refuses. Lines are only appended, never edited or removed.
  `.gitattributes` merges it as a union, so appends from two branches keep both lines. A union merge
  may put the added lines in any order; each entry carries its own timestamp, so order is not used.
- **Refusals.** A pool, confirmation or singleton run is refused when it has no touch entry, when its run id is
  already in the log, when the AIOpsLab commit is not pinned, or when the work tree has changes git
  does not ignore (new files included). A confirmation run is refused unless the slice is opened
  by `scripts/run_baselines_pool.py --open-confirmation`, which opens it once and closes it when the
  run ends. The slice was opened on 2026-11-14 at 23:26 UTC and closed on 2026-11-15 at 07:36 UTC
  ([docs/confirmation_check.md](confirmation_check.md)), so every later confirmation run is
  refused. A pool or singleton run is also refused until
  the confirmation slice is sealed (gate G1): `runs/confirmation_seal.json` must cover the registry's confirmation ids, the
  touch log must hold one `seal-` touch per id at the sealed time, and each checksum recomputed
  under the run's AIOpsLab commit must match the record
  ([docs/confirmation_problems.md](confirmation_problems.md)). Run ids starting with `seal-`
  are kept for the seal touches. Run ids starting with `corpus-` are kept for authoring touches
  (`log_authoring`: a problem's definition and known fix were read to write its retrieval corpus
  record), and those starting with `fixture-` for fixture touches (`log_fixture`: a test fixture
  was built from the problem, which counts as an evaluation-set touch); `start_run` refuses both,
  and neither is written for a confirmation problem. Development runs are not logged and may run with neither a
  pin nor a clean tree; only their creation is logged. Run ids are lowercase.
- **Slice registry.** `src/remvalgate/harness/slices.json` maps problem ids to slices. It holds the
  16 pool problems: the 9 current ones, one of them the overlay variant of AssignNonExistentNode
  ([docs/problem_inventory.md](problem_inventory.md)), and the three TargetPortMisconfig, two
  ScalePod and two AssignNonExistentNode problems of the pool extension
  ([docs/pool_extension.md](pool_extension.md)). It also holds the development problems
  designated in [docs/development_problems.md](development_problems.md), and the three
  confirmation problems ([docs/confirmation_problems.md](confirmation_problems.md)), and the
  five singleton problems (rows 8, 9 and 12 to 14 of
  [docs/problem_inventory.md](problem_inventory.md)).
- **Singleton types (exploratory).** The five fault types with one instance each are held out of
  training: no fold holds them, so no gate or planner is trained on them, and the retrieval corpus
  is built from the pool slice only (`log_authoring` refuses a singleton problem). They
  form the exploratory generalization check (BRD R3.6, R9.5), reported descriptively only, since
  five types are too few for a quantitative claim. Their runs are logged as evaluation-set touches
  and subject to the same refusals as pool runs, each in its own namespace: `test-hotel-reservation` for rows 9, 13
  and 14, `astronomy-shop` for row 12 and `test-social-network` for row 8. BRD 7.3 names only the
  pool and the confirmation slice as touches; logging the singleton runs as well follows the E3-S6
  acceptance criteria. Decided in #28 by a group member, pending group confirmation.
