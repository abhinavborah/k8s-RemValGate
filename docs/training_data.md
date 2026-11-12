# Training data

Story E7-S1 (#45), requirement R7.1. The training data of the gate and of baseline 5 is built per
fold from the oracle labels of baseline 3c's candidates ([oracle_labels.md](oracle_labels.md)),
with no new rollouts. The builder is `src/remvalgate/harness/training_data.py`, the export
`scripts/export_training_data.py`. The export of 2026-11-08, now superseded, is under
[Results](#results).

Reusing the oracle labels departs from R7.1 and proposal version 2, which ask for K new rollouts
per training problem per fold (384 at K = 8, about 33 hours serial). The group confirmed it
(BRD version 1.16), and the change is in the BRD change log (1.11). It saves about 256 rollouts. The
limit it brings is under Limits; the masking, the copied-fix exclusion and the `held_out_text`
count below bound it. A rerun of only the (problem, fold) pairs that retrieved a fold-f record was
rejected: the folds spread each fault type, so most pairs retrieve a same-type fold-f record and
the rerun would save at most about 30 percent of the per-fold cost.

## Rules

- **Out of fold.** The folds are `assign_folds` of the 16 pool problems. Fold f's training set
  holds the candidates of the 12 pool problems outside fold f, each labelled once (R does not
  apply to training data). The build refuses a fold-f problem, a confirmation, development or
  singleton id, an unregistered id, a pool problem with no labels file and a problem with two.
  The confirmation slice is never read.
- **Label.** 1 when the candidate's execution was RECOVERED, 0 when it was APPLIED_INEFFECTIVE or
  APPLIED_HARMFUL. The outcome is kept beside it.
- **No outcome.** A candidate the allow-list rejected, one with no command in its reply, and one
  that could not be sent unchanged are stored with `train` false and never trained on.
- **Merged duplicates.** Near-identical candidates (`dedup_key`) were executed once and share a
  label, so they are one record listing their seeds.
- **The plan's own command** is not one of the K and is left out.
- **Masking.** A training problem retrieves from the corpus of the fold that holds it out, so it
  can retrieve the record of a fold-f problem, fix included. In fold f's files every such entry is
  replaced by `[held-out record removed]`, in the gate input and in baseline 5's prompt. Runbook
  entries are never masked. Text the planner wrote after reading an entry, in a later turn of
  baseline 5's prompt or in the diagnosis the gate input keeps, is not masked. The report counts
  it instead: `held_out_text` gives, per file, the training records whose text, namespace flags
  left out, still holds a fold-f problem's recorded fix or summary, as written or JSON-escaped.
  It is an upper bound, since a summary is one template per fault type and service and can match
  without being copied.
- **One setup.** The labelling runs pooled into one training set must share the model name and
  version (which holds the thinking setting), K, the seed and the sampling settings (temperature,
  top_p, top_k, max_tokens, thinking). The build refuses runs that differ, so a run resumed with
  another setup is never pooled.
- **Copied fixes.** A record whose command makes the same calls as the recorded fix of a fold-f
  problem, namespaces left out, is stored with `train` false.
- **Length.** The gate input is checked against 2,048 tokens: with the tokenizer saved at
  `--tokenizer` (local files only), or else with a tokenizer-free proxy, one token per digit and
  one per three other characters. The proxy is an estimate, not a bound; how far it is from the
  Qwen3.5-4B count is under [Length check](#length-check). Rows over the budget are listed in the
  report, not cut. The count leaves out the chat template.
- **Touches.** Reading pool labels is an evaluation-set touch. Before any file is read the export
  appends one touch per fold and training problem through `start_run`, 48 in all, with run id
  `train-export-<fold>-<problem id>-<UTC time>`. The problem id is part of the run id because the
  touch log refuses a run id it already holds. `start_run` needs a clean tree, the pinned AIOpsLab
  commit and the sealed confirmation slice.

## Running it

```
uv run python scripts/export_training_data.py [--tokenizer DIR] runs/oracle/<run id>.labels.json ...
```

Give the 16 labels files of the pool run. Each needs its `<run id>.json` and
`<run id>.transcript.json` beside it. No cluster and no model are needed. The output goes to
`runs/training_data/`, which git ignores, and to the shared drive. A file is never overwritten.

For exact counts, save the Qwen3.5-4B tokenizer once (three files, about 13 MB, no weights) at the
revision of the smoke run ([compute.md](compute.md)) and give its folder. transformers 5.22.1, the
locked version, loads it without PyTorch. Name the folder after the model and the revision, since
the files record the folder's name as `length_counter`:

```
uvx --from huggingface-hub==2.4.0 hf download Qwen/Qwen3.5-4B config.json tokenizer.json \
    tokenizer_config.json --revision 851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a \
    --local-dir <DIR>/qwen3.5-4b-851bf6e
uv run --with transformers==5.22.1 python scripts/export_training_data.py \
    --tokenizer <DIR>/qwen3.5-4b-851bf6e runs/oracle/<run id>.labels.json ...
```

## Format

One JSON file per fold and kind, `training-<kind>-fold<f>-<UTC time>.json`, where kind is `gate`
or `baseline5`. Every value is plain JSON, so a notebook reads it without this package.

| Field | Meaning |
|---|---|
| `format` | `remvalgate-training-data/1` |
| `kind`, `fold` | `gate` or `baseline5`, and f |
| `created` | UTC time of the export |
| `commit`, `dirty`, `aiopslab_commit` | the code the export ran at, as `start_run` read it |
| `fold_assignment`, `fold_assignment_sha256` | problem id to fold, and the SHA-256 of its JSON with sorted keys and no spaces |
| `labels_files` | per training problem: `run_id`, `file`, `sha256` (of the labels file), `transcript_sha256` (of the transcript file) and `manifest` (the labelling run's whole manifest: model name, version, date and seed) |
| `touches` | the run ids of this fold's touches |
| `length_counter` | `length_proxy`, or the tokenizer's folder name |
| `mask` | the marker that replaces a held-out record |
| `report` | the counts below |
| `records` | one per distinct command of a training problem |

Every record holds `id` (`<problem id>/<first seed>`), `problem_id`, `run_id`, `seeds`,
`candidate` (the command, null when the reply held none), `outcome` (null when there is none),
`label` (1, 0 or null), `train`, `excluded` (why `train` is false, else null), `masked_entries`
and `length`. A gate record adds `input`: one JSON object, in this order, `task`,
`first_observation` (whole), `diagnosis` (`fault_type`, `service`, `reason`), `retrieved` (the past
fixes as the planner saw them, masked) and `candidate`. It holds no problem id, seed or label. A
baseline 5 record adds `messages`, the planner's sampling messages (the diagnosis conversation,
the diagnosis as its answer and the request for one command, rebuilt from the transcript by
`sampling_messages` as `sample` in `src/remvalgate/planner/loop.py` builds them), masked, and `completion`, the candidate's reply as sampled.

The report gives `problems`, `records`, `candidates`, `train`, `train_by_outcome`,
`train_label_1`, `no_outcome`, `no_outcome_candidates` (candidates, not records, with no outcome,
by cause: `rejected`, `not_sendable`, `no_command`), `fix_match_excluded`, `masked_entries`,
`records_with_masked_entries`, `train_with_masked_entries`, `held_out_text` (`gate_train`,
`baseline5_train`), `held_out`, `token_budget`, and for
each kind the maximum length, the 95th percentile and the ids over the budget.

## Results

**Superseded.** On 2026-11-12 three defects were found in code that every planner run shares
(#162, #163, #164). Step 2 of #165 runs the oracle labelling again with the fixed planner, and its
step 3 makes this export again from the new labels and runs the [Length check](#length-check)
again. The export below stays as the record of the 2026-11-08 run.

One export on 2026-11-08, at commit `4d83002` with `dirty: false`, from the 16 labels files of the
second labelling run of the pool (commit `3d34cb4`, [oracle_labels.md](oracle_labels.md),
Results), with the length proxy. Its 48 touches carry the time 2026-11-08T15:30:17Z in
`runs/touch_log.jsonl`; the export appends them before it reads any labels file.

| Fold | Records | Train | Train with label 1 |
|---|---|---|---|
| 0 | 42 | 8 | 7 |
| 1 | 43 | 11 | 5 |
| 2 | 55 | 13 | 6 |
| 3 | 55 | 11 | 6 |

The gate file and the baseline 5 file of a fold share one report, so their counts are the same,
and each holds the 12 labels files and the 12 touches of its fold. The 8 files are on the shared
drive under `k8s-RemValGate/run-logs/`, not in git: `training-data-20261108.tar.gz` (774809
bytes, SHA-256 `bcd3cb7d5a0a58b2758b0a1a37077bace0dea680ff4c4454abff1e9575331146`). No stored
file holds a MongoDB password. The report's lengths are proxy counts; [Length check](#length-check)
gives the tokenizer's beside them.

## Length check

`scripts/check_length_proxy.py --tokenizer DIR` counts synthetic text of the gate input's shape
with the tokenizer, as the export counts, and compares the count with the proxy
(`src/remvalgate/harness/length_check.py`). The text holds no pool data, so the check makes no
touch: the first observation is a `kubectl get deployments,pods` listing of one Ready pod per
deployment of the application's chart, laid out and named as kubectl and Kubernetes v1.32.1 lay
out and name them, and the retrieved past fixes are development corpus entries. It exits 1 when
the tokenizer counts more than the proxy on a gate input.

Run on 2026-11-09 with the tokenizer folder of [Running it](#running-it) (Qwen3.5-4B at revision
`851bf6e`, transformers 5.22.1), 20 samples per application and text:

| Application | Text | Proxy, largest | Count, largest | Over 2,048 by proxy, by count | Count / proxy |
|---|---|---|---|---|---|
| Social Network | gate input | 2251 | 1948 | 20, 0 | 0.859 to 0.874 |
| Social Network | listing alone | 1806 | 1588 | 0, 0 | 0.872 to 0.886 |
| Social Network | pod names alone | 493 | 541 | 0, 0 | 1.078 to 1.110 |
| Hotel Reservation | gate input | 1724 | 1431 | 0, 0 | 0.826 to 0.834 |
| Hotel Reservation | listing alone | 1291 | 1091 | 0, 0 | 0.841 to 0.855 |
| Hotel Reservation | pod names alone | 312 | 364 | 0, 0 | 1.155 to 1.194 |

- The tokenizer's pre-tokenizer splits every digit apart, so the proxy's digit rule holds for it.
- On the gate inputs the proxy is 14 to 21 percent above the count, since a run of spaces that
  pads a column is one token. It is below the count on generated pod names, whose suffixes mix
  digits with single letters, so it is not a bound on all text.
- Every synthetic Social Network gate input is over the budget by the proxy and under it by the
  count; the largest count is 1,948, 100 under the budget. So the proxy's over-budget list of the
  2026-11-08 export can hold rows that fit, and a row near the budget needs the tokenizer's count.

Step 3 of #165 runs this check again ([Results](#results)).

## Limits

- The held-out fold's records, which the gate scores, are not in these files.
- `k8s_target_port-misconfig-mitigation-3` and `pool-k8s_target_port-misconfig-mitigation-4` gave
  no candidates, since the planner reached the 16 turn cap with no diagnosis, so they add no record
  to any fold's training set.
- The length check counts synthetic text, not the gate inputs. A real first observation after an
  injection can hold other rows (a Pending pod, a deployment at 0/0, the pod of a second
  ReplicaSet), and real diagnoses and retrieved entries differ in length. Baseline 5's prompts,
  which hold the whole diagnosis conversation, are not checked.
- The training candidates were sampled once, from the corpus of each problem's own fold, and the
  same candidates and labels serve the three folds in which a problem trains, so the three
  training sets share their candidate text. Masking removes the held-out records from what the
  model is shown, not their influence on what the planner wrote at sampling time. This is
  disclosed beside the results.
