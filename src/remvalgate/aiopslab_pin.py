"""The AIOpsLab commit this repository pins, read from the submodule link in git."""

import re
import subprocess
from pathlib import Path

SUBMODULE = "external/AIOpsLab"
_LINK = re.compile(r"160000 commit ([0-9a-f]{40})\t")


def pinned_commit(repo: str | Path) -> str:
    """Return the committed pin, for the aiopslab_commit argument of start_run.

    Read from HEAD, not from the checkout under external/, so a submodule moved by hand and not
    committed does not change it. The run manifest records such a tree as dirty, and refuses it for
    pool and confirmation runs.
    """
    out = subprocess.run(
        ["git", "ls-tree", "HEAD", "--", SUBMODULE],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    match = _LINK.match(out)
    if not match:
        raise ValueError(f"{SUBMODULE} is not a submodule link in HEAD of {repo}")
    return match.group(1)
