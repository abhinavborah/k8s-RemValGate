# Primary analysis: the fine-tuned planner against the untuned planner

Story E9-S5 (#56). The claim compares the fine-tuned planner's top pick with the untuned planner's
(baseline 3c), per pool problem, as recovered or not.

## What is compared

- **Systems.** 3cft, the planner with its fold's LoRA adapter merged (#159,
  [finetuned_planner.md](finetuned_planner.md)), and 3c, the same planner without it.
- **Same everything else.** Both are served by the same llama-server build on the same GPU host,
  run interleaved by problem at seeds 1000 and 1001 (#161, #165 step 3), with retrieval
  configuration (iii), the same tools, prompt, sampling and turn cap. A pool problem is served by
  the adapter of its own fold, which held it out.
- **Unit.** A problem counts as recovered only if it recovers at both seeds (the rule of #56,
  `harness.g3.combine_seeds`). Each seed is also reported alone.

## Threshold, committed before the runs

With gate G3 (#53, R6.6, [baselines_pool.md](baselines_pool.md)): the claim is supported only if
3cft recovers more problems than it loses against 3c and the two-sided McNemar mid-p (#21) is
below 0.05, paired by problem at n = 16. That takes 5 wins with 0 losses, or 7 with 1. Anything
else falsifies it. The interval is the Bonett-Price interval for the paired difference (#21).

What G3 already bounds: on the untuned planner's own candidates, only 1 problem is recoverable
(#165 step 2), so a ranking over them can gain at most 1. 3cft samples its own candidates, so it is
not bound by that count.

## Reported beside the claim

- Baseline 5, the fine-tuned ranking over 3c's candidates: 7.0 of 16, the same as 3c's top pick
  in the labelling run ([baseline5.md](baseline5.md)).
- The candidates of both planners labelled by execution (#159 against #165 step 2): per problem
  the top pick's outcome, the working candidates of K, mixed outcomes and recoverable problems.

## How it is computed

```
uv run python scripts/summarize_finetune.py \
    --results runs/baselines_pool/<3c and 3cft run id>.result.json ... \
    --labels runs/oracle/<run id>.labels.json ... \
    --ft-labels runs/oracle_3cft/<run id>.labels.json ... \
    --baseline5 <summarize_baseline5 output>.json
```

`src/remvalgate/harness/finetune.py` refuses a run that is missing or repeated, and a 3cft run
whose served model is not its problem's fold (or a control served by a fold's model).

## Results

Not run yet: #165 step 3 started on 2026-11-14 at 15:12 IST. The results are added here when it ends.

## Limits

- n = 16 problems, so only a large gain can pass the test. The minimum gain above says how large.
- The served build (llama-server on a GPU host) differs from LM Studio on the run host, so these
  3c runs are a separate configuration from #165 step 1 and are not pooled with it.
- The training data is small: 6 to 9 working records per fold, and no working MongoDB fix anywhere
  in the pool ([training_data.md](training_data.md), #159).
