"""Hub-level diagnostic sensor for an EyBond hub entry.

Gives the user immediate proof the integration is working: a single
``Discovered dongles`` sensor on a hub device, whose state is the number of
dongles seen and whose attributes list each one (PN, status, last_seen,
enabled, protocol). It also fires a one-shot persistent notification when a
brand-new, still-unconfigured dongle appears, nudging the user to open the
hub options and assign a protocol.

The sensor reads the live discovery registry from the hub runtime on each
coordinator tick (the hub coordinator ticks every update interval even with
no enabled children), so no extra polling machinery is needed.
"""
from __future__ import annotations

import logging

from homeassistant.components import persistent_notification
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..api.protocols.eybond_discovery import DongleStatus
from ..const import CONF_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)


class EybondHubDiscoverySensor(CoordinatorEntity, SensorEntity):
    """Number of dongles discovered on the hub, with a per-dongle list."""

    _attr_has_entity_name = True
    _attr_name = "Discovered dongles"
    _attr_icon = "mdi:lan-connect"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "dongles"

    def __init__(self, hass: HomeAssistant, coordinator, config_entry) -> None:
        super().__init__(coordinator)
        self._hass = hass
        self._entry_id = config_entry.entry_id
        self._hub_name = config_entry.data.get(CONF_NAME) or "EyBond Hub"
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

    def _registry(self):
        # Lazy import avoids a config_flow/__init__ import cycle.
        from ..eybond_hub import get_hub_runtime

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

    def _handle_coordinator_update(self) -> None:
        self._maybe_notify_new()
        super()._handle_coordinator_update()

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
