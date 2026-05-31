"""Tests for the per-child DeviceTarget wiring (hub.init + InverterDevice).

A hub entry polls several inverters from one entry; each is a DeviceTarget
(stable id + transport uri + per-device protocol + name). Hub.init turns
targets into InverterDevice items that the platforms build entities from.

These import hub.py, which pulls in Home Assistant, so they self-skip on the
pure CI matrix and run in the hass job (and locally where HA is installed).
"""
import asyncio

import pytest

pytest.importorskip("homeassistant")

from custom_components.dess_monitor_local.coordinators.device_target import (  # noqa: E402
    DeviceTarget,
)
from custom_components.dess_monitor_local.hub import Hub  # noqa: E402


class _Coord:
    """Minimal stand-in exposing the .devices the Hub reads."""

    def __init__(self, devices):
        self.devices = devices


def test_device_target_is_frozen():
    t = DeviceTarget(id="x", uri="x", protocol="voltronic", name="n")
    with pytest.raises(Exception):
        t.id = "y"  # frozen dataclass


def test_hub_init_builds_items_from_targets():
    coord = _Coord([
        DeviceTarget(
            id="eybond:PN1:1",
            uri="eybond://0.0.0.0:8899/1?pn=PN1",
            protocol="voltronic",
            name="Inv A",
        ),
        DeviceTarget(
            id="eybond:PN2:1",
            uri="eybond-pi18://0.0.0.0:8899/1?pn=PN2",
            protocol="pi18",
            name="Inv B",
        ),
    ])
    hub = Hub(None, "hubname", coord)
    asyncio.run(hub.init())

    assert len(hub.items) == 2
    a, b = hub.items
    # Identity is the stable target id (entity unique_ids / device identifiers).
    assert a.inverter_id == "eybond:PN1:1"
    # Transport URI is what command sends use.
    assert a.device_data == "eybond://0.0.0.0:8899/1?pn=PN1"
    # Per-item protocol so the hub can mix protocols across children.
    assert a.protocol == "voltronic"
    assert a.name == "Inv A"
    assert b.protocol == "pi18"
    assert b.inverter_id == "eybond:PN2:1"


def test_hub_init_tolerates_bare_string_device():
    # Defensive: a plain URI string is treated as legacy (id == uri).
    hub = Hub(None, "hubname", _Coord(["tcp://1.2.3.4:8899"]))
    asyncio.run(hub.init())
    assert len(hub.items) == 1
    item = hub.items[0]
    assert item.inverter_id == "tcp://1.2.3.4:8899"
    assert item.device_data == "tcp://1.2.3.4:8899"
    # No protocol info available from a bare string.
    assert item.protocol is None
    assert item.name == "hubname"


def test_hub_rebuild_items_reflects_new_targets():
    # In-place reconcile (A+2b): after the coordinator's poll set changes,
    # Hub.rebuild_items() rebuilds the child list so the platforms recreate
    # entities for exactly the new set.
    coord = _Coord([
        DeviceTarget(id="eybond:PN1:1", uri="eybond://h:8899/1?pn=PN1",
                     protocol="voltronic", name="A"),
    ])
    hub = Hub(None, "hub", coord)
    asyncio.run(hub.init())
    assert [i.inverter_id for i in hub.items] == ["eybond:PN1:1"]

    # PN1 removed, PN2 (pi18) + PN3 (modbus) added.
    coord.devices = [
        DeviceTarget(id="eybond:PN2:1", uri="eybond-pi18://h:8899/1?pn=PN2",
                     protocol="pi18", name="B"),
        DeviceTarget(id="eybond:PN3:2", uri="eybond-modbus://h:8899/2?pn=PN3",
                     protocol="modbus", name="C"),
    ]
    asyncio.run(hub.rebuild_items())
    assert [i.inverter_id for i in hub.items] == ["eybond:PN2:1", "eybond:PN3:2"]
    assert [i.protocol for i in hub.items] == ["pi18", "modbus"]


def test_coordinator_set_targets_swaps_poll_set():
    # set_targets swaps both the explicit target list and the live .devices the
    # update cycle reads — the runtime hook the in-place reconcile uses.
    from custom_components.dess_monitor_local.coordinators.direct_coordinator import (
        DirectCoordinator,
    )
    c = DirectCoordinator.__new__(DirectCoordinator)  # bypass HA base __init__
    t1 = DeviceTarget(id="a", uri="a", protocol="voltronic", name="A")
    t2 = DeviceTarget(id="b", uri="b", protocol="pi18", name="B")

    c.set_targets([t1, t2])
    assert c._targets == [t1, t2] and c.devices == [t1, t2]

    c.set_targets([t2])  # replaces, not appends
    assert c._targets == [t2] and c.devices == [t2]


def test_coordinator_poll_schedule_invariants():
    # Locks the split-cadence config that keeps live data fresh while small
    # per-cycle (so a cycling dongle's window is enough) — see _CMD_SCHEDULE.
    from custom_components.dess_monitor_local.coordinators.direct_coordinator import (
        DirectCoordinator,
    )
    sched = {cmd: n for cmd, _s, n in DirectCoordinator._CMD_SCHEDULE}
    assert sched["QPIGS"] == 1                 # live telemetry every cycle
    assert sched["QPIRI"] >= sched["QPIWS"] > sched["QPIGS"]  # static rarer than faults rarer than live
    # Distinct section per command (no clobbering on carry-forward).
    sections = [s for _c, s, _n in DirectCoordinator._CMD_SCHEDULE]
    assert len(sections) == len(set(sections))
    # Tolerate more transient misses before unavailable (cycling dongles).
    assert DirectCoordinator._MAX_CONSECUTIVE_FAILURES >= 5


def test_child_failure_summary_reads_nested_counts():
    # Guards the debug-panel cycle event against the _counts shape:
    # FailureTracker._counts is {device: {command: fails}} (nested), NOT
    # {(device, command): fails}.
    from custom_components.dess_monitor_local.coordinators.direct_coordinator import (
        DirectCoordinator,
    )
    from custom_components.dess_monitor_local.coordinators.failure_tracker import (
        FailureTracker,
    )
    c = DirectCoordinator.__new__(DirectCoordinator)
    c._failures = FailureTracker(6)
    c.devices = [
        DeviceTarget(id="eybond:PNA:1", uri="u", protocol="voltronic", name="A"),
        DeviceTarget(id="eybond:PNB:1", uri="u", protocol="voltronic", name="B"),
    ]
    c._failures.on_failure("eybond:PNA:1", "QPIGS")
    c._failures.on_failure("eybond:PNA:1", "QPIGS")
    c._failures.on_success("eybond:PNB:1", "QPIGS")

    summary = c._child_failure_summary()
    assert summary["eybond:PNA:1"] == "fail:2"
    assert summary["eybond:PNB:1"] == "ok"
