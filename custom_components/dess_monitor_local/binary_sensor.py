"""Binary sensors derived from QPIGS device-status bit fields.

The inverter packs several status flags into two ASCII bit-strings in
QPIGS — ``device_status_bits_b7_b0`` (8 bits) and
``device_status_bits_b10_b8`` (3 bits). The existing
``DirectDeviceStatusSensor`` exposes a coarse single-state summary, but
automations need per-flag triggers ("fault → alarm", "battery_low →
notify", etc.). Each binary_sensor here maps to one specific bit so HA
automations can listen on a clean entity-id.

All entities live on the same coordinator as the rest of the
integration and update on every poll.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.dess_monitor_local import HubConfigEntry
from custom_components.dess_monitor_local.api.commands.direct_commands import (
    parse_device_status_bits_b10_b8,
    parse_device_status_bits_b7_b0,
)
from custom_components.dess_monitor_local.const import DOMAIN
from custom_components.dess_monitor_local.coordinators.direct_coordinator import (
    DirectCoordinator,
)
from custom_components.dess_monitor_local.hub import InverterDevice


# (status-key, sensor-suffix, human name, device_class). The parser
# normalises bits to True/False, so we just look them up by key.
_B7_B0_FLAGS: tuple[tuple[str, str, str, BinarySensorDeviceClass | None], ...] = (
    ("fault",              "fault",             "Fault",              BinarySensorDeviceClass.PROBLEM),
    ("line_fail",          "line_fail",         "Line Fail",          BinarySensorDeviceClass.PROBLEM),
    ("bus_over",           "bus_over",          "Bus Over",           BinarySensorDeviceClass.PROBLEM),
    ("battery_low",        "battery_low",       "Battery Low",        BinarySensorDeviceClass.BATTERY),
    ("battery_high",       "battery_high",      "Battery High",       BinarySensorDeviceClass.PROBLEM),
    ("inverter_overload",  "inverter_overload", "Inverter Overload",  BinarySensorDeviceClass.PROBLEM),
    ("inverter_on",        "inverter_on",       "Inverter On",        BinarySensorDeviceClass.RUNNING),
)

_B10_B8_FLAGS: tuple[tuple[str, str, str, BinarySensorDeviceClass | None], ...] = (
    ("charging_to_battery", "charging_to_battery", "Charging to Battery", BinarySensorDeviceClass.BATTERY_CHARGING),
    ("charging_ac_active",  "charging_ac_active",  "AC Charging Active",  BinarySensorDeviceClass.RUNNING),
    ("charging_scc_active", "charging_scc_active", "SCC Charging Active", BinarySensorDeviceClass.RUNNING),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: HubConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = config_entry.runtime_data
    coordinator = hub.direct_coordinator

    new_entities: list[BinarySensorEntity] = []
    for item in hub.items:
        for key, suffix, name, dc in _B7_B0_FLAGS:
            new_entities.append(
                _StatusBitBinarySensor(
                    item, coordinator,
                    raw_field="device_status_bits_b7_b0",
                    parser=parse_device_status_bits_b7_b0,
                    flag_key=key,
                    sensor_suffix=suffix,
                    name=name,
                    device_class=dc,
                )
            )
        for key, suffix, name, dc in _B10_B8_FLAGS:
            new_entities.append(
                _StatusBitBinarySensor(
                    item, coordinator,
                    raw_field="device_status_bits_b10_b8",
                    parser=parse_device_status_bits_b10_b8,
                    flag_key=key,
                    sensor_suffix=suffix,
                    name=name,
                    device_class=dc,
                )
            )

        # Inverter warnings/faults (QPIWS for PI30, QFWS for PI18).
        new_entities.append(_AnyWarningBinarySensor(item, coordinator))
        for key, suffix, name, dc in _QPIWS_WARNINGS:
            new_entities.append(
                _WarningFlagBinarySensor(
                    item, coordinator,
                    flag_key=key, sensor_suffix=suffix,
                    name=name, device_class=dc,
                )
            )

    if new_entities:
        async_add_entities(new_entities)


# Subset of QPIWS / QFWS bits surfaced as dedicated binary_sensors —
# the actionable ones for typical residential automations. Everything
# else lives on the fault-summary sensor as attributes.
_QPIWS_WARNINGS: tuple[tuple[str, str, str, BinarySensorDeviceClass | None], ...] = (
    ("inverter_fault",         "inverter_fault",         "Inverter Fault",      BinarySensorDeviceClass.PROBLEM),
    ("overload",               "overload",               "Overload",            BinarySensorDeviceClass.PROBLEM),
    ("over_temperature",       "over_temperature",       "Over Temperature",    BinarySensorDeviceClass.PROBLEM),
    ("fan_locked",             "fan_locked",             "Fan Locked",          BinarySensorDeviceClass.PROBLEM),
    ("battery_under_shutdown", "battery_under_shutdown", "Battery Shutdown",    BinarySensorDeviceClass.PROBLEM),
    ("eeprom_fault",           "eeprom_fault",           "EEPROM Fault",        BinarySensorDeviceClass.PROBLEM),
)


class _StatusBitBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """One bit of the QPIGS status field, exposed as binary_sensor.

    The bit-string parser is invoked on each tick rather than caching
    because the same parser feeds the legacy ``DirectDeviceStatusSensor``
    diagnostic — keeping a single decode point avoids divergence if the
    parser semantics ever change.
    """

    def __init__(
        self,
        inverter_device: InverterDevice,
        coordinator: DirectCoordinator,
        *,
        raw_field: str,
        parser,
        flag_key: str,
        sensor_suffix: str,
        name: str,
        device_class: BinarySensorDeviceClass | None,
    ):
        super().__init__(coordinator)
        self._inverter_device = inverter_device
        self._raw_field = raw_field
        self._parser = parser
        self._flag_key = flag_key
        self._attr_unique_id = (
            f"{inverter_device.inverter_id}_direct_{sensor_suffix}"
        )
        self._attr_name = f"{inverter_device.name} Direct {name}"
        if device_class is not None:
            self._attr_device_class = device_class

    @property
    def device_info(self) -> DeviceInfo:
        return {
            "identifiers": {(DOMAIN, self._inverter_device.inverter_id)},
            "name": self._inverter_device.name,
            "sw_version": self._inverter_device.firmware_version,
            "model": self._inverter_device.inverter_id,
            "manufacturer": "ESS",
        }

    @property
    def available(self) -> bool:
        return (
            self.coordinator.data is not None
            and self._inverter_device.inverter_id in self.coordinator.data
        )

    @property
    def _qpigs(self) -> dict:
        try:
            return self.coordinator.data[self._inverter_device.inverter_id].get("qpigs", {})
        except (KeyError, TypeError):
            return {}

    @callback
    def _handle_coordinator_update(self) -> None:
        raw = self._qpigs.get(self._raw_field)
        if raw is None or raw == "":
            self._attr_is_on = None
        else:
            try:
                parsed = self._parser(raw)
                value = parsed.get(self._flag_key)
                self._attr_is_on = bool(value) if value is not None else None
            except Exception:
                self._attr_is_on = None
        self.async_write_ha_state()


class _WarningFlagBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """One bit from QPIWS (PI30) or QFWS (PI18). Reads from whichever
    section is populated — both are checked, the warning is ON if either
    reports it."""

    def __init__(
        self,
        inverter_device: InverterDevice,
        coordinator: DirectCoordinator,
        *,
        flag_key: str,
        sensor_suffix: str,
        name: str,
        device_class: BinarySensorDeviceClass | None,
    ):
        super().__init__(coordinator)
        self._inverter_device = inverter_device
        self._flag_key = flag_key
        self._attr_unique_id = (
            f"{inverter_device.inverter_id}_direct_warning_{sensor_suffix}"
        )
        self._attr_name = f"{inverter_device.name} Direct {name}"
        if device_class is not None:
            self._attr_device_class = device_class

    @property
    def device_info(self) -> DeviceInfo:
        return {
            "identifiers": {(DOMAIN, self._inverter_device.inverter_id)},
            "name": self._inverter_device.name,
            "sw_version": self._inverter_device.firmware_version,
            "model": self._inverter_device.inverter_id,
            "manufacturer": "ESS",
        }

    @property
    def available(self) -> bool:
        return (
            self.coordinator.data is not None
            and self._inverter_device.inverter_id in self.coordinator.data
        )

    @property
    def _flags(self) -> dict:
        try:
            dev = self.coordinator.data[self._inverter_device.inverter_id]
        except (KeyError, TypeError):
            return {}
        # PI30 lives in qpiws; PI18 in qfws. Merge so a single flag_key
        # lookup works for either protocol.
        merged = dict(dev.get("qpiws", {}) or {})
        for k, v in (dev.get("qfws", {}) or {}).items():
            merged.setdefault(k, v)
        return merged

    @callback
    def _handle_coordinator_update(self) -> None:
        v = self._flags.get(self._flag_key)
        self._attr_is_on = bool(v) if v is not None else None
        self.async_write_ha_state()


class _AnyWarningBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Aggregate: True iff *any* known QPIWS / QFWS bit is set. Useful
    as a single automation trigger for "the inverter has something to
    say". For the specific issue, read the fault summary sensor or one
    of the per-flag binary_sensors."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert"

    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(coordinator)
        self._inverter_device = inverter_device
        self._attr_unique_id = (
            f"{inverter_device.inverter_id}_direct_any_warning"
        )
        self._attr_name = f"{inverter_device.name} Direct Any Warning"

    @property
    def device_info(self) -> DeviceInfo:
        return {
            "identifiers": {(DOMAIN, self._inverter_device.inverter_id)},
            "name": self._inverter_device.name,
            "sw_version": self._inverter_device.firmware_version,
            "model": self._inverter_device.inverter_id,
            "manufacturer": "ESS",
        }

    @property
    def available(self) -> bool:
        return (
            self.coordinator.data is not None
            and self._inverter_device.inverter_id in self.coordinator.data
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        try:
            dev = self.coordinator.data[self._inverter_device.inverter_id]
        except (KeyError, TypeError):
            self._attr_is_on = None
            self.async_write_ha_state()
            return

        qpiws = dev.get("qpiws", {}) or {}
        qfws = dev.get("qfws", {}) or {}

        # PI18 explicit fault_code wins immediately.
        fault_code = qfws.get("fault_code")
        if isinstance(fault_code, (int, float)) and fault_code != 0:
            self._attr_is_on = True
            self.async_write_ha_state()
            return

        any_set = any(
            bool(v) for k, v in qpiws.items()
            if isinstance(v, bool) and not k.startswith("_reserved_")
        ) or any(
            bool(v) for k, v in qfws.items()
            if isinstance(v, bool) and k.startswith("warn_")
        )
        self._attr_is_on = any_set
        self.async_write_ha_state()
