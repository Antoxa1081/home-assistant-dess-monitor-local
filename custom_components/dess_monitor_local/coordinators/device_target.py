"""A pollable inverter target behind a config entry.

Separates *identity* from *transport* so one config entry can own several
inverters:

- ``id`` is the stable identity used for entity ``unique_id``s, HA device
  registry identifiers, and the coordinator data-map key.
- ``uri`` is the transport address commands are sent to (the ``device``
  string fed to the dispatcher / adapters).

For legacy single-device entries the two are identical (the device URI), so
existing entity ``unique_id``s and device identifiers are preserved exactly.
For EyBond hub children, ``id`` is ``eybond:<pn>:<devaddr>`` (stable across
reconnects and IP changes) while ``uri`` carries the live connection params.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceTarget:
    id: str
    uri: str
    protocol: str
    name: str
