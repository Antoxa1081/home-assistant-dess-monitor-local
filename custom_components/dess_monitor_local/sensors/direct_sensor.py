import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    EntityCategory,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.dess_monitor_local import DirectCoordinator
from custom_components.dess_monitor_local.api.commands.direct_commands import (
    ACInputVoltageRange,
    BatteryType,
    ChargerSourcePriority,
    DeviceStatusBitsB7B0,
    OperatingMode,
    OutputSourcePriority,
    ParallelMode,
    parse_device_status_bits_b7_b0,
)
from custom_components.dess_monitor_local.api.decoders.enums import (
    # PI18 direction/status enums come straight from their real home
    # rather than being re-exported through direct_commands (a re-export
    # there reads as an unused import and gets stripped by linters).
    PI18BatteryPowerDirection,
    PI18DCACPowerDirection,
    PI18LinePowerDirection,
    PI18MPPTStatus,
)
from custom_components.dess_monitor_local.api.model import WarningKey
from custom_components.dess_monitor_local.const import DOMAIN
from custom_components.dess_monitor_local.helpers.sanity import (
    is_plausible_battery_current,
    is_plausible_battery_voltage,
    is_plausible_power,
)
from custom_components.dess_monitor_local.hub import InverterDevice

_LOGGER = logging.getLogger(__name__)


class DirectSensorBase(CoordinatorEntity, SensorEntity):

    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._inverter_device = inverter_device

    @property
    def device_info(self) -> DeviceInfo:
        """Information about this entity/device."""
        return {
            "identifiers": {(DOMAIN, self._inverter_device.inverter_id)},
            "name": self._inverter_device.name,
            "sw_version": self._inverter_device.firmware_version,
            "model": self._inverter_device.inverter_id,
            "manufacturer": 'ESS'
        }

    @property
    def available(self) -> bool:
        """Return True if the coordinator has fetched data for this inverter.

        Without this check the ``data`` property below dereferences
        ``coordinator.data[id]`` on every state update; if the very first
        poll fails (or HA restarts and we haven't completed a poll yet),
        that raises TypeError/KeyError and corrupts the entity update
        fan-out for every sibling entity on the same coordinator.
        """
        return (
            self.coordinator.data is not None
            and self._inverter_device.inverter_id in self.coordinator.data
        )

    @property
    def data(self):
        return self.coordinator.data[self._inverter_device.inverter_id]

    @property
    def snapshot(self):
        """The protocol-neutral DeviceSnapshot for this inverter, or None.

        Domain-model migration (Phase C): sensors prefer the typed snapshot
        field over the legacy section dict where a mapping exists.
        """
        snaps = getattr(self.coordinator, "snapshots", None) or {}
        return snaps.get(self._inverter_device.inverter_id)

    def _metric(self, section: str, key: str):
        """Resolve a numeric metric value, preferring the typed snapshot.

        Domain-model migration (Phase C group 5): the *derived* sensors
        (energy integrators, vSoC, time-to-*) read their physical inputs —
        battery voltage & currents, PV / output power, charge setpoints —
        through this resolver instead of parsing the legacy section dict
        directly. When a snapshot exists for the device the typed accessor
        from ``_SNAPSHOT_FIELD`` wins (already ``float | None``); that is
        what lets SMG-II / PI18 drop their fabricated legacy ``qpigs`` /
        ``qpiri`` in Phase D. The caller's sanity gates (plausibility,
        all-zeros, NaN) still apply to the resolved value. With no snapshot
        it parses the legacy section float, so pre-migration behavior is
        byte-identical. Returns ``None`` when absent or unparseable.
        """
        accessor = _SNAPSHOT_FIELD.get((section, key))
        snap = self.snapshot if accessor is not None else None
        if snap is not None:
            return accessor(snap)
        raw = self.data.get(section, {}).get(key)
        if raw is None:
            return None
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None


# Migration map (Phase C): legacy (section, key) → accessor on the snapshot.
# Where an entry exists AND a snapshot is available, the typed snapshot value
# is used (so e.g. SMG-II's previously-fabricated bus_voltage/battery_soc are
# now None → the sensor goes unavailable instead of showing a fake constant).
# Unmapped keys (and the no-snapshot path) fall back to the legacy section.
_SNAPSHOT_FIELD = {
    ("qpigs", "grid_voltage"): lambda s: s.metrics.grid_voltage,
    ("qpigs", "grid_frequency"): lambda s: s.metrics.grid_frequency,
    ("qpigs", "ac_output_voltage"): lambda s: s.metrics.ac_output_voltage,
    ("qpigs", "ac_output_frequency"): lambda s: s.metrics.ac_output_frequency,
    ("qpigs", "output_active_power"): lambda s: s.metrics.ac_output_active_power,
    ("qpigs", "output_apparent_power"): lambda s: s.metrics.ac_output_apparent_power,
    ("qpigs", "load_percent"): lambda s: s.metrics.load_percent,
    ("qpigs", "bus_voltage"): lambda s: s.metrics.bus_voltage,
    ("qpigs", "battery_voltage"): lambda s: s.metrics.battery_voltage,
    ("qpigs", "battery_charging_current"): lambda s: s.metrics.battery_charge_current,
    ("qpigs", "battery_discharge_current"): lambda s: s.metrics.battery_discharge_current,
    ("qpigs", "battery_capacity"): lambda s: s.metrics.battery_soc,
    ("qpigs", "inverter_heat_sink_temperature"): lambda s: s.metrics.temp_heatsink,
    ("qpigs", "inverter_dcdc_module_temperature"): lambda s: s.metrics.temp_dcdc,
    ("qpigs", "pv_input_voltage"): lambda s: s.metrics.pv1.voltage,
    ("qpigs", "pv_input_current"): lambda s: s.metrics.pv1.current,
    ("qpigs", "pv_charging_power"): lambda s: s.metrics.pv1.power,
    ("qpigs", "scc_battery_voltage"): lambda s: s.metrics.scc_battery_voltage,
    ("qpigs", "grid_ac_in_power"): lambda s: s.metrics.grid_power,
    # PI18 extras (Phase C group 5b): second MPPT / PV2, folded into qpigs.
    # pv2 is ``PvInput | None`` (None when the device has no second input),
    # so the accessors null-guard before dereferencing.
    ("qpigs", "pv2_input_voltage"):
        lambda s: s.metrics.pv2.voltage if s.metrics.pv2 else None,
    ("qpigs", "pv2_input_current"):
        lambda s: s.metrics.pv2.current if s.metrics.pv2 else None,
    ("qpigs", "pv2_input_power"):
        lambda s: s.metrics.pv2.power if s.metrics.pv2 else None,
    ("qpigs", "scc2_battery_voltage"): lambda s: s.metrics.scc2_battery_voltage,
    ("qpigs", "mppt1_temperature"): lambda s: s.metrics.temp_mppt1,
    ("qpigs", "mppt2_temperature"): lambda s: s.metrics.temp_mppt2,
    # qpiri — device ratings / nameplate (Phase C group 2)
    ("qpiri", "rated_grid_voltage"): lambda s: s.ratings.grid_voltage,
    ("qpiri", "rated_input_current"): lambda s: s.ratings.input_current,
    ("qpiri", "rated_ac_output_voltage"): lambda s: s.ratings.ac_output_voltage,
    ("qpiri", "rated_output_frequency"): lambda s: s.ratings.output_frequency,
    ("qpiri", "rated_output_current"): lambda s: s.ratings.output_current,
    ("qpiri", "rated_output_apparent_power"): lambda s: s.ratings.output_apparent_power,
    ("qpiri", "rated_output_active_power"): lambda s: s.ratings.output_active_power,
    ("qpiri", "rated_battery_voltage"): lambda s: s.ratings.battery_voltage,
    ("qpiri", "low_battery_to_ac_bypass_voltage"):
        lambda s: s.ratings.low_battery_to_bypass_voltage,
    ("qpiri", "shut_down_battery_voltage"): lambda s: s.ratings.shutdown_battery_voltage,
    ("qpiri", "bulk_charging_voltage"): lambda s: s.ratings.bulk_charging_voltage,
    ("qpiri", "float_charging_voltage"): lambda s: s.ratings.float_charging_voltage,
    ("qpiri", "max_utility_charging_current"):
        lambda s: s.ratings.max_utility_charging_current,
    ("qpiri", "max_charging_current"): lambda s: s.ratings.max_charging_current,
    ("qpiri", "high_battery_voltage_to_battery_mode"):
        lambda s: s.ratings.high_battery_to_battery_mode_voltage,
    ("qpiri", "rated_battery_capacity"): lambda s: s.ratings.battery_capacity_ah,
}

# Enum-valued sensors (Phase C group 2): (section, key) → snapshot enum field.
# The sensor stores the enum's ``.name``; None → no value.
_SNAPSHOT_ENUM_FIELD = {
    ("qpiri", "battery_type"): lambda s: s.ratings.battery_type,
    ("qpiri", "ac_input_voltage_range"): lambda s: s.ratings.ac_input_voltage_range,
    ("qpiri", "output_source_priority"): lambda s: s.ratings.output_source_priority,
    ("qpiri", "charger_source_priority"): lambda s: s.ratings.charger_source_priority,
    ("qpiri", "parallel_mode"): lambda s: s.ratings.parallel_mode,
    # PI18 direction / sub-status enums (Phase C group 5b), folded into qpigs.
    ("qpigs", "mppt1_status"): lambda s: s.metrics.mppt1_status,
    ("qpigs", "mppt2_status"): lambda s: s.metrics.mppt2_status,
    ("qpigs", "battery_power_direction"): lambda s: s.metrics.battery_power_direction,
    ("qpigs", "dcac_power_direction"): lambda s: s.metrics.dcac_power_direction,
    ("qpigs", "line_power_direction"): lambda s: s.metrics.line_power_direction,
}


class DirectTypedSensorBase(DirectSensorBase):
    """Абстрактный базовый класс для сенсоров, получающих значение по ключу."""

    def __init__(
            self,
            inverter_device: InverterDevice,
            coordinator: DirectCoordinator,
            data_section: str,
            data_key: str,
            sensor_suffix: str = "",
            name_suffix: str = ""
    ):
        super().__init__(inverter_device, coordinator)
        self.data_section = data_section
        self.data_key = data_key

        suffix = sensor_suffix or data_key
        name_part = name_suffix or data_key.replace('_', ' ').title()

        self._attr_unique_id = f"{self._inverter_device.inverter_id}_direct_{suffix}"
        self._attr_name = f"{self._inverter_device.name} Direct {name_part}"

    @callback
    def _handle_coordinator_update(self) -> None:
        accessor = _SNAPSHOT_FIELD.get((self.data_section, self.data_key))
        snapshot = self.snapshot if accessor is not None else None
        if accessor is not None and snapshot is not None:
            # Typed value straight from the domain model (already float | None).
            self._attr_native_value = accessor(snapshot)
        else:
            # Legacy path: parse the string section value.
            section = self.data.get(self.data_section, {})
            raw_value = section.get(self.data_key)
            if raw_value is not None:
                try:
                    self._attr_native_value = float(raw_value)
                except (ValueError, TypeError):
                    self._attr_native_value = None
            else:
                self._attr_native_value = None
        self.async_write_ha_state()


# All numeric base classes below get ``state_class = MEASUREMENT``.
# This enables HA's long-term statistics: a row is written every 5 minutes
# regardless of state-change frequency. Without it, sensors that pin at a
# steady value (battery full → power 0 W for hours; load idle → current 0 A;
# temperature steady) stop emitting ``state_changed`` events, so the History
# card / Apex / mini-graph render a frozen line at the moment of the last
# change. With MEASUREMENT, the graph extends to ``now`` even when the
# underlying value hasn't moved.


class DirectWattSensorBase(DirectTypedSensorBase):
    device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unit_of_measurement = UnitOfPower.WATT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _sensor_option_display_precision = 0


class DirectTemperatureSensorBase(DirectTypedSensorBase):
    device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 0
    _sensor_option_display_precision = 0


class DirectVoltageSensorBase(DirectTypedSensorBase):
    device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_suggested_display_precision = 1
    _sensor_option_display_precision = 1


class DirectCurrentSensorBase(DirectTypedSensorBase):
    """Базовый сенсор силы тока (A) для direct-протокола."""

    device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_suggested_display_precision = 0
    _sensor_option_display_precision = 0


class DirectApparentPowerSensorBase(DirectTypedSensorBase):
    device_class = SensorDeviceClass.APPARENT_POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unit_of_measurement = UnitOfApparentPower.VOLT_AMPERE
    _attr_native_unit_of_measurement = UnitOfApparentPower.VOLT_AMPERE
    _attr_suggested_display_precision = 0
    _sensor_option_display_precision = 0


class DirectBatteryCapacitySensorBase(DirectTypedSensorBase):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unit_of_measurement = "Ah"
    _attr_native_unit_of_measurement = "Ah"
    _attr_suggested_display_precision = 0
    _sensor_option_display_precision = 0


class DirectFrequencySensorBase(DirectTypedSensorBase):
    device_class = SensorDeviceClass.FREQUENCY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unit_of_measurement = UnitOfFrequency.HERTZ
    _attr_native_unit_of_measurement = UnitOfFrequency.HERTZ
    _attr_suggested_display_precision = 1
    _sensor_option_display_precision = 1


class DirectEnumSensorBase(DirectTypedSensorBase):
    """Базовый класс для сенсоров с перечислимым значением (ENUM)."""

    enum_class = None  # Подкласс обязан переопределить
    device_class = SensorDeviceClass.ENUM
    _attr_device_class = SensorDeviceClass.ENUM

    @property
    def options(self) -> list[str]:
        return [e.name for e in self.enum_class] if self.enum_class else []

    @callback
    def _handle_coordinator_update(self) -> None:
        accessor = _SNAPSHOT_ENUM_FIELD.get((self.data_section, self.data_key))
        snapshot = self.snapshot if accessor is not None else None
        if accessor is not None and snapshot is not None:
            enum_val = accessor(snapshot)
            value = enum_val.name if enum_val is not None else None
        else:
            section = self.data.get(self.data_section, {})
            value = section.get(self.data_key)

        self._attr_native_value = value if value in self.options else None
        self.async_write_ha_state()


class BatteryTypeSensor(DirectEnumSensorBase):
    enum_class = BatteryType


class ACInputVoltageRangeSensor(DirectEnumSensorBase):
    enum_class = ACInputVoltageRange


class OutputSourcePrioritySensor(DirectEnumSensorBase):
    enum_class = OutputSourcePriority


class ChargerSourcePrioritySensor(DirectEnumSensorBase):
    enum_class = ChargerSourcePriority


class ParallelModeSensor(DirectEnumSensorBase):
    enum_class = ParallelMode


class DirectPVPowerSensor(DirectWattSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device,
            coordinator,
            data_section="qpigs",
            data_key="pv_charging_power",
            sensor_suffix="pv_power",
            name_suffix="PV Power"
        )

class DirectACGridInPowerSensor(DirectWattSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device,
            coordinator,
            data_section="qpigs",
            data_key="grid_ac_in_power",
            sensor_suffix="grid_ac_in_power",
            name_suffix="Grid AC In Power"
        )


class DirectPV2PowerSensor(DirectWattSensorBase):  # можно и от DirectSensorBase, если не нужен unit/class
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device,
            coordinator,
            data_section="unused",  # не используется, можно передать любой
            data_key="unused",
            sensor_suffix="pv2_power",
            name_suffix="PV2 Power"
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        try:
            qpigs2 = self.data["qpigs2"]
            self._attr_native_value = float(qpigs2["pv_current"]) * float(qpigs2["pv_voltage"])
        except (KeyError, ValueError, TypeError):
            self._attr_native_value = None

        self.async_write_ha_state()


class DirectPVVoltageSensor(DirectVoltageSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device,
            coordinator,
            data_section="qpigs",
            data_key="pv_input_voltage",
            sensor_suffix="pv_voltage",
            name_suffix="PV Voltage"
        )


class DirectPV2VoltageSensor(DirectVoltageSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device,
            coordinator,
            data_section="qpigs2",
            data_key="pv_voltage",
            sensor_suffix="pv2_voltage",
            name_suffix="PV2 Voltage"
        )


class DirectPV2CurrentSensor(DirectCurrentSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device,
            coordinator,
            data_section="qpigs2",
            data_key="pv_current",
            sensor_suffix="pv2_current",
            name_suffix="PV2 Current"
        )


class DirectBatteryVoltageSensor(DirectVoltageSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device,
            coordinator,
            data_section="qpigs",
            data_key="battery_voltage",
            sensor_suffix="battery",
            name_suffix="Battery Voltage"
        )


class DirectInverterOutputPowerSensor(DirectWattSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device,
            coordinator,
            data_section="qpigs",
            data_key="output_active_power",
            sensor_suffix="inverter_out_power",
            name_suffix="Inverter Out Power"
        )


class DirectInverterTemperatureSensor(DirectTemperatureSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device,
            coordinator,
            data_section="qpigs",
            data_key="inverter_heat_sink_temperature",
            sensor_suffix="inverter_temperature",
            name_suffix="Inverter Temperature"
        )

class DirectInverterDCModuleTemperatureSensor(DirectTemperatureSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device,
            coordinator,
            data_section="qpigs",
            data_key="inverter_dcdc_module_temperature",
            sensor_suffix="inverter_dc_dc_temperature",
            name_suffix="Inverter DC-DC Module Temperature"
        )


class DirectGridVoltageSensor(DirectVoltageSensorBase):
    def __init__(self, inverter_device, coordinator):
        super().__init__(inverter_device, coordinator, "qpigs", "grid_voltage", "grid_voltage", "Grid Voltage")


class DirectGridFrequencySensor(DirectTypedSensorBase):
    device_class = SensorDeviceClass.FREQUENCY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unit_of_measurement = "Hz"
    _attr_native_unit_of_measurement = "Hz"
    _attr_suggested_display_precision = 1
    _sensor_option_display_precision = 1

    def __init__(self, inverter_device, coordinator):
        super().__init__(inverter_device, coordinator, "qpigs", "grid_frequency", "grid_freq", "Grid Frequency")


class DirectACOutputVoltageSensor(DirectVoltageSensorBase):
    def __init__(self, inverter_device, coordinator):
        super().__init__(inverter_device, coordinator, "qpigs", "ac_output_voltage", "ac_output_voltage",
                         "AC Output Voltage")


class DirectACOutputFrequencySensor(DirectTypedSensorBase):
    device_class = SensorDeviceClass.FREQUENCY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unit_of_measurement = "Hz"
    _attr_native_unit_of_measurement = "Hz"
    _attr_suggested_display_precision = 1
    _sensor_option_display_precision = 1

    def __init__(self, inverter_device, coordinator):
        super().__init__(inverter_device, coordinator, "qpigs", "ac_output_frequency", "ac_output_freq",
                         "AC Output Frequency")


class DirectOutputApparentPowerSensor(DirectWattSensorBase):
    def __init__(self, inverter_device, coordinator):
        super().__init__(inverter_device, coordinator, "qpigs", "output_apparent_power", "output_apparent_power",
                         "Apparent Power")


class DirectLoadPercentSensor(DirectTypedSensorBase):
    device_class = SensorDeviceClass.POWER_FACTOR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unit_of_measurement = "%"
    _attr_native_unit_of_measurement = "%"
    _attr_suggested_display_precision = 0
    _sensor_option_display_precision = 0

    def __init__(self, inverter_device, coordinator):
        super().__init__(inverter_device, coordinator, "qpigs", "load_percent", "load_percent", "Load Percent")


class DirectBusVoltageSensor(DirectVoltageSensorBase):
    def __init__(self, inverter_device, coordinator):
        super().__init__(inverter_device, coordinator, "qpigs", "bus_voltage", "bus_voltage", "Bus Voltage")


class DirectBatteryChargingCurrentSensor(DirectCurrentSensorBase):
    def __init__(self, inverter_device, coordinator):
        super().__init__(inverter_device, coordinator, "qpigs", "battery_charging_current", "battery_charging_current",
                         "Battery Charging Current")


class DirectBatteryDischargeCurrentSensor(DirectCurrentSensorBase):
    def __init__(self, inverter_device, coordinator):
        super().__init__(inverter_device, coordinator, "qpigs", "battery_discharge_current",
                         "battery_discharge_current", "Battery Discharge Current")

class DirectBatteryPowerSensor(DirectWattSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device,
            coordinator,
            data_section="qpigs",
            data_key="_battery_power",
            sensor_suffix="battery_power",
            name_suffix="Battery Power"
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        # Domain-model migration (Phase C): the signed battery power is built
        # from the typed snapshot's charge / discharge magnitudes & voltage
        # (or the legacy qpigs floats when no snapshot exists). Any missing
        # input → unavailable, matching the old empty-qpigs guard.
        battery_charging_current = self._metric('qpigs', 'battery_charging_current')
        battery_discharge_current = self._metric('qpigs', 'battery_discharge_current')
        battery_voltage = self._metric('qpigs', 'battery_voltage')
        if (
            battery_charging_current is None
            or battery_discharge_current is None
            or battery_voltage is None
        ):
            self._attr_native_value = None
            self.async_write_ha_state()
            return

        # All-zeros == "no data" (bridge offline, empty payload, missing keys
        # falling back to default 0). Silently skip — not a parser anomaly.
        if (
            battery_charging_current == 0.0
            and battery_discharge_current == 0.0
            and battery_voltage == 0.0
        ):
            self._attr_native_value = None
            self.async_write_ha_state()
            return

        if (
            not is_plausible_battery_current(battery_charging_current)
            or not is_plausible_battery_current(battery_discharge_current)
            or not is_plausible_battery_voltage(battery_voltage)
        ):
            _LOGGER.debug(
                "%s: implausible reading "
                "(I_chg=%.2f A, I_dis=%.2f A, V=%.2f V); dropping sample",
                self.entity_id or self._attr_unique_id,
                battery_charging_current,
                battery_discharge_current,
                battery_voltage,
            )
            self._attr_native_value = None
            self.async_write_ha_state()
            return

        raw_value = (battery_charging_current - battery_discharge_current) * battery_voltage

        if is_plausible_power(raw_value):
            self._attr_native_value = float(raw_value)
        else:
            self._attr_native_value = None

        self.async_write_ha_state()


class DirectBatteryCapacitySensor(DirectTypedSensorBase):
    # Inverter-reported SoC% (BMS-sourced on Li-CAN setups, internal
    # estimate on lead-acid). Treat as a continuous measurement so the
    # History card extends the line when the battery is pegged at 100/0.
    device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unit_of_measurement = "%"
    _attr_native_unit_of_measurement = "%"
    _attr_suggested_display_precision = 0
    _sensor_option_display_precision = 0

    def __init__(self, inverter_device, coordinator):
        super().__init__(inverter_device, coordinator, "qpigs", "battery_capacity", "battery_capacity",
                         "Battery Capacity")


class DirectPVInputCurrentSensor(DirectCurrentSensorBase):
    def __init__(self, inverter_device, coordinator):
        super().__init__(inverter_device, coordinator, "qpigs", "pv_input_current", "pv_input_current",
                         "PV Input Current")


class DirectSCCBatteryVoltageSensor(DirectVoltageSensorBase):
    def __init__(self, inverter_device, coordinator):
        super().__init__(inverter_device, coordinator, "qpigs", "scc_battery_voltage", "scc_batt_voltage",
                         "SCC Battery Voltage")


class DirectDiagnosticSensorBase(DirectTypedSensorBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC


QPIRI_SENSOR_MAPPING = {
    "rated_grid_voltage": (DirectVoltageSensorBase, "Rated Grid Voltage"),
    "rated_input_current": (DirectCurrentSensorBase, "Rated Input Current"),
    "rated_ac_output_voltage": (DirectVoltageSensorBase, "Rated AC Output Voltage"),
    "rated_output_frequency": (DirectFrequencySensorBase, "Rated Output Frequency"),
    "rated_output_current": (DirectCurrentSensorBase, "Rated Output Current"),
    "rated_output_apparent_power": (DirectApparentPowerSensorBase, "Rated Output Apparent Power"),
    "rated_output_active_power": (DirectWattSensorBase, "Rated Output Active Power"),
    "rated_battery_voltage": (DirectVoltageSensorBase, "Rated Battery Voltage"),
    "low_battery_to_ac_bypass_voltage": (DirectVoltageSensorBase, "Low Battery to AC Bypass Voltage"),
    "shut_down_battery_voltage": (DirectVoltageSensorBase, "Shut Down Battery Voltage"),
    "bulk_charging_voltage": (DirectVoltageSensorBase, "Bulk Charging Voltage"),
    "float_charging_voltage": (DirectVoltageSensorBase, "Float Charging Voltage"),
    "battery_type": (BatteryTypeSensor, "Battery Type"),
    "max_utility_charging_current": (DirectCurrentSensorBase, "Max Utility Charging Current"),
    "max_charging_current": (DirectCurrentSensorBase, "Max Charging Current"),
    "ac_input_voltage_range": (ACInputVoltageRangeSensor, "AC Input Voltage Range"),
    "output_source_priority": (OutputSourcePrioritySensor, "Output Source Priority"),
    "charger_source_priority": (ChargerSourcePrioritySensor, "Charger Source Priority"),
    "parallel_max_number": (DirectDiagnosticSensorBase, "Parallel Max Number"),
    "reserved_uu": (DirectDiagnosticSensorBase, "Reserved UU"),
    "reserved_v": (DirectDiagnosticSensorBase, "Reserved V"),
    "parallel_mode": (ParallelModeSensor, "Parallel Mode"),
    "high_battery_voltage_to_battery_mode": (DirectVoltageSensorBase, "High Battery Voltage to Battery Mode"),
    "solar_work_condition_in_parallel": (DirectDiagnosticSensorBase, "Solar Work Condition In Parallel"),
    "solar_max_charging_power_auto_adjust": (DirectDiagnosticSensorBase, "Solar Max Charging Power Auto Adjust"),
    "rated_battery_capacity": (DirectBatteryCapacitySensorBase, "Rated Battery Capacity"),
    "reserved_b": (DirectDiagnosticSensorBase, "Reserved B"),
    "reserved_ccc": (DirectDiagnosticSensorBase, "Reserved CCC")
}


def generate_qpiri_sensors(inverter_device, coordinator):
    return [
        sensor_class(
            inverter_device=inverter_device,
            coordinator=coordinator,
            data_section="qpiri",
            data_key=data_key,
            name_suffix=name_suffix,
        )
        for data_key, (sensor_class, name_suffix) in QPIRI_SENSOR_MAPPING.items()
    ]


class DirectDeviceStatusSensor(DirectSensorBase):
    """Главный сенсор с битами как атрибутами."""
    _attr_name = "Device Status"
    _attr_icon = "mdi:information-outline"

    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        """Initialize the sensor."""
        super().__init__(inverter_device, coordinator)
        self._attr_unique_id = f"{self._inverter_device.inverter_id}_direct_device_status"
        self._attr_name = f"{self._inverter_device.name} Direct Device Status"
        # self._inverter_device = inverter_device

    @callback
    def _handle_coordinator_update(self) -> None:
        # ``.get`` (not ``[]``) so a protocol without PI30 status bits — e.g.
        # SMG-II once its fabricated qpigs is removed in Phase D — degrades to
        # "OK"/empty instead of raising KeyError across the update fan-out.
        qpigs = self.data.get("qpigs", {})
        flags = int(qpigs.get("device_status_bits_b7_b0", 0))
        if flags & DeviceStatusBitsB7B0.FAULT:
            self._attr_native_value = 'FAULT'
        elif flags & DeviceStatusBitsB7B0.LINE_FAIL:
            self._attr_native_value = 'LINE_FAIL'
        elif flags & DeviceStatusBitsB7B0.INVERTER_OVERLOAD:
            self._attr_native_value = 'INVERTER_OVERLOAD'
        elif flags & DeviceStatusBitsB7B0.BATTERY_LOW:
            self._attr_native_value = 'BATTERY_LOW'
        self._attr_native_value = 'OK'
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        qpigs = self.data.get("qpigs", {})
        bits = qpigs.get("device_status_bits_b7_b0", 0)
        attrs = parse_device_status_bits_b7_b0(bits)
        return attrs


class DirectOperatingModeSensor(DirectEnumSensorBase):
    """Inverter operating mode from QMOD: PowerOn / Standby / Line /
    Battery / ShutdownApproaching / Fault.

    Useful as an automation trigger ("battery active → notify",
    "fault → alarm") without having to template-parse status bit strings.

    The decoder returns the field as an ``OperatingMode`` enum instance
    (or the literal string ``"Unknown"`` when the code didn't match any
    known mode). Coerce to ``.name`` so HA's ENUM-class validation
    accepts the value.
    """

    enum_class = OperatingMode

    def __init__(self, inverter_device, coordinator):
        super().__init__(
            inverter_device, coordinator,
            data_section="qmod", data_key="operating_mode",
            sensor_suffix="operating_mode", name_suffix="Operating Mode",
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        snapshot = self.snapshot
        if snapshot is not None:
            mode = snapshot.metrics.mode
            value = mode.name if mode is not None else None
        else:
            raw = self.data.get("qmod", {}).get("operating_mode")
            if hasattr(raw, "name"):
                value = raw.name
            elif isinstance(raw, str):
                value = raw
            else:
                value = None
        if value in self.options:
            self._attr_native_value = value
        else:
            self._attr_native_value = None
        self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Inverter Warning / Fault summary sensor — combines PI30 QPIWS and PI18 FWS
# into a single human-readable status text, with every individual flag
# preserved as an attribute for granular automations.
# ---------------------------------------------------------------------------


# Severity order for the summary text. Higher index = lower priority. The
# first set bit found in this list becomes the displayed state; the
# state shows "OK" when no bit is set.
#
# Each entry is a *base name* — the lookup tries both ``flags[name]``
# (PI30 QPIWS) and ``flags["warn_" + name]`` (PI18 QFWS + the agent's
# postgen flat snapshot), so all three transports share one severity
# table without duplicate entries.
_WARNING_SEVERITY_ORDER: tuple[tuple[str, str], ...] = (
    ("fault_active",                  "Fault Active"),                # agent only
    ("inverter_fault",                "Inverter Fault"),
    ("battery_under_shutdown",        "Battery Shutdown"),
    ("battery_open",                  "Battery Disconnected"),
    ("battery_short",                 "Battery Short Circuit"),
    ("battery_over_current",          "Battery Overcurrent"),         # agent
    ("self_test_fail",                "Self-test Fail"),
    ("inverter_over_current",         "Inverter Overcurrent"),
    ("inverter_negative_power",       "Inverter Negative Power"),     # agent
    ("bus_over",                      "Bus Overvoltage"),
    ("bus_under",                     "Bus Undervoltage"),
    ("bus_soft_fail",                 "Bus Soft-start Fail"),
    ("over_temperature",              "Over Temperature"),
    ("inverter_over_temperature",     "Inverter Over Temperature"),   # agent
    ("dcdc_over_temperature",         "DC-DC Over Temperature"),      # agent
    ("pv_over_temperature",           "PV Over Temperature"),         # agent
    ("eeprom_fault",                  "EEPROM Fault"),
    ("current_sensor_fail",           "Current Sensor Fail"),
    ("fan_locked",                    "Fan Locked"),
    ("overload",                      "Overload"),
    ("battery_voltage_high",          "Battery Overvoltage"),
    ("battery_low_alarm",             "Battery Low"),
    ("battery_too_low_to_charge",     "Battery Too Low to Charge"),
    ("battery_type_incompatible",     "Battery Type Mismatch"),       # agent
    ("inverter_voltage_too_high",     "Inverter Output Overvoltage"),
    ("inverter_voltage_too_low",      "Inverter Output Undervoltage"),
    ("op_dc_voltage_over",            "Output DC Overvoltage"),
    ("pv_voltage_high",               "PV Overvoltage"),
    ("pv_low_voltage",                "PV Voltage Too Low"),          # agent
    ("pv_over_current",               "PV Overcurrent"),              # agent
    ("mppt_overload_fault",           "MPPT Overload"),
    ("opv_short",                     "Output Short"),
    ("inverter_soft_fail",            "Inverter Soft-start Fail"),
    ("mains_low_frequency",           "Grid Low Frequency"),          # agent
    ("mains_over_frequency",          "Grid Over Frequency"),         # agent
    ("mains_waveform_abnormal",       "Grid Waveform Abnormal"),      # agent
    ("parallel_host_lost",            "Parallel Host Lost"),          # agent
    ("parallel_sync_abnormal",        "Parallel Sync Lost"),          # agent
    ("parallel_battery_diff",         "Parallel Battery Mismatch"),   # agent
    ("parallel_mode_inconsistent",    "Parallel Mode Mismatch"),      # agent
    ("parallel_version_incompatible", "Parallel Version Mismatch"),   # agent
    ("parallel_comm_interrupted",     "Parallel Comm Lost"),          # agent
    ("battery_eq_charging",           "Battery Equalize Charging"),   # agent (info)
    ("pv_energy_low",                 "PV Energy Low"),               # agent (info)
    ("power_limit",                   "Power Limiting"),
    ("mppt_overload_warning",         "MPPT Overload Warning"),
    ("line_fail",                     "Line Fail"),
    # Sensor-calibration warnings — diagnostic, lowest priority.
    ("battery_current_bias",          "Battery Current Bias"),        # agent
    ("inverter_current_bias",         "Inverter Current Bias"),       # agent
    ("output_current_bias",           "Output Current Bias"),         # agent
    ("pv_current_bias",               "PV Current Bias"),             # agent
)


def _flag_set(flags: dict, base_name: str) -> bool:
    """Test whether a warning flag is set under either of its two
    naming conventions: bare (PI30 QPIWS) or ``warn_``-prefixed (PI18
    QFWS + agent postgen). Used by the severity walk so a single
    base-name table works across all transports."""
    return bool(flags.get(base_name)) or bool(flags.get(f"warn_{base_name}"))


class DirectInverterFaultSummarySensor(DirectSensorBase):
    """Single-glance "what's wrong with the inverter" sensor.

    State machine: walks the warning bits in severity order, displays
    the worst active one as text. ``"OK"`` when nothing is flagged.
    Surfaces *every* flag (set or clear) plus the active count as
    state attributes for granular template / automation use.

    Works for both PI30 (via QPIWS dict) and PI18 (via QFWS dict with
    its richer ``fault_code`` / ``fault_description`` semantics). If the
    inverter doesn't report a section, the sensor falls back to the
    other. Effectively unavailable only when *both* sections are empty.
    """

    _attr_icon = "mdi:alert-circle-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, inverter_device, coordinator):
        super().__init__(inverter_device, coordinator)
        self._attr_unique_id = (
            f"{inverter_device.inverter_id}_direct_fault_summary"
        )
        self._attr_name = (
            f"{inverter_device.name} Direct Inverter Fault Summary"
        )

    def _merged_warnings(self) -> dict:
        """Pick the populated warning section (PI30 ``qpiws`` or PI18
        ``qfws``) — they share enough field names that downstream
        consumers can treat the result as a single namespace."""
        qpiws = self.data.get("qpiws", {}) or {}
        qfws = self.data.get("qfws", {}) or {}
        # Prefer whichever is populated; if both, merge with PI18 fields
        # only adding non-overlapping warn_* keys.
        merged = dict(qpiws)
        for k, v in qfws.items():
            merged.setdefault(k, v)
        return merged

    @callback
    def _handle_coordinator_update(self) -> None:
        # Domain-model migration (Phase C group 3): the typed snapshot's
        # Faults bucket is the canonical source — it already canonicalises
        # PI18's variant warning spellings and unions the agent extras, so
        # the severity walk and the per-flag attributes are protocol-neutral.
        # Falls back to the legacy merged qpiws/qfws dict when no snapshot
        # exists (behaviour unchanged on that path).
        snapshot = self.snapshot
        if snapshot is not None:
            self._update_from_faults(snapshot.faults)
        else:
            self._update_from_legacy_flags(self._merged_warnings())

    def _update_from_faults(self, faults) -> None:
        """State + attributes from the typed Faults bucket."""
        if faults.fault_code:
            self._attr_native_value = (
                f"Fault: {faults.fault_description or faults.fault_code}"
            )
        elif faults.warning_code:
            self._attr_native_value = (
                f"Warning: SMG-II code 0x{int(faults.warning_code):08X}"
            )
        else:
            active = faults.warnings
            # First active warning in severity order (StrEnum compares equal
            # to its bare value, so the str base-name tests set membership).
            first_active = next(
                (
                    display
                    for base, display in _WARNING_SEVERITY_ORDER
                    if base in active
                ),
                None,
            )
            total = len(active)
            if total == 0:
                self._attr_native_value = "OK"
            elif first_active is None:
                self._attr_native_value = f"Warning: {total} active"
            elif total > 1:
                self._attr_native_value = (
                    f"Warning: {first_active} (+{total - 1} more)"
                )
            else:
                self._attr_native_value = f"Warning: {first_active}"

        self._attr_extra_state_attributes = _faults_attrs(faults)
        self.async_write_ha_state()

    def _update_from_legacy_flags(self, flags: dict) -> None:
        # PI18 / SMG-II carry an explicit fault_code — if non-zero, it
        # takes absolute priority over individual warning bits because
        # it represents an active hardware fault. SMG-II additionally
        # exposes warning_code as a separate DWORD; non-zero there is a
        # "warning state" rather than fault but still warrants summary.
        fault_code = flags.get("fault_code")
        fault_description = flags.get("fault_description")
        warning_code = flags.get("warning_code")
        if isinstance(fault_code, (int, float)) and fault_code != 0:
            self._attr_native_value = (
                f"Fault: {fault_description or fault_code}"
            )
            self._attr_extra_state_attributes = _flag_attrs(flags)
            self.async_write_ha_state()
            return
        if isinstance(warning_code, (int, float)) and warning_code != 0:
            # SMG-II direct-Modbus path: the per-bit decomposition isn't
            # public, so we show the raw hex. Users with agent access
            # get a richer breakdown via warn_* flags instead.
            self._attr_native_value = (
                f"Warning: SMG-II code 0x{int(warning_code):08X}"
            )
            self._attr_extra_state_attributes = _flag_attrs(flags)
            self.async_write_ha_state()
            return

        # Walk severity-ordered list; first set bit wins. The helper
        # transparently handles both naming conventions (bare vs warn_).
        first_active = None
        for key, display in _WARNING_SEVERITY_ORDER:
            if _flag_set(flags, key):
                first_active = display
                break

        # Count total unique active bits across both conventions to size
        # the "(+N more)" suffix. De-duplicate so an agent setting both
        # ``overload`` and ``warn_overload`` (theoretically) only counts
        # once.
        active_keys: set[str] = set()
        for key, _ in _WARNING_SEVERITY_ORDER:
            if _flag_set(flags, key):
                active_keys.add(key)
        # Plus any warn_* flags we haven't catalogued in the severity
        # table (unknown agent/firmware extensions) — surface them as
        # "+N more" so they're at least counted.
        cataloged_warn = {f"warn_{k}" for k, _ in _WARNING_SEVERITY_ORDER}
        for k, v in flags.items():
            if k.startswith("warn_") and v and k not in cataloged_warn:
                active_keys.add(k)
        total_active = len(active_keys)

        if total_active == 0:
            self._attr_native_value = "OK"
        elif first_active is None:
            # Only uncataloged warn_* bits set — name the count.
            self._attr_native_value = f"Warning: {total_active} active"
        elif total_active > 1:
            self._attr_native_value = (
                f"Warning: {first_active} (+{total_active - 1} more)"
            )
        else:
            self._attr_native_value = f"Warning: {first_active}"

        self._attr_extra_state_attributes = _flag_attrs(flags)
        self.async_write_ha_state()


def _flag_attrs(flags: dict) -> dict:
    """Produce the attribute dict for the fault summary sensor.

    Keeps booleans as booleans (HA renders them as on/off in the UI),
    drops the internal ``_reserved_*`` bits, and adds a derived
    ``active_count`` for easy template use.
    """
    attrs: dict = {}
    active = 0
    for key, value in flags.items():
        if key.startswith("_reserved_"):
            continue
        if isinstance(value, bool):
            attrs[key] = value
            if value:
                active += 1
        else:
            # Non-boolean fields (fault_code, fault_description, has_fault)
            # — pass through verbatim.
            attrs[key] = value
    attrs["active_count"] = active
    return attrs


def _faults_attrs(faults) -> dict:
    """Attribute dict for the fault summary from the typed Faults bucket.

    Every canonical WarningKey is exposed as a boolean (HA renders these as
    on/off), so automations get a stable, protocol-neutral attribute set —
    for PI30 the names are identical to the legacy bare flags; PI18/agent
    now report the canonical names instead of their raw warn_* spellings.
    ``active_count`` plus the explicit fault/warning codes round it out.
    """
    attrs: dict = {key.value: (key in faults.warnings) for key in WarningKey}
    attrs["active_count"] = len(faults.warnings)
    if faults.fault_code:
        attrs["fault_code"] = faults.fault_code
    if faults.fault_description:
        attrs["fault_description"] = faults.fault_description
    if faults.warning_code:
        attrs["warning_code"] = faults.warning_code
    return attrs


DIRECT_SENSORS = [
    DirectPVPowerSensor,
    DirectPV2PowerSensor,
    DirectPVVoltageSensor,
    DirectPV2VoltageSensor,
    DirectPVInputCurrentSensor,
    DirectPV2CurrentSensor,
    DirectBatteryVoltageSensor,
    DirectBatteryChargingCurrentSensor,
    DirectBatteryDischargeCurrentSensor,
    DirectBatteryCapacitySensor,
    DirectInverterOutputPowerSensor,
    DirectInverterTemperatureSensor,
    DirectInverterDCModuleTemperatureSensor,
    DirectGridVoltageSensor,
    DirectGridFrequencySensor,
    DirectACGridInPowerSensor,
    DirectACOutputVoltageSensor,
    DirectACOutputFrequencySensor,
    DirectOutputApparentPowerSensor,
    DirectLoadPercentSensor,
    DirectBusVoltageSensor,
    DirectSCCBatteryVoltageSensor,
    DirectDeviceStatusSensor,
    DirectBatteryPowerSensor,
    DirectOperatingModeSensor,
    DirectInverterFaultSummarySensor,
]


# ---------------------------------------------------------------------------
# PI18-only sensors. Wired up *in addition* to DIRECT_SENSORS when the user
# selected PI18 in the config flow (registered conditionally in sensor.py).
# All read from the ``qpigs`` section because the PI18 decoder folds every
# GS field — including the PI18-specific extras — into a single dict.
# ---------------------------------------------------------------------------


class DirectPV2InputPowerSensor(DirectWattSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device, coordinator,
            data_section="qpigs", data_key="pv2_input_power",
            sensor_suffix="pv2_input_power", name_suffix="PV2 Input Power",
        )


class DirectPV2InputVoltageSensor(DirectVoltageSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device, coordinator,
            data_section="qpigs", data_key="pv2_input_voltage",
            sensor_suffix="pv2_input_voltage", name_suffix="PV2 Input Voltage",
        )


class DirectPV2InputCurrentSensor(DirectCurrentSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device, coordinator,
            data_section="qpigs", data_key="pv2_input_current",
            sensor_suffix="pv2_input_current", name_suffix="PV2 Input Current",
        )


class DirectMPPT1TemperatureSensor(DirectTemperatureSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device, coordinator,
            data_section="qpigs", data_key="mppt1_temperature",
            sensor_suffix="mppt1_temperature", name_suffix="MPPT1 Temperature",
        )


class DirectMPPT2TemperatureSensor(DirectTemperatureSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device, coordinator,
            data_section="qpigs", data_key="mppt2_temperature",
            sensor_suffix="mppt2_temperature", name_suffix="MPPT2 Temperature",
        )


class DirectSCC2BatteryVoltageSensor(DirectVoltageSensorBase):
    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device, coordinator,
            data_section="qpigs", data_key="scc2_battery_voltage",
            sensor_suffix="scc2_battery_voltage", name_suffix="SCC2 Battery Voltage",
        )


class DirectMPPT1StatusSensor(DirectEnumSensorBase):
    enum_class = PI18MPPTStatus

    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device, coordinator,
            data_section="qpigs", data_key="mppt1_status",
            sensor_suffix="mppt1_status", name_suffix="MPPT1 Status",
        )


class DirectMPPT2StatusSensor(DirectEnumSensorBase):
    enum_class = PI18MPPTStatus

    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device, coordinator,
            data_section="qpigs", data_key="mppt2_status",
            sensor_suffix="mppt2_status", name_suffix="MPPT2 Status",
        )


class DirectBatteryPowerDirectionSensor(DirectEnumSensorBase):
    enum_class = PI18BatteryPowerDirection

    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device, coordinator,
            data_section="qpigs", data_key="battery_power_direction",
            sensor_suffix="battery_power_direction", name_suffix="Battery Power Direction",
        )


class DirectDCACPowerDirectionSensor(DirectEnumSensorBase):
    enum_class = PI18DCACPowerDirection

    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device, coordinator,
            data_section="qpigs", data_key="dcac_power_direction",
            sensor_suffix="dcac_power_direction", name_suffix="DC-AC Power Direction",
        )


class DirectLinePowerDirectionSensor(DirectEnumSensorBase):
    enum_class = PI18LinePowerDirection

    def __init__(self, inverter_device: InverterDevice, coordinator: DirectCoordinator):
        super().__init__(
            inverter_device, coordinator,
            data_section="qpigs", data_key="line_power_direction",
            sensor_suffix="line_power_direction", name_suffix="Line Power Direction",
        )


PI18_SENSORS = [
    DirectPV2InputPowerSensor,
    DirectPV2InputVoltageSensor,
    DirectPV2InputCurrentSensor,
    DirectMPPT1TemperatureSensor,
    DirectMPPT2TemperatureSensor,
    DirectSCC2BatteryVoltageSensor,
    DirectMPPT1StatusSensor,
    DirectMPPT2StatusSensor,
    DirectBatteryPowerDirectionSensor,
    DirectDCACPowerDirectionSensor,
    DirectLinePowerDirectionSensor,
]
