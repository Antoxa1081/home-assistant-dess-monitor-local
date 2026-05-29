"""Test bootstrap for the pure-logic layer of dess_monitor_local.

Home Assistant is not (and cannot easily be) installed under the test
interpreter, and importing the integration package normally would run
``custom_components/dess_monitor_local/__init__.py`` — which pulls in
``homeassistant.*`` and crashes collection.

We sidestep that by pre-registering the parent packages as lightweight
stub modules whose ``__path__`` points at the real source directories.
Importing a submodule (e.g. ``...api.crc``) then loads the *real* file
and resolves its relative imports against the real dirs, but the heavy
package ``__init__.py`` files are never executed.

This only works for modules that don't themselves import homeassistant
(crc, sanity, enums, the decoders, the pure helper functions in the
protocol modules). Entity/platform modules are out of scope here.
"""
from __future__ import annotations

import pathlib
import sys
import types

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PKG = _ROOT / "custom_components" / "dess_monitor_local"


def _stub_package(fullname: str, path: pathlib.Path) -> None:
    """Register ``fullname`` as a namespace-style package rooted at
    ``path`` without executing its real ``__init__.py``."""
    if fullname in sys.modules:
        return
    mod = types.ModuleType(fullname)
    mod.__path__ = [str(path)]
    mod.__package__ = fullname
    sys.modules[fullname] = mod


# Parent packages, shallowest first.
_stub_package("custom_components", _ROOT / "custom_components")
_stub_package("custom_components.dess_monitor_local", _PKG)
_stub_package("custom_components.dess_monitor_local.api", _PKG / "api")
_stub_package("custom_components.dess_monitor_local.api.decoders", _PKG / "api" / "decoders")
_stub_package("custom_components.dess_monitor_local.api.protocols", _PKG / "api" / "protocols")
_stub_package("custom_components.dess_monitor_local.api.commands", _PKG / "api" / "commands")

# External runtime deps that some protocol modules import at top level but
# the pure functions under test never actually touch. Stub them so the
# module body imports cleanly.
for _name in ("aiohttp",):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)
