#!/usr/bin/env bash
# Serve one GGUF of the fine-tuned planner study on the GPU host (story E5-S7, #159): start
# llama-server, open a quick tunnel to it, and write its record file with write_served_record.py.
# Prints "<name> <tunnel address>". The API key file is read by llama-server and never printed.
#
#   serve_on_gpu_host.sh <name> <gguf> <port> <host label> [<fold> <adapter folder>]
#
# Needs, in WORK (default the current folder): llama.cpp built with CUDA (llama.cpp/build/bin),
# cloudflared, and the API key at $API_KEY_FILE (default /root/secret/api_key).
set -euo pipefail
name=$1; gguf=$2; port=$3; host=$4; fold=${5:-}; adapter=${6:-}
WORK=${WORK:-$PWD}
KEY=${API_KEY_FILE:-/root/secret/api_key}
here=$(cd "$(dirname "$0")" && pwd)
args=(--jinja -c 32768 -ngl 99 --parallel 1 --alias qwen/qwen3.5-4b)
cd "$WORK"
setsid nohup ./llama.cpp/build/bin/llama-server -m "$gguf" "${args[@]}" --host 127.0.0.1 \
    --port "$port" --api-key-file "$KEY" > "server_$name.log" 2>&1 < /dev/null &
until grep -q 'listening on http' "server_$name.log"; do
    sleep 1
    if grep -qiE 'error|failed' "server_$name.log"; then tail -5 "server_$name.log"; exit 1; fi
done
setsid nohup ./cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$port" \
    > "tunnel_$name.log" 2>&1 < /dev/null &
until grep -qo 'https://[a-z0-9-]*\.trycloudflare\.com' "tunnel_$name.log"; do sleep 1; done
fold_args=()
if [ -n "$fold" ]; then fold_args=(--fold "$fold" --adapter "$adapter"); fi
python3 "$here/write_served_record.py" --gguf "$gguf" --llama-cpp llama.cpp --host "$host" \
    --out "$name.served.json" "${fold_args[@]}" -- "${args[@]}" > /dev/null
echo "$name $(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "tunnel_$name.log" | head -1)"
