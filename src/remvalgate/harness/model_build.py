"""The model build a run is served with, read from the LM Studio host at run start (#161).

A run records what the server reports, never a string typed by hand: the LM Studio app version, its
CLI commit, the selected GGUF engine, the loaded model (key, variant, quantization, context length,
parallel slots) and the SHA-256 of the GGUF file it loaded. Two runs are comparable only when
build_key gives the same value for both; check_one_build refuses a set of runs that mixes builds,
including a mix of runs with a recorded build and runs without one (written before this record
existed).

Not reported by `lms ps`: GPU offload. It is left out rather than guessed.
"""

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

APP_PLIST = "/Applications/LM Studio.app/Contents/Info"
# fields that differ between runs of one build, so they do not take part in build_key
_PER_RUN = frozenset({"model_file"})
_ENGINE = re.compile(r"^(llama\.cpp-\S+@\S+)\s+✓\s+GGUF", re.MULTILINE)
_COMMIT = re.compile(r"CLI commit:\s*(\S+)")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# the SHA-256 of a file read once per process, keyed by path, size and modification time
_HASHES: dict[tuple[str, int, int], str] = {}


def _run(*argv: str) -> str:
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout


def _sha256(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _HASHES:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        _HASHES[key] = digest.hexdigest()
    return _HASHES[key]


def _model_file(loaded: Mapping, models: Path, hub: Path) -> Path:
    """The GGUF file of the loaded model: under models/<path> when LM Studio stores it there,
    else in a folder named by a base key of the hub's model.yaml. The mmproj vision file is left
    out, and the quantization name must be in the file name."""
    quant = loaded["quantization"]["name"].lower()
    folders = [models / loaded["path"]]
    yaml = hub / loaded["path"] / "model.yaml"
    if yaml.exists():
        keys = re.findall(r"^\s*-\s*key:\s*(\S+)", yaml.read_text(encoding="utf-8"), re.MULTILINE)
        for key in keys:
            user, _, repo = key.partition("/")
            if (models / user).is_dir():
                folders += [d for d in (models / user).iterdir() if d.name.lower() == repo.lower()]
    found = []
    for folder in folders:
        if folder.is_file() and folder.suffix == ".gguf":
            found.append(folder)
        elif folder.is_dir():
            found += [
                f
                for f in folder.glob("*.gguf")
                if not f.name.lower().startswith("mmproj") and quant in f.name.lower()
            ]
    if len(found) != 1:
        raise RuntimeError(f"cannot find the one GGUF file of {loaded['modelKey']}: {found}")
    return found[0]


def read_build(
    model_key: str,
    run: Callable[..., str] = _run,
    home: Path | None = None,
) -> dict:
    """The build of the loaded model model_key, as the host reports it.

    Raises RuntimeError when the model is not loaded or its file cannot be found, so a run never
    starts without a build record.
    """
    home = home or Path.home()
    loaded = [m for m in json.loads(run("lms", "ps", "--json")) if m.get("modelKey") == model_key]
    if len(loaded) != 1:
        raise RuntimeError(f"{model_key} is not loaded once in LM Studio (lms ps)")
    model = loaded[0]
    engines = _ENGINE.findall(_ANSI.sub("", run("lms", "runtime", "ls")))
    commit = _COMMIT.search(_ANSI.sub("", run("lms", "version")))
    try:
        app = run("defaults", "read", APP_PLIST, "CFBundleShortVersionString").strip()
    except (OSError, subprocess.CalledProcessError):
        app = "unknown"
    path = _model_file(model, home / ".lmstudio" / "models", home / ".lmstudio" / "hub" / "models")
    return {
        "server": "lmstudio",
        "app_version": app,
        "cli_commit": commit.group(1) if commit else "unknown",
        "gguf_engine": engines[0] if len(engines) == 1 else "unknown",
        "model_key": model_key,
        "variant": model.get("selectedVariant"),
        "quantization": model["quantization"]["name"],
        "context_length": model.get("contextLength"),
        "parallel": model.get("parallel"),
        "model_file": str(path),
        "model_sha256": _sha256(path),
    }


def build_key(manifest: Mapping) -> str:
    """What two comparable runs must share: the recorded build without its per-run fields, or ""
    for a manifest written before builds were recorded."""
    build = manifest.get("model_build") or {}
    return json.dumps({k: v for k, v in build.items() if k not in _PER_RUN}, sort_keys=True)


def check_one_build(manifests: Iterable[Mapping], what: str = "the runs") -> None:
    """Refuse runs of one comparison that were served by different builds. Raises ValueError."""
    keys = {build_key(m) for m in manifests if m.get("model_name") != "none"}
    if len(keys) > 1:
        raise ValueError(f"{what} were served by more than one model build: {sorted(keys)}")
