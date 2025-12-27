from pydantic import BaseModel, field_validator

from custom_components.dess_monitor_local.types import InverterSensorData, InverterSettings, InverterRatedParams


class InverterBaseModel(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def cast_numeric_strings(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if not v or v.endswith("_"):
                return 0.0
            try:
                return float(v)
            except ValueError:
                return v
        return v


class QpigsParser(InverterBaseModel):
    # Grid
    grid_voltage: float
    grid_frequency: float

    # AC output
    ac_output_voltage: float
    ac_output_frequency: float
    output_apparent_power: float
    output_active_power: float
    load_percent: float

    # DC / battery
    bus_voltage: float
    battery_voltage: float
    battery_charging_current: float
    battery_discharge_current: float
    battery_capacity: float

    # Temperature
    inverter_heat_sink_temperature: float

    # PV
    pv_input_current: float
    pv_input_voltage: float
    pv_charging_power: float
    scc_battery_voltage: float

    # Status / misc
    device_status_bits_b7_b0: str
    device_status_bits_b10_b8: str
    battery_voltage_offset: float
    eeprom_version: str


class QpiriParser(InverterBaseModel):
    # Rated
    rated_grid_voltage: float
    rated_input_current: float
    rated_ac_output_voltage: float
    rated_output_frequency: float
    rated_output_current: float
    rated_output_apparent_power: float
    rated_output_active_power: float
    rated_battery_voltage: float

    # Battery thresholds
    low_battery_to_ac_bypass_voltage: float
    high_battery_voltage_to_battery_mode: float
    shut_down_battery_voltage: float
    bulk_charging_voltage: float
    float_charging_voltage: float

    # Charging
    max_utility_charging_current: float
    max_charging_current: float

    # Modes / enums (оставляем str, дальше можно заменить на Enum)
    battery_type: str
    ac_input_voltage_range: str
    output_source_priority: str
    charger_source_priority: str

    # Parallel / misc
    parallel_max_number: int
    parallel_mode: str

    # Reserved / flags
    reserved_uu: str
    reserved_v: str
    solar_work_condition_in_parallel: int
    solar_max_charging_power_auto_adjust: int



def sensors_from_qpigs(q: QpigsParser) -> InverterSensorData:
    return InverterSensorData(
        grid_voltage=q.grid_voltage,
        grid_frequency=q.grid_frequency,
        grid_power=0.0,

        battery_voltage=q.battery_voltage,
        battery_current=q.battery_charging_current - q.battery_discharge_current,
        battery_power=q.battery_voltage * (
                q.battery_charging_current - q.battery_discharge_current
        ),

        ac_output_voltage=q.ac_output_voltage,
        ac_output_frequency=q.ac_output_frequency,
        ac_output_power=q.output_active_power,
        ac_load_percent=q.load_percent,

        pv_input_current=q.pv_input_current,
        pv_input_voltage=q.pv_input_voltage,
        pv_input_power=q.pv_charging_power,

        inverter_bus_voltage=q.bus_voltage,
        inverter_temperature=q.inverter_heat_sink_temperature,
        inverter_dc_temperature=q.inverter_heat_sink_temperature,
    )


def settings_from_qpiri(q: QpiriParser) -> InverterSettings:
    return InverterSettings(
        low_battery_to_ac_bypass_voltage=q.low_battery_to_ac_bypass_voltage,
        high_battery_voltage_to_battery_mode=q.high_battery_voltage_to_battery_mode,
        shut_down_battery_voltage=q.shut_down_battery_voltage,

        bulk_charging_voltage=q.bulk_charging_voltage,
        float_charging_voltage=q.float_charging_voltage,

        max_utility_charging_current=q.max_utility_charging_current,
        max_charging_current=q.max_charging_current,

        battery_type=q.battery_type,
        ac_input_voltage_range=q.ac_input_voltage_range,
        output_source_priority=q.output_source_priority,
        charger_source_priority=q.charger_source_priority,
    )


def rated_from_qpiri(q: QpiriParser) -> InverterRatedParams:
    return InverterRatedParams(
        rated_grid_voltage=q.rated_grid_voltage,
        rated_input_current=q.rated_input_current,

        rated_ac_output_voltage=q.rated_ac_output_voltage,
        rated_output_frequency=q.rated_output_frequency,
        rated_output_current=q.rated_output_current,

        rated_output_apparent_power=q.rated_output_apparent_power,
        rated_output_active_power=q.rated_output_active_power,

        rated_battery_voltage=q.rated_battery_voltage,
    )
