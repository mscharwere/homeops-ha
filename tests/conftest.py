"""Test bootstrap for the HomeOps integration.

``custom_components/homeops/__init__.py`` imports Home Assistant, which is not a
test dependency here — importing the package normally would drag the whole HA
runtime in just to reach two pure functions.

Instead we register a synthetic parent package whose ``__path__`` points at the
integration directory, then load only the modules under test into it. Relative
imports (``from .const import ...``) resolve against that package, so ``api.py``
loads unmodified without ``__init__.py`` ever executing.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

PKG_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "homeops"
SHIM = "homeops_under_test"


def _load(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, PKG_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _bootstrap():
    if SHIM not in sys.modules:
        package = types.ModuleType(SHIM)
        package.__path__ = [str(PKG_DIR)]
        sys.modules[SHIM] = package
        _load(f"{SHIM}.const", "const.py")
    return _load(f"{SHIM}.api", "api.py")


@pytest.fixture(scope="session")
def api_module():
    """The HomeOps API client module, loaded without Home Assistant."""
    return _bootstrap()
