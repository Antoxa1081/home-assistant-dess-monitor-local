"""Hub-level diagnostic sensor for an EyBond hub entry.

Gives the user immediate proof the integration is working: a single
``Discovered dongles`` sensor on a hub device, whose state is the number of
dongles seen and whose attributes list each one (PN, status, last_seen,
enabled, protocol). It also fires a one-shot persistent notification when a
brand-new, still-unconfigured dongle appears, nudging the user to open the
hub options and assign a protocol.

Discovery state lives in the hub's registry, not in ``coordinator.data`` —
and the hub coordinator runs with ``always_update=False``, so it would not
fire when no child data changes. The sensor therefore refreshes on its own
timer, reading the live registry from the hub runtime each tick.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components import persistent_notification
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from ..api.protocols.eybond_discovery import DongleStatus
from ..const import (
    CONF_NAME,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Refresh cadence floor — discovery is cheap, but don't hammer the loop.
_MIN_INTERVAL_S = 5


class EybondHubDiscoverySensor(SensorEntity):
    """Number of dongles discovered on the hub, with a per-dongle list."""

    _attr_has_entity_name = True
    _attr_name = "Discovered dongles"
    _attr_icon = "mdi:lan-connect"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "dongles"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        self._hass = hass
        self._entry_id = config_entry.entry_id
        self._hub_name = config_entry.data.get(CONF_NAME) or "EyBond Hub"
        interval = int(
            config_entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        )
        self._interval = timedelta(seconds=max(_MIN_INTERVAL_S, interval))
        self._attr_unique_id = f"eybond_hub:{self._entry_id}:discovered"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"eybond_hub:{self._entry_id}")},
            "name": self._hub_name,
            "manufacturer": "DESS Monitor Local",
            "model": "EyBond Hub",
        }
        # PNs already known at setup (from the persisted registry) — seeded in
        # async_added_to_hass so a restart doesn't re-notify existing dongles.
        self._known: set[str] = set()
        self._seeded = False
        self._unsub = None

    def _registry(self):
        # Lazy import avoids a config_flow/__init__ import cycle.
        from ..hub.eybond import get_hub_runtime

        runtime = get_hub_runtime(self._hass, self._entry_id)
        return runtime.registry if runtime is not None else None

    @property
    def native_value(self) -> int:
        registry = self._registry()
        return len(registry) if registry is not None else 0

    @property
    def extra_state_attributes(self) -> dict:
        registry = self._registry()
        if registry is None:
            return {"connected": 0, "dongles": []}
        records = registry.all()
        dongles = [
            {
                "pn": r.pn,
                "status": r.status.value,
                "enabled": r.enabled,
                "protocol": r.protocol or "none",
                "devaddr": r.devaddr,
                "name": r.name or r.pn,
                "last_seen": r.last_seen or "",
            }
            for r in records
        ]
        connected = sum(
            1 for r in records if r.status is DongleStatus.CONNECTED
        )
        return {"connected": connected, "dongles": dongles}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        registry = self._registry()
        if registry is not None:
            self._known = {r.pn for r in registry.all()}
        self._seeded = True
        # Self-driven refresh: discovery isn't part of coordinator.data.
        self._unsub = async_track_time_interval(
            self._hass, self._tick, self._interval
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _tick(self, now=None) -> None:
        self._maybe_notify_new()
        self.async_write_ha_state()

    def _maybe_notify_new(self) -> None:
        if not self._seeded:
            return
        registry = self._registry()
        if registry is None:
            return
        for rec in registry.all():
            if rec.pn in self._known:
                continue
            self._known.add(rec.pn)
            # Only nudge for dongles that still need configuring.
            if not rec.enabled:
                self._notify_new(rec.pn)

    def _notify_new(self, pn: str) -> None:
        _LOGGER.info(
            "EyBond hub '%s': new dongle PN=%s discovered (unconfigured)",
            self._hub_name, pn,
        )
        persistent_notification.async_create(
            self._hass,
            (
                f"New EyBond dongle **{pn}** connected to hub "
                f"**{self._hub_name}**.\n\n"
                "Open the hub options (Settings → Devices & Services → "
                "DESS Monitor Local → Configure → Manage discovered devices) "
                "to assign a protocol and enable polling."
            ),
            title="EyBond: new dongle discovered",
            notification_id=f"{DOMAIN}_eybond_new_{pn}",
        )
