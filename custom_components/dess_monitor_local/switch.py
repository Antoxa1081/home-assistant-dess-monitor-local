"""Switch entities for toggleable vSoC behaviours.

Currently exposes the ``vSoC Float Deadband`` master toggle. When ON,
the SoC integrator suppresses quantisation-noise current samples while
the battery sits in float (see ``FloatVoltageWindowNumber`` /
``FloatNoiseFloorNumber`` for the thresholds). When OFF, every reported
current sample is integrated verbatim — useful if your inverter reports
fractional, un-quantised current and the deadband would only hide real
micro-discharge.
"""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from custom_components.dess_monitor_local import HubConfigEntry
from custom_components.dess_monitor_local.const import DOMAIN
from custom_components.dess_monitor_local.hub import InverterDevice


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: HubConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = config_entry.runtime_data
    new_entities = []
    for item in hub.items:
        new_entities.append(FloatDeadbandSwitch(item, hass))
    if new_entities:
        async_add_entities(new_entities)


class FloatDeadbandSwitch(SwitchEntity, RestoreEntity):
    """Master enable for the SoC float-mode deadband.

    Defaults ON — preserves the behaviour shipped before the toggle
    existed. Restored across HA restarts. Filed under the device's
    Configuration section.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:sine-wave"

    def __init__(self, inverter_device: InverterDevice, hass: HomeAssistant):
        self._inverter_device = inverter_device
        self._hass = hass
        self._attr_unique_id = f"{inverter_device.inverter_id}_float_deadband"
        self._attr_name = f"{inverter_device.name} vSoC Float Deadband"
        self._attr_is_on = True  # default: deadband active
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, inverter_device.inverter_id)},
            name=inverter_device.name,
            manufacturer="ESS",
            model=inverter_device.inverter_id,
            sw_version=inverter_device.firmware_version,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None and state.state in ("on", "off"):
            self._attr_is_on = state.state == "on"

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
