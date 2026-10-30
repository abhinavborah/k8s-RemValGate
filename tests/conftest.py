import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def upstream_parser(monkeypatch):
    """The real AIOpsLab response parser at the pin.

    aiopslab itself reads config.yml on import, so only its status module is stubbed.
    """

    class ResponseParsingError(Exception):
        pass

    for name in ("aiopslab", "aiopslab.utils", "aiopslab.utils.status"):
        monkeypatch.setitem(
            sys.modules, name, SimpleNamespace(ResponseParsingError=ResponseParsingError)
        )
    path = Path(__file__).resolve().parents[1] / "external/AIOpsLab/aiopslab/orchestrator/parser.py"
    spec = importlib.util.spec_from_file_location("upstream_parser", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ResponseParser()
