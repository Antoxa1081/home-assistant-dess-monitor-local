"""Tests for the hub discovery diagnostic sensor (sensors/eybond_hub_sensor.py).

Imports Home Assistant (CoordinatorEntity / SensorEntity), so self-skips on
the pure CI matrix and runs in the hass job (and locally where HA exists).
The sensor reads a live registry and fires a one-shot notification for each
newly discovered, unconfigured dongle.
"""
from unittest.mock import patch

import pytest

pytest.importorskip("homeassistant")

from custom_components.dess_monitor_local.api.protocols.eybond_discovery import (  # noqa: E402
    EybondRegistry,
)
from custom_components.dess_monitor_local.sensors import (  # noqa: E402
    eybond_hub_sensor as mod,
)


class _Entry:
    entry_id = "hub1"
    data = {"name": "Garage Hub"}
    options = {}


def _make(registry):
    ent = mod.EybondHubDiscoverySensor(object(), _Entry())
    # Point the sensor at our registry instead of the hass runtime.
    ent._registry = lambda: registry
    ent.async_write_ha_state = lambda: None
    return ent


def test_value_and_attributes():
    reg = EybondRegistry()
    reg.record_seen("PN_A", "10.0.0.1:1")  # connected
    reg.set_enabled("PN_A", True)
    reg.set_protocol("PN_A", "voltronic")
    reg.record_seen("PN_B", "10.0.0.2:2")
    reg.mark_disconnected("PN_B")  # disconnected, unconfigured

    ent = _make(reg)
    assert ent.native_value == 2
    attrs = ent.extra_state_attributes
    assert attrs["connected"] == 1
    pns = {d["pn"]: d for d in attrs["dongles"]}
    assert pns["PN_A"]["enabled"] is True
    assert pns["PN_A"]["protocol"] == "voltronic"
    assert pns["PN_B"]["status"] == "disconnected"
    assert pns["PN_B"]["protocol"] == "none"


def test_notifies_only_for_new_unconfigured():
    reg = EybondRegistry()
    reg.record_seen("PN_OLD", "10.0.0.1:1")  # known at setup
    ent = _make(reg)
    # Seed known PNs (simulates async_added_to_hass).
    ent._known = {"PN_OLD"}
    ent._seeded = True

    with patch.object(mod.persistent_notification, "async_create") as create:
        # A brand-new unconfigured dongle appears.
        reg.record_seen("PN_NEW", "10.0.0.9:9")
        ent._tick()
        assert create.call_count == 1
        # Idempotent: a second tick with no new PNs doesn't re-notify.
        ent._tick()
        assert create.call_count == 1


def test_no_notify_for_enabled_new_device():
    reg = EybondRegistry()
    ent = _make(reg)
    ent._known = set()
    ent._seeded = True
    # A new PN that is already configured/enabled should not nudge.
    reg.record_seen("PN_CFG", "10.0.0.5:5")
    reg.set_enabled("PN_CFG", True)
    with patch.object(mod.persistent_notification, "async_create") as create:
        ent._tick()
        assert create.call_count == 0


def test_not_seeded_does_not_notify():
    reg = EybondRegistry()
    ent = _make(reg)
    reg.record_seen("PN_X", "10.0.0.7:7")
    # _seeded is False until async_added_to_hass runs.
    with patch.object(mod.persistent_notification, "async_create") as create:
        ent._tick()
        assert create.call_count == 0
