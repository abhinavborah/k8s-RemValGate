"""The model build a run is served with, read from the LM Studio host at run start (#161).

A run records what the server reports, never a string typed by hand: the LM Studio app version, its
CLI commit, the selected GGUF engine, the loaded model (key, variant, quantization, context length,
parallel slots) and the SHA-256 of the GGUF file it loaded. Two runs are comparable only when
build_key gives the same value for both; check_one_build refuses a set of runs that mixes builds,
including a mix of runs with a recorded build and runs without one (written before this record
existed).

It also records the server's sampling defaults, which apply to every parameter a request does not
send (chat.py sends temperature, top-p and top-k only): the `config` fields of the model's hub
`model.yaml`, and the per-model override LM Studio keeps, if any.

Not reported by `lms ps`: GPU offload. It is left out rather than guessed.
"""

import hashlib
import json
import re
import socket
import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from urllib.parse import urlsplit

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


_FIELD = re.compile(r"^\s*- key: (\S+)\s*$")
_VALUE = re.compile(r"^\s*(value|checked): ?(.*)$")


def _scalar(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return text.strip().strip('"')


def _config_fields(text: str) -> dict:
    """The key: value pairs of the `config` section of a hub model.yaml, read without a YAML
    library. A value is a scalar on its own line, or a block of checked and value lines below
    it."""
    fields, key, block, inside = {}, None, None, False
    for line in text.splitlines():
        if not line.startswith(" ") and line.strip():
            inside = line.startswith("config:")
            continue
        if not inside:
            continue
        if field := _FIELD.match(line):
            key, block = field.group(1), None
            continue
        value = _VALUE.match(line)
        if key is None or value is None:
            continue
        name, rest = value.groups()
        if name == "value" and block is None and rest.strip():
            fields[key] = _scalar(rest)
        elif name == "value" and block is None:
            block = fields[key] = {}
        elif block is not None:
            block[name] = _scalar(rest)
    return fields


def _server_defaults(model_key: str, home: Path) -> dict:
    """The hub model.yaml's config fields as key: value, and LM Studio's per-model override."""
    hub = home / ".lmstudio" / "hub" / "models" / model_key / "model.yaml"
    fields = _config_fields(hub.read_text(encoding="utf-8")) if hub.exists() else {}
    override = (
        home / ".lmstudio" / ".internal" / "user-concrete-model-default-config"
    ) / f"{model_key}.json"
    return {
        "hub": fields,
        "override": json.loads(override.read_text(encoding="utf-8")) if override.exists() else {},
    }


LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _check_local_server(base_url: str, home: Path) -> None:
    """Refuse a base URL that is not this host's LM Studio server, so the build read here is the
    one that answers the requests."""
    config = home / ".lmstudio" / ".internal" / "http-server-config.json"
    if not config.exists():
        raise RuntimeError(f"cannot read the LM Studio server port from {config}")
    port = json.loads(config.read_text(encoding="utf-8")).get("port")
    url = urlsplit(base_url)
    if url.hostname not in LOCAL_HOSTS or url.port != port:
        raise RuntimeError(
            f"{base_url} is not this host's LM Studio server (localhost port {port}); a run on "
            "another server is a separate configuration (#161)"
        )


def read_build(
    model_key: str,
    base_url: str,
    run: Callable[..., str] = _run,
    home: Path | None = None,
) -> dict:
    """The build of the loaded model model_key, as the host reports it.

    base_url is the server the run sends its requests to; it must be this host's LM Studio server.
    The base URL and the host name are part of the record, so builds on two hosts stay apart.
    Raises RuntimeError for any other server, when the model is not loaded, its file cannot be
    found, or the app version, CLI commit or selected GGUF engine cannot be read, so a run never
    starts without a full build record.
    """
    home = home or Path.home()
    _check_local_server(base_url, home)
    loaded = [m for m in json.loads(run("lms", "ps", "--json")) if m.get("modelKey") == model_key]
    if len(loaded) != 1:
        raise RuntimeError(f"{model_key} is not loaded once in LM Studio (lms ps)")
    model = loaded[0]
    engines = _ENGINE.findall(_ANSI.sub("", run("lms", "runtime", "ls")))
    commit = _COMMIT.search(_ANSI.sub("", run("lms", "version")))
    try:
        app = run("defaults", "read", APP_PLIST, "CFBundleShortVersionString").strip()
    except (OSError, subprocess.CalledProcessError):
        app = ""
    # a field the host does not report stops the run: two builds that both read as unknown would
    # pass check_one_build
    if not app:
        raise RuntimeError(f"cannot read the LM Studio app version from {APP_PLIST}")
    if commit is None:
        raise RuntimeError("cannot read the LM Studio CLI commit (lms version)")
    if len(engines) != 1:
        raise RuntimeError(f"expected one selected GGUF engine (lms runtime ls), got {engines}")
    path = _model_file(model, home / ".lmstudio" / "models", home / ".lmstudio" / "hub" / "models")
    return {
        "server": "lmstudio",
        "base_url": base_url,
        "host": socket.gethostname(),
        "app_version": app,
        "cli_commit": commit.group(1),
        "gguf_engine": engines[0],
        "model_key": model_key,
        "variant": model.get("selectedVariant"),
        "quantization": model["quantization"]["name"],
        "context_length": model.get("contextLength"),
        "parallel": model.get("parallel"),
        "model_file": str(path),
        "model_sha256": _sha256(path),
        "server_defaults": _server_defaults(model_key, home),
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
