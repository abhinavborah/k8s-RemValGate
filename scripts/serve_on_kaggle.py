"""Serve a Qwen3.5 GGUF from a Kaggle T4 with llama-server and open an ngrok tunnel to it.

Run on a Kaggle notebook with the T4 x2 accelerator, the internet on and the secret
NGROK_AUTHTOKEN attached (docs/backup_host.md). Not part of the package or the tests: it needs the
GPU and Kaggle's secrets client. Standard library only, so nothing is installed in the notebook.

Set MODEL, paste this file into a cell and run it. The cell keeps running while the server is up,
which keeps the session alive; it ends when the server stops or the cell is interrupted. The URL
it prints goes to the run scripts' --base-url and holds the secret path, so it is a password.
"""

import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "4b"  # "4b" or "9b"

# repository, commit, file and SHA-256 of each GGUF
MODELS = {
    "4b": (
        "lmstudio-community/Qwen3.5-4B-GGUF",
        "f9f88ac3e234be915e23811a6d28ea287bdb927e",
        "Qwen3.5-4B-Q8_0.gguf",
        "c3fc7bcaf6f75b8f7ceeead9a769f5a7a9f86a8180af1cfb2b72958dcad8e028",
    ),
    "9b": (
        "lmstudio-community/Qwen3.5-9B-GGUF",
        "1379f25c6b505a3fc737bd7818cb09389cf807c1",
        "Qwen3.5-9B-Q8_0.gguf",
        "096a84703a334662086e4b46b6a4dd896bad10a46f67cd84a53cfe1420b24717",
    ),
}
LLAMA_TAG = "b12110"  # llama.cpp v0.9.0
LLAMA_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}"
LLAMA_SUFFIX = f"{LLAMA_TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz"
# the cudart archive holds libcudart.so.12, which the image's CUDA 13 toolkit does not provide
LLAMA_ARCHIVES = {
    "llama": "4af1e1da23661db4a5b84b29aae16ebc053df689c5b8b1427c4670f2c6406d8c",
    "cudart-llama": "6f1d562086d84ae85df880d3711079ec225602b51d530608e5001600625067dd",
}
NGROK_URL = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz"
WORK = Path("/tmp/backup_host")  # not /kaggle/working, which a saved version keeps
LOG = WORK / "server.log"
PORT = 18080  # an uncommon port, since a notebook image may already use 8080
CONTEXT = 32768
# the first start compiles the CUDA kernels for the T4, on top of loading the model
READY_TIMEOUT_S = 900
PROMPT = "In one sentence, what does a Kubernetes readiness probe do?"


def show(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    # a child's own output can miss the notebook cell, so it is captured and printed here
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    print(result.stdout, result.stderr)
    return result


def print_log_tail() -> None:
    print("\n".join(LOG.read_text(errors="replace").splitlines()[-40:]), flush=True)


def sha256_of(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def fetch(url: str, path: Path, sha256: str) -> None:
    if path.exists() and sha256_of(path) == sha256:
        return
    subprocess.run(["curl", "-fL", "--retry", "5", "-C", "-", "-o", str(path), url], check=True)
    if sha256_of(path) != sha256:
        path.unlink()
        raise RuntimeError(f"{path.name}: checksum mismatch, file removed")


def unpack(archive: Path, strip: int) -> None:
    subprocess.run(
        ["tar", "-xzf", str(archive), "-C", str(WORK), f"--strip-components={strip}"], check=True
    )


def server_command(gguf: Path, prefix: str) -> list[str]:
    # no --reasoning flag: "on" writes enable_thinking into the template kwargs, which override a
    # request's reasoning_effort none, and the template already thinks by default
    return [
        str(WORK / "llama-server"),
        "-m",
        str(gguf),
        "-c",
        str(CONTEXT),
        # one slot, so the whole context belongs to the one conversation
        "-np",
        "1",
        # all layers or a load error, never a silent spill to the CPU
        "-ngl",
        "all",
        "--fit",
        "off",
        "--no-webui",
        # the planner's client sends no Authorization header, so the path is the credential
        "--api-prefix",
        prefix,
        "--port",
        str(PORT),
    ]


def wait_ready(server: subprocess.Popen, url: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"llama-server exited with {server.returncode}")
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=5):
                return
        # the server answers 503 while the model loads
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(5)
    raise RuntimeError(f"llama-server not ready after {READY_TIMEOUT_S} s")


def ask(base: str, **extra) -> dict:
    body = {"messages": [{"role": "user", "content": PROMPT}], "max_tokens": 2048, **extra}
    request = urllib.request.Request(
        f"{base}/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)


def check_thinking(base: str) -> None:
    on = ask(base, logprobs=True)
    off = ask(base, reasoning_effort="none")
    assert on["choices"][0]["message"].get("reasoning_content"), "thinking on did not think"
    assert not off["choices"][0]["message"].get("reasoning_content"), "thinking off still thought"
    assert off["choices"][0]["message"].get("content"), "thinking off returned no content"
    tokens = (on["choices"][0].get("logprobs") or {}).get("content") or ()
    print(
        f"thinking on: {on['usage']['completion_tokens']} completion tokens, "
        f"{len(tokens)} with logprobs, "
        f"{on['timings']['predicted_per_second']:.1f} tokens/s generated"
    )


def open_tunnel() -> tuple[subprocess.Popen, str]:
    # imported here because the module exists only on Kaggle
    from kaggle_secrets import UserSecretsClient

    # shortcut: the stable URL moves, so the agent is not pinned; pin it if a run depends on it
    archive = WORK / "ngrok.tgz"
    subprocess.run(["curl", "-fsSL", "-o", str(archive), NGROK_URL], check=True)
    unpack(archive, 0)
    # the agent reads this variable, so the token reaches neither a file nor the command line
    env = os.environ | {"NGROK_AUTHTOKEN": UserSecretsClient().get_secret("NGROK_AUTHTOKEN")}
    tunnel = subprocess.Popen(
        [str(WORK / "ngrok"), "http", str(PORT)], env=env, stdout=subprocess.DEVNULL
    )
    for _ in range(60):
        if tunnel.poll() is not None:
            raise RuntimeError(f"ngrok exited with {tunnel.returncode}")
        try:
            with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=5) as response:
                tunnels = json.load(response)["tunnels"]
            if tunnels:
                return tunnel, tunnels[0]["public_url"]
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(1)
    tunnel.terminate()
    raise RuntimeError("ngrok opened no tunnel")


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    show(["nvidia-smi"])
    for name, sha256 in LLAMA_ARCHIVES.items():
        archive = WORK / f"{name}-{LLAMA_SUFFIX}"
        fetch(f"{LLAMA_URL}/{archive.name}", archive, sha256)
        unpack(archive, 1)
    repo, revision, filename, sha256 = MODELS[MODEL]
    gguf = WORK / filename
    fetch(f"https://huggingface.co/{repo}/resolve/{revision}/{filename}", gguf, sha256)

    env = os.environ | {
        # the default split would spread the layers over both T4s
        "CUDA_VISIBLE_DEVICES": "0",
        # an empty entry would make the loader search the current directory
        "LD_LIBRARY_PATH": ":".join(filter(None, (str(WORK), os.environ.get("LD_LIBRARY_PATH")))),
    }
    devices = show([str(WORK / "llama-server"), "--list-devices"], env=env)
    if "CUDA0" not in devices.stdout + devices.stderr:
        # the release build loads its backends silently, so ask the loader what is missing
        ldd = subprocess.run(
            ["ldd", str(WORK / "libggml-cuda.so")], env=env, capture_output=True, text=True
        )
        print("\n".join(s for s in (ldd.stdout + ldd.stderr).splitlines() if "not found" in s))
        print("result: FAIL, llama-server sees no CUDA device")
        return 1

    prefix = f"/{secrets.token_hex(24)}"
    local = f"http://127.0.0.1:{PORT}{prefix}"
    with LOG.open("w") as log:
        server = subprocess.Popen(
            server_command(gguf, prefix), env=env, stdout=log, stderr=subprocess.STDOUT
        )
    tunnel = None
    try:
        wait_ready(server, local)
        print_log_tail()
        check_thinking(f"{local}/v1")
        show(["nvidia-smi", "--query-gpu=index,memory.used,memory.total", "--format=csv"])
        tunnel, public = open_tunnel()
        print(f"base url, keep private: {public}{prefix}/v1", flush=True)
        code = server.wait()
        print(f"llama-server exited with {code}")
        print_log_tail()
        return code
    except Exception:
        print_log_tail()
        raise
    finally:
        for process in (tunnel, server):
            if process is not None:
                process.terminate()


if __name__ == "__main__":
    sys.exit(main())
