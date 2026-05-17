import logging

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfElectricPotential, UnitOfPower, UnitOfTemperature, EntityCategory, \
    UnitOfElectricCurrent, UnitOfFrequency, UnitOfApparentPower
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.dess_monitor_local import DirectCoordinator
from custom_components.dess_monitor_local.api.commands.direct_commands import ParallelMode, ChargerSourcePriority, \
    OutputSourcePriority, ACInputVoltageRange, BatteryType, DeviceStatusBitsB7B0, \
    PI18BatteryPowerDirection, PI18DCACPowerDirection, PI18LinePowerDirection, PI18MPPTStatus, \
    parse_device_status_bits_b7_b0
from custom_components.dess_monitor_local.const import DOMAIN
from custom_components.dess_monitor_local.hub import InverterDevice
from custom_components.dess_monitor_local.sanity import (
    is_plausible_battery_current,
    is_plausible_battery_voltage,
    is_plausible_power,
)

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
        section = self.data.get(self.data_section, {})
        raw_value = section.get(self.data_key)

        if raw_value in self.options:
            self._attr_native_value = raw_value
        else:
            self._attr_native_value = None

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
        qpigs = self.data.get('qpigs', {})
        qpiri = self.data.get('qpiri', {})
        if not qpigs:
            self._attr_native_value = None
            self.async_write_ha_state()
            return
        try:
            battery_charging_current = float(qpigs.get('battery_charging_current', 0))
            battery_discharge_current = float(qpigs.get('battery_discharge_current', 0))
            battery_voltage = float(qpigs.get('battery_voltage', 0))
        except (TypeError, ValueError):
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
        qpigs = self.data["qpigs"]
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
        qpigs = self.data["qpigs"]
        bits = qpigs.get("device_status_bits_b7_b0", 0)
        attrs = parse_device_status_bits_b7_b0(bits)
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
