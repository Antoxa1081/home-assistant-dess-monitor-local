"""Test bootstrap for dess_monitor_local.

Two modes, chosen automatically by whether Home Assistant is importable:

* **HA present** (local dev, the dedicated CI "entities" job): do nothing.
  The real package ``__init__.py`` runs, every module imports normally,
  and both the pure-logic tests and the entity tests
  (``test_entities.py``) execute against the real code.

* **HA absent** (the lightweight CI matrix on 3.12/3.13): pre-register the
  parent packages as stub modules whose ``__path__`` points at the real
  source dirs. Importing a submodule (e.g. ``...api.crc``) then loads the
  real file and resolves relative imports, but the HA-importing package
  ``__init__.py`` files never execute. Entity tests skip themselves via
  ``pytest.importorskip("homeassistant")``.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PKG = _ROOT / "custom_components" / "dess_monitor_local"

_HA_AVAILABLE = importlib.util.find_spec("homeassistant") is not None


def _stub_package(fullname: str, path: pathlib.Path) -> None:
    """Register ``fullname`` as a namespace-style package rooted at
    ``path`` without executing its real ``__init__.py``."""
    if fullname in sys.modules:
        return
    mod = types.ModuleType(fullname)
    mod.__path__ = [str(path)]
    mod.__package__ = fullname
    sys.modules[fullname] = mod


if not _HA_AVAILABLE:
    # Parent packages, shallowest first.
    _stub_package("custom_components", _ROOT / "custom_components")
    _stub_package("custom_components.dess_monitor_local", _PKG)
    _stub_package("custom_components.dess_monitor_local.api", _PKG / "api")
    _stub_package("custom_components.dess_monitor_local.api.decoders", _PKG / "api" / "decoders")
    _stub_package("custom_components.dess_monitor_local.api.protocols", _PKG / "api" / "protocols")
    _stub_package("custom_components.dess_monitor_local.api.commands", _PKG / "api" / "commands")
    _stub_package("custom_components.dess_monitor_local.coordinators", _PKG / "coordinators")

    # External runtime deps imported at module top by some protocol modules
    # but never touched by the pure functions under test.
    for _name in ("aiohttp",):
        if _name not in sys.modules:
            sys.modules[_name] = types.ModuleType(_name)
