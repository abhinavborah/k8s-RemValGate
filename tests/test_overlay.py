import subprocess
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from remvalgate import overlay


def _factory():
    return object()


def test_shipped_overlay_is_empty_and_adds_nothing():
    registry = {"upstream-1": _factory}
    assert overlay.PROBLEMS == {}
    assert overlay.register(registry) == []
    assert list(registry) == ["upstream-1"]


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
