# Baseline 5

Story E6-S4 (#43), requirement R6.4. Baseline 5 is the planner, Qwen3.5-4B, LoRA-fine-tuned per
fold on the oracle-labelled candidates, choosing among its own K candidates by log-probability,
with no gate. It is the comparison baseline trained on the same small labels as the other
systems. This story adds the code; the evaluation on the primary pool is produced by the main
runs (#55). No run has been made yet, and `scripts/baseline5_kaggle.py` has not been executed: its
chat template and LoRA target names were checked against the model files at the pinned revision,
but whether a fold fits a T4's memory and session is unchecked.

## Data

- **Training.** Fold f trains on the baseline 5 file of the training data export
  ([training_data.md](training_data.md)), `training-baseline5-fold<f>-<UTC time>.json`: the 12
  pool problems outside fold f, with held-out records masked in the retrieved past fixes. Only
  records with `train` true and label 1 (RECOVERED) are trained on.
- **Held out.** Fold f's four held-out problems are not in the export, so
  `scripts/export_baseline5_heldout.py` writes them, one file per fold, from the same 16 labels
  files: per problem the sampling messages the planner saw (`sampling_messages`, as
  `planner.loop.sample` built them) and all K candidates in draw order with their replies, labels
  and outcomes. Duplicates and candidates with no outcome are kept; one with no outcome does not
  work. A held-out problem retrieved from the corpus of its own fold, which leaves out every fold-f
  problem, so its prompt needs no mask. The file also records whether baseline 3c's top pick, the
  plan's own command, solved the problem (`g3.top_pick_solved`).
- **Touches.** Reading pool labels is an evaluation-set touch. Before any file is read the export
  logs one touch per pool problem through `start_run`, 16 in all, with run id
  `b5-heldout-<fold>-<problem id>-<UTC time>`, so it needs gate G1 and a clean checkout.

## Training on label 1 only

The issue asks for the records with `train` true. Those include candidates that failed (label 0).
A causal language-model loss on a failed command raises its likelihood, and baseline 5 then ranks
by that likelihood, so it would learn to prefer commands that failed. Training on the label-1
records only (rejection-sampling fine-tuning) teaches the planner the commands that worked.
Decided by the group member assigned to the story on 2026-11-12, not yet group-confirmed.
Rejected: training on every `train` row, for the reason above; a contrastive loss on both labels,
which adds a loss and settings the BRD does not name. The export reports 8 to 13 trainable rows per
fold before this cut (#45), so a fold trains on a handful of rows. A fold with none scores with
the untuned model, and the scores file lists the ids it trained on.

## Training and scoring

`scripts/baseline5_kaggle.py` runs one fold on a GPU notebook:

| Setting | Value |
|---|---|
| Model | `Qwen/Qwen3.5-4B` at revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` (the smoke run's, [compute.md](compute.md)) |
| Precision | bfloat16, emulated on a T4, as in the smoke run |
| LoRA | r 16, alpha 32, dropout 0.05, no bias, on the language model's `q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj` and `down_proj`; the vision tower and the Gated DeltaNet projections are not adapted |
| Optimizer | AdamW, learning rate 1e-4, batch 1, 3 epochs, gradient checkpointing |
| Seed | 0, for the shuffle of each epoch and for torch |
| Loss | mean negative log-likelihood of the completion tokens only |
| Libraries | transformers 5.20.2, accelerate 1.14.0, peft 0.21.2 (released 2026-10-01, PyPI) |

The settings were proposed before any run and confirmed by the assigned group member on
2026-11-12. They are not tuned: there is no development split to tune them on.

A candidate's score is the sum of its completion tokens' log-probabilities given the sampling
messages, from the fine-tuned model's own forward pass, not from the model server. The prompt is
the chat template with the generation prompt and thinking off; the completion is the reply as
sampled, followed by the end-of-turn token, tokenized apart from the prompt so no token spans
the two. One function gives both the training loss and the score, so they cover the same tokens.

The scores file, `scores-baseline5-fold<f>-<UTC time>.json`, records the model, revision,
precision, LoRA settings, learning rate, epochs, seed, chat-template settings, GPU, GPU hours,
library versions, the SHA-256 of both input files, the ids trained on, every loss, each
candidate's completion length in tokens and each score. The LoRA adapter is saved beside it. Both
go to the shared drive.

## Results

`scripts/summarize_baseline5.py` takes one held-out file and its scores file per fold. It checks
that each scores file names its held-out file's SHA-256 and fold and scores exactly its candidates,
then gives per held-out problem:

- the ranking of the K candidates by score, highest first, ties in draw order;
- the coverage curve: for m = 1 to K, the expected share of the top m candidates that do not work
  (`harmful_rate_at_coverage` at coverage m/K, ties at the cut shared), and its area (`aurc`);
- top-1: whether the top-scored candidate works (`top1_accuracy`, a tie at the top counts its
  working share). A problem with no candidates is not solved.

The totals are baseline 5's expected number of problems solved and baseline 3c's, from the oracle
labels. Baselines 1, 2, 3a and 3b come from their own pool runs and are set beside these in the main
runs (#55).

## Limits

- The candidates were sampled from the GGUF Q8_0 build through the model server with thinking on
  ([oracle_labels.md](oracle_labels.md)).
  Baseline 5 scores them with the Hugging Face bfloat16 weights and thinking off, since the
  transcript keeps each reply without its thinking. The scores are of the reply alone.
- A sum of log-probabilities favours shorter replies. The scores file keeps each completion's
  length so this can be checked.
- Gate G3 found no recoverable problem in the pool (#53): on no problem where 3c's top pick failed
  did another of the K candidates work. A ranking over the K can therefore solve at most the
  problems on which 3c's top pick already worked.
- The BRD still names the gate as what baseline 5 is compared against (O1, R9.3). Which system
  takes that place is a group decision to record in the BRD.
