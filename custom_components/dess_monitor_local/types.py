from dataclasses import dataclass


@dataclass
class InverterSensorData:
    grid_voltage: float
    grid_power: float
    grid_frequency: float
    battery_voltage: float
    battery_power: float
    battery_current: float
    ac_output_voltage: float
    ac_output_power: float
    ac_output_frequency: float
    ac_load_percent: float
    pv_input_current: float
    pv_input_voltage: float
    pv_input_power: float
    inverter_bus_voltage: float
    inverter_temperature: float
    inverter_dc_temperature: float


@dataclass
class InverterSettings:
    low_battery_to_ac_bypass_voltage: float
    high_battery_voltage_to_battery_mode: float
    shut_down_battery_voltage: float
    bulk_charging_voltage: float
    float_charging_voltage: float
    max_utility_charging_current: float
    max_charging_current: float
    # enums settings
    battery_type: str
    ac_input_voltage_range: str
    output_source_priority: str
    charger_source_priority: str


@dataclass
class InverterRatedParams:
    rated_grid_voltage: float
    rated_input_current: float
    rated_ac_output_voltage: float
    rated_output_frequency: float
    rated_output_current: float
    rated_output_apparent_power: float
    rated_output_active_power: float
    rated_battery_voltage: float


@dataclass
class InverterSnapshot:
    sensors: InverterSensorData
    settings: InverterSettings
    rated: InverterRatedParams
