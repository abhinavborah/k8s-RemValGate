# Training data

Story E7-S1 (#45), requirement R7.1. The training data of the gate and of baseline 5 is built per
fold from the oracle labels of baseline 3c's candidates ([oracle_labels.md](oracle_labels.md)),
with no new rollouts. The builder is `src/remvalgate/harness/training_data.py`, the export
`scripts/export_training_data.py`. No export has been made yet.

Reusing the oracle labels departs from R7.1 and proposal version 2, which ask for K new rollouts
per training problem per fold (384 at K = 8, about 33 hours serial). The group confirmed it
(BRD version 1.15), and the change is in the BRD change log (1.11). It saves about 256 rollouts. The
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
  one per three other characters. The proxy is meant to overestimate, but it is not a bound. Rows
  over the budget are listed in the report, not cut. The count leaves out the chat template.
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

## Limits

- The held-out fold's records, which the gate scores, are not in these files.
- `k8s_target_port-misconfig-mitigation-3` and `pool-k8s_target_port-misconfig-mitigation-4` gave
  no candidates, since the planner reached the 16 turn cap with no diagnosis, so they add no record
  to any fold's training set.
- The training candidates were sampled once, from the corpus of each problem's own fold, and the
  same candidates and labels serve the three folds in which a problem trains, so the three
  training sets share their candidate text. Masking removes the held-out records from what the
  model is shown, not their influence on what the planner wrote at sampling time. This is
  disclosed beside the results.
