from typing import Literal

from pydantic import BaseModel, field_validator

from custom_components.dess_monitor_local.parsers.common_parser import BaseInverterParser
from custom_components.dess_monitor_local.types import InverterSensorData, InverterSettings, InverterRatedParams, \
    InverterSnapshot


class EasunSMG2ModbusSensors(BaseModel):
    operation_mode: Literal[
        "Power On",
        "Standby",
        "Mains",
        "Off-Grid",
        "Bypass",
        "Charging",
        "Fault",
    ]

    mains_voltage: float
    mains_frequency: float
    mains_power: int

    inverter_voltage: float
    inverter_current: float
    inverter_frequency: float
    inverter_power: int
    inverter_charge_power: int

    output_voltage: float
    output_current: float
    output_frequency: float
    output_active_power: int

    battery_voltage: float
    battery_current: float
    battery_power: int

    pv_voltage: float
    pv_current: float
    pv_power: int
    pv_charge_power: int

    load_percent: int
    temp_dcdc: int
    temp_inverter: int

    @field_validator("*", mode="before")
    @classmethod
    def validate_numbers(cls, v):
        if isinstance(v, (int, float)):
            return v
        raise ValueError(f"Invalid sensor value: {v}")


class EasunSMG2ModbusConfig(BaseModel):
    output_mode: int
    output_priority: int
    input_voltage_range: int
    buzzer_mode: int

    lcd_backlight: int
    lcd_auto_return: int
    energy_saving_mode: int

    overload_auto_restart: int
    overtemp_auto_restart: int
    overload_transfer_to_bypass: int

    battery_eq_enabled: int

    output_voltage_setting: float
    output_freq_setting: float

    battery_ovp: float
    max_charge_voltage: float
    float_charge_voltage: float

    battery_discharge_recovery_mains: float
    battery_low_protection_mains: float
    battery_low_protection_offgrid: float

    battery_charging_priority: int
    max_charging_current: float
    max_mains_charging_current: float

    eq_charging_voltage: float
    eq_time_minutes: int
    eq_timeout: int
    eq_interval_days: int

    @field_validator("*", mode="before")
    @classmethod
    def validate_config(cls, v):
        if isinstance(v, (int, float)):
            return v
        raise ValueError(f"Invalid config value: {v}")


class EasunSMG2ModbusParser(BaseInverterParser):
    def sensors_from(
            self,
            s: EasunSMG2ModbusSensors,
    ) -> InverterSensorData:
        return InverterSensorData(
            grid_voltage=s.mains_voltage,
            grid_frequency=s.mains_frequency,
            grid_power=s.mains_power,

            ac_output_voltage=s.output_voltage,
            ac_output_frequency=s.output_frequency,
            ac_output_power=s.output_active_power,
            ac_load_percent=s.load_percent,

            battery_voltage=s.battery_voltage,
            battery_current=s.battery_current,
            battery_power=s.battery_power,

            pv_input_voltage=s.pv_voltage,
            pv_input_current=s.pv_current,
            pv_input_power=s.pv_power,

            inverter_bus_voltage=s.inverter_voltage,
            inverter_temperature=s.temp_inverter,
            inverter_dc_temperature=s.temp_dcdc,
        )

    def settings_from(
            self,
            c: EasunSMG2ModbusConfig,
    ) -> InverterSettings:
        return InverterSettings(
            low_battery_to_ac_bypass_voltage=c.battery_discharge_recovery_mains,
            high_battery_voltage_to_battery_mode=c.max_charge_voltage,
            shut_down_battery_voltage=c.battery_low_protection_offgrid,

            bulk_charging_voltage=c.max_charge_voltage,
            float_charging_voltage=c.float_charge_voltage,

            max_utility_charging_current=c.max_mains_charging_current,
            max_charging_current=c.max_charging_current,

            battery_type=str(c.battery_charging_priority),
            ac_input_voltage_range=str(c.input_voltage_range),
            output_source_priority=str(c.output_priority),
            charger_source_priority=str(c.battery_charging_priority),
        )

    def rated_from(
            self,
            c: EasunSMG2ModbusConfig,
    ) -> InverterRatedParams:
        return InverterRatedParams(
            rated_grid_voltage=c.output_voltage_setting,
            rated_input_current=c.max_mains_charging_current,

            rated_ac_output_voltage=c.output_voltage_setting,
            rated_output_frequency=c.output_freq_setting,
            rated_output_current=c.max_charging_current,

            rated_output_apparent_power=0.0,
            rated_output_active_power=0.0,

            rated_battery_voltage=c.battery_ovp,
        )

    def snapshot_from(
            self,
            sensors: EasunSMG2ModbusSensors,
            config: EasunSMG2ModbusConfig,
    ) -> InverterSnapshot:
        return InverterSnapshot(
            sensors=self.sensors_from(sensors),
            settings=self.settings_from(config),
            rated=self.rated_from(config),
        )
