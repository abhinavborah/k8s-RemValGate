# Backup host

A second place to run a one-problem smoke test when the host of the earlier runs is not available:
the cluster on a GitHub Codespace and the model on a Kaggle T4, reached through an ngrok tunnel.
Chosen by a group member on 2026-11-09, pending group confirmation.

One development problem has been run on this host, on 2026-11-10 with the 9B, and it passed: run
`planner-dev-scale_pod_zero_social_net-mitigation-1-think-20261110t063528z`, with its files under
`k8s-RemValGate/run-logs/` on the shared drive. That is the only run here so far. The run's
`host_checks.txt` there holds the output behind the memory, speed and digest figures here and in the
README. The run script had no `--model` flag then, so the run's manifest and transcript name the
model `qwen/qwen3.5-4b`, although the 9B served it; the manifest's `model_version` names the 9B. On
2026-11-09 the script's server flags, its thinking check and `src/remvalgate/planner/chat.py` were
also run against llama-server b12110 on a Mac with a tiny model.

A result from this host is a separate configuration and is reported per host:

| | Earlier runs | This host |
|---|---|---|
| Nodes | arm64 on Docker Desktop, some images under amd64 emulation | amd64 in Docker-in-Docker, no emulation |
| Node image | `jacksonarthurclark/aiopslab-kind-arm` | `jacksonarthurclark/aiopslab-kind-x86` |
| `media-frontend` image | `jacksonarthurclark/media-frontend` (ARM build) | `yg397/media-frontend:xenial` (the chart's default) |
| Model server | LM Studio | llama-server b12110 on one T4 |
| Sampling the client does not send | LM Studio's defaults | llama-server's defaults, such as `min_p` 0.05 |
| A request longer than the context | not checked | refused with HTTP 400, which ends the run |
| The sum of a candidate's log-probabilities | not checked | covers the thinking tokens as well as the answer |

## 1. Create the Codespace

Create it from a pushed branch that holds `.devcontainer/`, on the 4-core machine (16 GB of memory,
32 GB of disk). `gh` needs the `codespace` scope, which `gh auth refresh -h github.com -s codespace`
adds. The second command opens it in the browser:

```
gh codespace create -R <owner>/k8s-RemValGate -b <branch> -m standardLinux32gb --idle-timeout 240m
gh codespace code --web -c <name the first command printed>
```

Use the terminals and the Explorer of VS Code in the browser, because the dev container defines no
SSH server.

The container installs the tools and the submodules when it is created. At every start it creates
`/run/udev`, which the kind config mounts, and raises the inotify limits, which a two-node cluster
can exceed. Check the versions:

```
kind version; kubectl version --client; helm version --short; asciinema --version; uv --version
docker version --format '{{.Server.Version}}'
```

kind v0.33.0, kubectl v1.36.1, helm v4.3.1 and engine 29.9.1 are the versions of the verified run in
the README. asciinema 3.2.1 is the current 3.x, and uv 0.13.5 is the version CI uses.

## 2. Start the cluster and record the node image

```
cd external/AIOpsLab
kind create cluster --config kind/kind-config-x86.yaml    # context kind-kind; do not pass --name
cp aiopslab/config.yml.example aiopslab/config.yml
sed -i 's/^k8s_host: .*/k8s_host: kind/' aiopslab/config.yml
cd ../..
docker image inspect --format '{{index .RepoDigests 0}}' jacksonarthurclark/aiopslab-kind-x86:latest
```

The config names the node image by the moving tag `:latest`, so a later pull can differ. The README
lists the digest from the run on 2026-11-10. A different digest on a later run is a different
cluster configuration, so report it beside the result.

## 3. Install the environment and deploy

```
uv sync --locked
uv pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu
grep -v -E '^(nvidia|cuda|triton)' requirements-retrieval.txt > /tmp/retrieval-cpu.txt
uv pip install -r /tmp/retrieval-cpu.txt
```

The planner needs the retrieval group, and the Codespace has no GPU and little disk, so this is the
README's CPU recipe. Do not run `uv sync` again afterwards, because it removes these packages;
`uv run` leaves them.

Check the install before Kaggle uses GPU quota. The second line downloads the retrieval models,
about 1.2 GB:

```
uv run python scripts/run_planner_dev.py --help > /dev/null && echo imports ok
uv run python -c "from remvalgate.retrieval.configs import load_models; load_models(); print('retrieval models ok')"
```

Deploy Social Network and install Prometheus as in the README:

```
uv run python scripts/deploy_social_network.py
kubectl --context kind-kind wait --for=condition=Ready pods --all -n test-social-network --timeout=900s
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
kubectl --context kind-kind wait --for=condition=Ready pods --all -n observe --timeout=600s
df -h /
```

The health checks are the two `kubectl wait` lines: either deploy can raise on a slow first image
pull, and that is not a failure.

## 4. Start the model server on Kaggle

Do this once the cluster is Ready, because it uses GPU quota.

1. Create an ngrok account and copy its authtoken.
2. Create a Kaggle notebook and leave it private. In the settings, set the accelerator to GPU T4 x2
   and turn the internet on.
3. Under Add-ons, Secrets, add `NGROK_AUTHTOKEN` and attach it to the notebook.
4. Set `MODEL` at the top of `scripts/serve_on_kaggle.py` to `"4b"` or `"9b"`, paste the file into a
   cell and run it. The repository is private, so the notebook cannot clone it.

The first start downloads about 0.8 GB of binaries and the GGUF (4.5 or 9.5 GB), then compiles the
CUDA kernels for the T4, so expect it to be slow. The cell keeps running while the server is up,
which is expected to keep the session alive, but that has not been tested. When you are done,
interrupt it and stop the session from Active Events at the lower left of the Kaggle page, because
an interrupted session keeps using GPU quota.

Check the output for `CUDA0` in the device list, that the `loading model` line names the GGUF you
chose, and for the line that starts `thinking on:`. The script stops if the device is missing or if
either thinking mode does not behave, and prints what the loader or the server reported. Note the
tokens per second and the memory in the `nvidia-smi` table beside the run. On 2026-11-10 the 9B used
9385 MiB of 15360 MiB on one T4 and generated 20.0 tokens/s on the script's short prompt. The 4B,
loaded once and not run on a problem, used 5575 MiB.

The last line is `base url, keep private: https://<domain>.ngrok-free.app/<secret>/v1`. Copy the
whole URL.

## 5. Check the client through the tunnel

In the Codespace, paste this line on its own. It waits, showing nothing, until you paste the URL and
press Enter:

```
read -rs LLAMA_BASE_URL && export LLAMA_BASE_URL
```

Then check the path and the client:

```
curl -s -o /dev/null -w '%{http_code}\n' "$LLAMA_BASE_URL/models"
uv run python - <<'PY'
import os
from remvalgate.planner.chat import OpenAIChat

chat = OpenAIChat(model="qwen/qwen3.5-4b", base_url=os.environ["LLAMA_BASE_URL"])
print(chat.complete([{"role": "user", "content": "Reply with the word ready."}], logprobs=True))
PY
```

The first line should print `200`. A `404` means the URL is incomplete or from an earlier start of
the script, or the session has ended. The block should end with a `Completion` with a `logprob` that
is not `None`.

## 6. Run one problem

Start the pod watch in a second terminal, before the run, and leave it running until the run ends.
The API server closes a watch after a randomized time above 30 minutes, so the loop starts it again;
each restart prints the current pods again before new events.

```
mkdir -p runs/recordings
while :; do kubectl --context kind-kind get pods -A -w --output-watch-events; sleep 1; done \
  | while IFS= read -r line; do printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$line"; done \
  > runs/recordings/<run name>.pods.log
```

Run a development problem, which logs no touch, inside the recorder, in the terminal where
`LLAMA_BASE_URL` was exported. Keep the single quotes around the command, so the recording holds the
variable's name and not the URL.

```
asciinema rec --return --title "backup host smoke <run name>" \
  --command 'uv run python scripts/run_planner_dev.py --problem dev-scale_pod_zero_social_net-mitigation-1 --candidates 2 --model <name below> --model-version "<string below>" --base-url "$LLAMA_BASE_URL"' \
  runs/recordings/<run name>.cast
```

The smoke test passes when the run ends with a transcript and a manifest under `runs/manifests/`. A
wrong diagnosis is a result, not a failure of the wiring.

Stop the pod watch by killing its terminal, because Ctrl-C did not stop the loop on 2026-11-10.
Interrupt the Kaggle cell and stop the session as in step 4. Then rename both recordings to the run
id printed in the manifest and upload them to `k8s-RemValGate/run-logs/<run id>/` on the shared
drive, next to the manifest and the transcript. Do not commit them. Delete the Codespace with
`gh codespace delete` once the files are uploaded, since a stopped one still uses storage.

## Model version strings

Pass one of these as `--model-version`, and the same model's name, `qwen/qwen3.5-4b` or
`qwen/qwen3.5-9b`, as `--model`. The run script appends `; thinking=on` or `; thinking=off`. The
hashes are the Hugging Face commits pinned in `scripts/serve_on_kaggle.py`.

```
qwen3.5-4b gguf q8_0 lmstudio-community/Qwen3.5-4B-GGUF@f9f88ac, context 32768, llama-server b12110, kaggle t4
qwen3.5-9b gguf q8_0 lmstudio-community/Qwen3.5-9B-GGUF@1379f25, context 32768, llama-server b12110, kaggle t4
```

The 4B file matches the build LM Studio served by size only: LM Studio's 5.16 GB is this file
(4.48 GB) plus the model's vision file (0.68 GB), which a text run does not load.

## Limits

- **The base URL is a password.** The secret path is the only protection on a public endpoint,
  because the planner's client sends no `Authorization` header. Do not print it or paste it anywhere
  shared, since a recording keeps every byte shown. The run scripts write `base_url` into their
  transcripts, and `--judge` writes it into the manifest too, so upload a run's files only after the
  Kaggle session has stopped. Each start of the script makes a new path, so an old one is dead.
- **Time per call.** The client waits 300 s for a reply and ngrok closes a connection after 5
  minutes with no data. A reply sends nothing until it is done, so each call has to finish within
  300 s. The worst case is 4096 tokens at the speed the script printed, plus the prompt: about 205 s
  plus the prompt for the 9B. The run on 2026-11-10 used far fewer tokens per call, so that case has
  not been timed. A Cloudflare quick tunnel ends a reply after 125 s, which is why it is not used.
- **Free plans.** ngrok's free plan allows 1 GB of data out and 20,000 requests a month, which is
  enough for a smoke test and not for a pool run: a reply with logprobs carries 20 alternatives per
  token. A Kaggle session lasts at most 12 hours, with about 30 GPU hours a week. A Codespace stops
  after its idle timeout, and terminal output counts as activity; it lives at most 12 hours. After a
  stop, `docker start kind-control-plane kind-worker` brings the cluster back.
- **Prebuilt server.** The llama.cpp build needs glibc 2.38 or later. Kaggle's image is built on
  Colab's Ubuntu 24.04 base, with glibc 2.39. On an older image the script stops at the device check
  and prints the loader's error.
