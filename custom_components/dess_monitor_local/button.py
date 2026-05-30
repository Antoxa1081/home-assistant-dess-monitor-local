"""Button entities for one-shot inverter actions.

Currently exposes ``Exit Fault State`` for SMG-II / Modbus inverters —
writes 1 to register 426, which the firmware uses as an acknowledge /
reset trigger when the unit is latched in fault mode. Other protocols
(PI30 / PI18 / agent) don't have an equivalent direct register, so the
button is only registered when the entry uses ``modbus_*`` protocol.
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.dess_monitor_local import HubConfigEntry
from custom_components.dess_monitor_local.api.protocols.modbus_rtu import (
    parse_modbus_uri,
    write_modbus_single_register,
)
from custom_components.dess_monitor_local.const import (
    CONF_DEVICE,
    CONF_PROTOCOL,
    DOMAIN,
    PROTOCOL_MODBUS,
)
from custom_components.dess_monitor_local.hub import InverterDevice

_LOGGER = logging.getLogger(__name__)

# SMG-II Modbus register that resets a latched fault state when written.
# Documented in ESPHome's smg-ii project as "Exit fault state — write 1".
_EXIT_FAULT_REGISTER = 426


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: HubConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = config_entry.runtime_data
    entry_protocol = config_entry.options.get(CONF_PROTOCOL)
    entry_device_uri = config_entry.options.get(CONF_DEVICE)

    new_entities: list[ButtonEntity] = []
    for item in hub.items:
        # Per-item protocol (a hub may mix protocols); the exit-fault button
        # only applies to Modbus SMG-II. Fall back to entry-level values for
        # legacy single-device entries.
        protocol = getattr(item, "protocol", None) or entry_protocol
        if protocol != PROTOCOL_MODBUS:
            # Other protocols don't expose a single-register fault-reset;
            # don't pollute their entity list with a button that wouldn't work.
            continue
        device_uri = getattr(item, "device_data", None) or entry_device_uri
        if not device_uri:
            continue
        new_entities.append(SMG2ExitFaultButton(item, hass, device_uri))

    if new_entities:
        async_add_entities(new_entities)


class SMG2ExitFaultButton(ButtonEntity):
    """One-shot button that writes 1 to SMG-II register 426 to clear
    latched fault state. Surfaced as a Lovelace button or callable via
    ``button.press`` service from automations."""

    _attr_icon = "mdi:restart-alert"

    def __init__(
        self,
        inverter_device: InverterDevice,
        hass: HomeAssistant,
        device_uri: str,
    ):
        self._inverter_device = inverter_device
        self._hass = hass
        self._device_uri = device_uri
        self._attr_unique_id = (
            f"{inverter_device.inverter_id}_exit_fault_state"
        )
        self._attr_name = f"{inverter_device.name} Exit Fault State"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, inverter_device.inverter_id)},
            name=inverter_device.name,
            manufacturer="ESS",
            model=inverter_device.inverter_id,
            sw_version=inverter_device.firmware_version,
        )

    async def async_press(self) -> None:
        """Issue the Modbus write. Goes through the shared command queue
        so it can't interleave with the coordinator's polling reads —
        SMG-II's RS485 bus is half-duplex and overlapping transactions
        truncate each other's frames."""
        try:
            host, port = parse_modbus_uri(self._device_uri)
        except Exception as err:
            _LOGGER.warning("Cannot parse Modbus URI %r: %s", self._device_uri, err)
            return

        queue = self._hass.data.get("dess_monitor_local_queue")
        if queue is None:
            _LOGGER.warning("Command queue not available; skipping exit-fault")
            return

        async def _do_write() -> dict:
            return await write_modbus_single_register(
                host, port, _EXIT_FAULT_REGISTER, 1
            )

        result = await queue.enqueue(_do_write)
        if isinstance(result, dict) and result.get("error"):
            _LOGGER.warning(
                "Exit-fault write failed: %s", result["error"]
            )
        else:
            _LOGGER.info(
                "Exit-fault command sent to %s; inverter should clear fault state",
                self._device_uri,
            )
