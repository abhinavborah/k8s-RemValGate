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

## Threshold

The threshold is the minimum gain committed with gate G3 (#53, R6.6,
[baselines_pool.md](baselines_pool.md), `harness.g3.meets_min_gain`), applied to the combined
counts at n = 16: the claim is supported only with 5 wins and 0 losses, or 7 wins and 1 loss
against 3c. Two or more losses falsify it whatever the wins, and anything else falsifies it too.
That threshold was committed for the gate against baseline 5. Applying it to this comparison
(3cft against 3c) is proposed and pending group confirmation (#56), and is disclosed as such.

The two-sided McNemar mid-p (#21) and the Bonett-Price interval for the paired difference (#21)
are reported beside the claim and do not decide it. Each seed's counts are descriptive.

What G3 already bounds: on the untuned planner's own candidates, only 1 problem is recoverable
(#165 step 2), so a ranking over them can gain at most 1. 3cft samples its own candidates, so it is
not bound by that count.

## Reported beside the claim

- Baseline 5, the fine-tuned ranking over 3c's candidates: 7.0 of 16 on 2026-11-14, the same as
  3c's top pick in the labelling run ([baseline5.md](baseline5.md), the result and its run record
  at the end of the 2026-11-14 section).
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

#165 step 3, run 2026-11-14 from a clean checkout at `f434ca2`: the #159 labels 15:12 to 17:12 IST,
then the 64 batch runs, started 17:12 to 21:27 IST and ended 21:32 IST (16:02 UTC); the result was
read at 21:33 IST. The batch covers 16 problems, 2 arms and seeds 1000 and 1001, served by
llama.cpp `61dbf49` on one A100 SXM 80 GB host (the base Qwen3.5-4B Q8_0 for 3c, each fold's merged
GGUF for 3cft). Every manifest was checked: clean commit, the right build per arm by SHA-256, the
problem's own fold for 3cft, one touch per run. The threshold was confirmed on #56 and the labels
adopted (#181) before the batch ended and before its result was read; see Threshold.

| | 3c | 3cft | wins (only 3cft) | losses (only 3c) | mid-p | Bonett-Price 95% |
|---|---|---|---|---|---|---|
| both seeds (the claim) | 7 | 5 | 0 | 2 | 0.25 | [-0.32, 0.10] |
| seed 1000 | 7 | 7 | 1 | 1 | 0.75 | [-0.22, 0.22] |
| seed 1001 | 10 | 8 | 1 | 3 | 0.375 | [-0.37, 0.15] |

**The claim is falsified:** 0 wins and 2 losses at both seeds, against a bar of 5 wins and 0
losses or 7 and 1. The losses are `pool-assign_to_non_existent_node_social_net-mitigation-3` and
`pool-scale_pod_zero_social_net-mitigation-2`. The fine-tuned planner is therefore not the final
system. Using 3c for the confirmation slice (#59) and the singleton check (#73) is proposed,
pending group confirmation on #59.

Reported beside it:

| | top pick solves | problems with a working candidate | mixed | recoverable |
|---|---|---|---|---|
| 3c labels (#165 step 2, seed 0) | 7 | 8 | 0 | 1 |
| 3cft labels (#159, seed 0) | 8 | 7 | 1 | 0 |

- Baseline 5 solves 7.0 of 16 ([baseline5.md](baseline5.md)).
- Baseline 6, the prompted judge over the same 3c candidates (#185), solves 8 of 16 and escalates on
  3 (database problems whose candidates the allow-list refused, so nothing was scored). It ranks a
  working candidate first on the one recoverable problem (`k8s_target_port-misconfig-mitigation-2`)
  and loses none of 3c's 7, so it reaches the upper end of the bound of 5 to 8
  ([baseline5.md](baseline5.md)). It ran on 2026-11-15 from `1b07912`, the commit of #185 before its
  review, on this host's LM Studio (Qwen3.5-4B Q8_0, SHA-256 `c3fc7bca...e028`), thinking on,
  temperature 0, threshold 0; the judge is the planner's model, so the two share a model family.
  Files: `runs/baseline6/` and `runs/recordings/b6_pool_20261115.*`.
- The labelling runs are one draw at seed 0, so their top-pick counts (7 and 8) are descriptive and
  not the claim.

Run notes: one planner retry in the fold 1 labels (`RemoteDisconnected`, recovered; requests carry
a fixed seed). The batch was paused once between two runs (17:17 to 17:41 IST) for a network change
on the run host; no run was cut. Each labelling fold and the batch were recorded with asciinema
(`runs/recordings/rerun165_step3_*.cast`, with their stdout logs) beside a timestamped pod watch
(`rerun165_step3_20261114.pods.log`); all are in the archive below.

Files, on the shared drive under `k8s-RemValGate/run-logs/rerun165_step3_20261114/`, each with its
SHA-256 (a `.sha256` file beside the first three):

| File | Bytes | SHA-256 | Holds |
|---|---|---|---|
| `step3.tar.gz` | 12436550 | `12200ed8f809e75dd35f4cb101177e7d6cc32482ca7887a3678051d6bc80292b` | the 64 batch runs, the #159 labels, the step 3 recordings and pod watch, R5.4, the touch log |
| `r34.tar.gz` | 2185174 | `af4e3ab15baeac87d6e4ce7eeeddf3441e95bf1f6129e3844e5cda82d5247f5b` | the #34 labels and recordings |
| `b6.tar.gz` | 18298 | `3dd462f23b5c113d79204c6d32322ff6716439516d3d1d8fd305433d7d5138c0` | the baseline 6 manifests, results, summary and recording |
| `scores.tar.gz` | 4572 | `eb5f3ffa6c12fbce347eb1539adc02a3cacd2f16844b871ab789caa7bebe1d8a` | the baseline 5 scores, one file per fold |
| `podlogs3.tar.gz` | 286966 | `3b95b430740547cd751cdabb305d828b9895f1282e51227f14db3c9f5f9eb250` | the GPU host's server and tunnel logs and served records |
| `adapters.tar` | 339937280 | `db17a79f4bbed27df064df455d4998a360aa632a6b5b2be83f3ce2b241fa48c2` | the 4 per-fold LoRA adapters |

## Limits

- n = 16 problems, so only a large gain can pass the test. The minimum gain above says how large.
- The served build (llama-server on a GPU host) differs from LM Studio on the run host, so these
  3c runs are a separate configuration from #165 step 1 and are not pooled with it.
- The training data is small: 6 to 9 working records per fold, and no working MongoDB fix anywhere
  in the pool ([training_data.md](training_data.md), #159).
