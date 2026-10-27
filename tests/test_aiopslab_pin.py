import subprocess
from pathlib import Path

import pytest

from remvalgate.aiopslab_pin import SUBMODULE, pinned_commit

PIN = "a" * 40


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def test_reads_the_gitlink_without_a_checkout(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{PIN},{SUBMODULE}")
    _git(repo, "commit", "-q", "-m", "pin")
    assert pinned_commit(repo) == PIN


def test_refuses_when_head_has_no_submodule_link(tmp_path):
    repo = _repo(tmp_path)
    (repo / "f").write_text("x")
    _git(repo, "add", "f")
    _git(repo, "commit", "-q", "-m", "no pin")
    with pytest.raises(ValueError, match=SUBMODULE):
        pinned_commit(repo)


def test_the_repository_pin_is_a_commit_hash():
    assert len(pinned_commit(Path(__file__).resolve().parents[1])) == 40
