import ast
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from remvalgate import overlay
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, Slice, load_registry


def _factory():
    return object()


def test_shipped_overlay_is_the_development_slice():
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    dev = {i for i, s in registry.slices.items() if s is Slice.DEVELOPMENT}
    assert set(overlay.PROBLEMS) == dev
    assert overlay.register({"upstream-1": _factory}) == list(overlay.PROBLEMS)


def test_register_adds_entries_and_returns_their_ids():
    registry = {"upstream-1": _factory}
    added = overlay.register(registry, {"ours-1": _factory, "ours-2": _factory})
    assert added == ["ours-1", "ours-2"]
    assert set(registry) == {"upstream-1", "ours-1", "ours-2"}


def test_register_refuses_an_upstream_id_and_adds_nothing():
    registry = {"upstream-1": _factory}
    with pytest.raises(ValueError, match="upstream-1"):
        overlay.register(registry, {"ours-1": _factory, "upstream-1": _factory})
    assert list(registry) == ["upstream-1"]


_ids = st.sets(st.text(alphabet="abcdef-_0123456789", min_size=1, max_size=8), max_size=6)


@given(registry_ids=_ids, overlay_ids=_ids)
def test_register_is_the_union_or_a_refusal_that_changes_nothing(registry_ids, overlay_ids):
    registry = dict.fromkeys(registry_ids, _factory)
    before = dict(registry)
    problems = dict.fromkeys(overlay_ids, _factory)
    if registry_ids & overlay_ids:
        with pytest.raises(ValueError):
            overlay.register(registry, problems)
        assert registry == before
    else:
        overlay.register(registry, problems)
        assert set(registry) == registry_ids | overlay_ids


def test_importing_the_overlay_does_not_load_aiopslab():
    # aiopslab reads config.yml on import, which CI does not have
    code = (
        "import sys, remvalgate.overlay\n"
        "assert not any(m.startswith('aiopslab') for m in sys.modules)"
    )
    subprocess.run([sys.executable, "-I", "-c", code], check=True)


def test_every_overlay_factory_names_a_dev_problem():
    # dev_problems imports aiopslab, so its top-level names are read from the source
    tree = ast.parse(Path(overlay.__file__).with_name("dev_problems.py").read_text())
    defined = {n.name for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    names = {f.__closure__[0].cell_contents for f in overlay.PROBLEMS.values()}
    assert names <= defined
