"""Tests for the SMG-II Modbus pure helpers (api/protocols/modbus_rtu.py):
signed-register conversion, URI parsing, and the QPIGS/QPIRI projections."""
import pytest

from custom_components.dess_monitor_local.api.protocols import modbus_rtu


class TestI16:
    def test_positive(self):
        assert modbus_rtu._i16(100) == 100

    def test_zero(self):
        assert modbus_rtu._i16(0) == 0

    def test_negative(self):
        # 0xFFFF -> -1, 0x8000 -> -32768 (sign boundary).
        assert modbus_rtu._i16(0xFFFF) == -1
        assert modbus_rtu._i16(0x8000) == -32768

    def test_max_positive(self):
        assert modbus_rtu._i16(0x7FFF) == 32767


class TestParseModbusUri:
    def test_valid(self):
        assert modbus_rtu.parse_modbus_uri("modbus://192.168.1.50:502") == (
            "192.168.1.50", 502,
        )


_SENSORS = {
    "mains_voltage": 237.0,
    "mains_frequency": 50.0,
    "output_voltage": 230.6,
    "output_frequency": 50.0,
    "output_active_power": 408,
    "load_percent": 11,
    "battery_voltage": 27.3,
    "battery_current": -14.0,   # discharging
    "temp_inverter": 30,
    "temp_dcdc": 27,
    "pv_current": 0.0,
    "pv_voltage": 32.9,
    "pv_power": 0,
    "mains_power": 429,
}


class TestSmg2ToQpigs:
    def test_discharge_split(self):
        d = modbus_rtu.smg2_to_qpigs(_SENSORS)
        # battery_current -14 -> charge 0, discharge 14.
        assert int(d["battery_charging_current"]) == 0
        assert int(d["battery_discharge_current"]) == 14

    def test_charge_split(self):
        d = modbus_rtu.smg2_to_qpigs(dict(_SENSORS, battery_current=8.0))
        assert int(d["battery_charging_current"]) == 8
        assert int(d["battery_discharge_current"]) == 0

    def test_voltage_formatting(self):
        d = modbus_rtu.smg2_to_qpigs(_SENSORS)
        assert d["battery_voltage"] == "27.30"
        assert d["grid_voltage"] == "237.0"

    def test_qpigs_shape_keys_present(self):
        d = modbus_rtu.smg2_to_qpigs(_SENSORS)
        for key in (
            "grid_voltage", "battery_voltage", "battery_charging_current",
            "battery_discharge_current", "device_status_bits_b7_b0",
            "pv_charging_power",
        ):
            assert key in d


_CONFIG = {
    "input_voltage_range": 1,
    "battery_low_protection_mains": 24.0,
    "battery_low_protection_offgrid": 22.9,
    "max_charge_voltage": 28.6,
    "float_charge_voltage": 27.2,
    "max_mains_charging_current": 50.0,
    "max_charging_current": 50.0,
    "output_priority": 0,
    "battery_charging_priority": 2,
    "battery_discharge_recovery_mains": 25.0,
}


class TestSmg2ToQpiri:
    def test_voltage_passthrough(self):
        d = modbus_rtu.smg2_to_qpiri(_CONFIG)
        assert d["bulk_charging_voltage"] == "28.6"
        assert d["float_charging_voltage"] == "27.2"

    def test_ac_range_ups(self):
        d = modbus_rtu.smg2_to_qpiri(_CONFIG)
        assert d["ac_input_voltage_range"] == "UPS"

    def test_ac_range_appliance(self):
        d = modbus_rtu.smg2_to_qpiri(dict(_CONFIG, input_voltage_range=0))
        assert d["ac_input_voltage_range"] == "Appliance"

    def test_output_priority_mapped(self):
        d = modbus_rtu.smg2_to_qpiri(_CONFIG)
        # 0 -> UtilityFirst per _OUTPUT_PRIORITY_MAP.
        assert d["output_source_priority"] == "UtilityFirst"
