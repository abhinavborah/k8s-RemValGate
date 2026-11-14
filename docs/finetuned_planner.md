# Fine-tuned planner: serving and the 3cft arm

Story E5-S7 (#159), the fine-tuning ablation of #165 step 3. The fine-tuned planner is 3c's
configuration (retrieval (iii), the same tools, prompt, sampling and turn cap) with one change: the
model is the base Qwen3.5-4B with the problem's own fold's LoRA adapter merged in. A problem is
never served by an adapter trained on it.

## Training and merge

- Each fold's adapter is trained by `scripts/baseline5_kaggle.py` (LoRA r16, alpha 32, 3 epochs,
  label 1 records only; [baseline5.md](baseline5.md)). The same run scores baseline 5.
- `scripts/merge_baseline5_adapter.py` merges the adapter into the base model at the pinned
  revision and keeps the base model's tokenizer, chat template and processor files.
- llama.cpp's `convert_hf_to_gguf.py` converts the merged model with `--outtype q8_0`, the
  quantization of the untuned runs. The llama.cpp commit is recorded with each server.
- The 3c control's GGUF is made the same way: the merge script with no adapter, then the same
  conversion. Saving through transformers drops the model's next-token prediction layer
  (`blk.32`, used only for speculative decoding, which these servers do not run), so a merged
  model has 378 tensors against 393 for a direct conversion of the original files. Building the
  control through the same path gives both arms the same structure (4,482,400,128 bytes each), so
  the two GGUFs differ in the LoRA weights only.

## Serving

The fine-tuned GGUF files stay on the GPU host. Each is served by llama.cpp's `llama-server`
(`--jinja -c 32768 -ngl 99 --parallel 1`) behind a quick tunnel, and the run host sends the planner
requests there. The base GGUF, converted the same way, is served the same way as the 3c control,
so the two compared arms share one server, one build and one host (#161). 3c's runs on the run
host's LM Studio are a separate configuration and are reported apart.

- **Build record.** When a server starts, the GPU host writes a record file: llama.cpp commit,
  host, GPU, GGUF file name and SHA-256, quantization, context length and server arguments.
  `model_build.read_served_build` checks it against the running server's `/props` (file name,
  context length, build) and refuses a mismatch before the run's touch. The tunnel address is
  recorded per run as `endpoint` and is not part of `build_key`, since it changes on a restart.
- **Access.** The server requires an API key. The run host reads it from the environment
  (`REMVALGATE_PLANNER_API_KEY`), and it is never written to the repository or a manifest.
- **Dropped connections.** `planner/chat.py` retries a dropped connection, a timeout or a 5xx up
  to 3 times, 5 and 15 seconds apart, and prints each retry. The request carries a fixed seed, so
  a retry asks for the same reply. A 4xx is never retried.

## Runs

- `scripts/run_baselines_pool.py --baseline 3cft --fold-servers <file>` runs the fine-tuned arm.
  The file maps each of the 4 folds to its `base_url` and record, and a pool problem is served by
  its own fold's model. A development problem is in no fold's training data and runs with
  `--dev-fold <f>`, the dry run before the pool. The manifest records the fold in `sampling.fold`
  and the fold's build in `model_build`.
- The 3c control runs in the same batch with `--baseline 3c`, `--base-url <base server>` and
  `--served-build <base record>`, interleaved with 3cft as #161 requires.
- `scripts/run_oracle_labels.py --fold <f> --base-url <fold server> --served-build <record>`
  samples and labels the fine-tuned planner's candidates on fold f's pool problems (all of them by
  default) and refuses a pool problem of another fold (#159). Its labels go to
  `runs/oracle_3cft/`, with run ids `oracle3cft-fold<f>-...`, so no glob of `runs/oracle/` mixes
  them with the untuned labels.

## Checks before the pool runs (2026-11-14)

- **Load test.** 30 requests through the tunnel with the longest 3c prompt of #165 step 1 (14525
  prompt tokens): 30 succeeded, 0 retries, median 12.3 s, longest 35.6 s.
- **End to end.** One run of 3c on `dev-k8s_target_port-misconfig-mitigation-1` at seed 1000
  through the tunnel: completed with a full record (`server` llama-server, build
  `b3382-61dbf492`, `base-q8_0.gguf`), 16 valid tool calls, 0 retries. It reached the turn cap,
  as 3c on LM Studio did on this problem at seed 1000 in #165 step 0. One run is a sanity check,
  not a comparison.
- **Dry run of 3cft.** 3c (control GGUF `basemerged-q8_0.gguf`) and 3cft (`--dev-fold 0`,
  `fold0-q8_0.gguf`) on `dev-scale_pod_zero_social_net-mitigation-1` at seed 1000, interleaved:
  both RECOVERED, each manifest names its own server's GGUF and SHA-256, `sampling.fold` is 0 for
  3cft and null for 3c, 0 retries. One run per arm checks the path, not the comparison.
- **Template.** The base GGUF has the same chat template, tokens, merges and special token ids
  as LM Studio's Qwen3.5-4B Q8_0 (equal hashes). Its tensor layout is the newer one of llama.cpp
  `61dbf49` (fused `attn_qkv_gate` and `ssm_beta_alpha`, and the next-token prediction block).

## Limits

- The served build differs from the LM Studio build of every earlier run (another server, host,
  GPU and llama.cpp version), so 3cft is compared with the 3c control of the same server only.
- A quick tunnel has no uptime guarantee and a new address after each restart. A restart between
  runs needs a new `--fold-servers` file; a drop during a run fails that run after 3 attempts.
