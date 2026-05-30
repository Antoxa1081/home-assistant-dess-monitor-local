"""Tests for the Voltronic PI30 → domain-model projection
(api/decoders/voltronic.py::voltronic_to_snapshot) and WarningKey.from_flags.
Pure — no Home Assistant."""
from custom_components.dess_monitor_local.api.decoders.enums import (
    ACInputVoltageRange,
    BatteryType,
    ChargerSourcePriority,
    OperatingMode,
    OutputSourcePriority,
    ParallelMode,
)
from custom_components.dess_monitor_local.api.decoders.voltronic import (
    voltronic_to_snapshot,
)
from custom_components.dess_monitor_local.api.model import WarningKey

_QPIGS = {
    "grid_voltage": "239.3", "grid_frequency": "50.0",
    "ac_output_voltage": "230.6", "ac_output_frequency": "49.9",
    "output_apparent_power": "2144", "output_active_power": "2136",
    "load_percent": "053", "bus_voltage": "400", "battery_voltage": "26.70",
    "battery_charging_current": "010", "battery_capacity": "062",
    "inverter_heat_sink_temperature": "0043", "pv_input_current": "12.0",
    "pv_input_voltage": "140.4", "scc_battery_voltage": "27.00",
    "battery_discharge_current": "00000", "pv_charging_power": "01697",
}
_QPIRI = {
    "rated_grid_voltage": "230.0", "rated_output_active_power": "4200",
    "rated_battery_voltage": "24.0", "bulk_charging_voltage": "28.4",
    "float_charging_voltage": "27.2", "battery_type": "UserDefined",
    "ac_input_voltage_range": "UPS", "output_source_priority": "SBU",
    "charger_source_priority": "SolarFirst", "parallel_mode": "Master",
    "parallel_max_number": "6", "rated_battery_capacity": "200",
    "max_charging_current": "050", "max_utility_charging_current": "30",
}
_QMOD = {"operating_mode": OperatingMode.Line}
_QPIWS = {"overload": True, "fan_locked": True, "_reserved_0": False, "line_fail": False}
_QPIGS2 = {"pv_voltage": "138.0", "pv_current": "5.0"}


def _snap():
    return voltronic_to_snapshot(
        {"qpigs": _QPIGS, "qpiri": _QPIRI, "qmod": _QMOD,
         "qpiws": _QPIWS, "qpigs2": _QPIGS2}
    )


class TestVoltronicMetrics:
    def test_typed_measurements(self):
        m = _snap().metrics
        assert m.grid_voltage == 239.3
        assert m.ac_output_active_power == 2136.0
        assert m.ac_output_apparent_power == 2144.0
        assert m.battery_voltage == 26.7
        assert m.battery_soc == 62.0          # device-reported, real for PI30
        assert m.scc_battery_voltage == 27.0
        assert m.pv1.power == 1697.0
        assert m.temp_heatsink == 43.0
        assert m.mode is OperatingMode.Line

    def test_signed_battery_current(self):
        m = _snap().metrics
        assert m.battery_current == 10.0       # charging 10, discharging 0
        assert m.battery_charge_current == 10.0
        assert m.battery_discharge_current == 0.0
        assert m.battery_power == 267.0        # 26.7 V * 10 A

    def test_discharging_is_negative(self):
        qpigs = dict(_QPIGS, battery_charging_current="000",
                     battery_discharge_current="00022")
        m = voltronic_to_snapshot({"qpigs": qpigs}).metrics
        assert m.battery_current == -22.0

    def test_pv2_present(self):
        m = _snap().metrics
        assert m.pv2 is not None
        assert m.pv2.voltage == 138.0 and m.pv2.current == 5.0
        assert m.pv2.power == 690.0


class TestVoltronicRatings:
    def test_typed_and_enums(self):
        r = _snap().ratings
        assert r.output_active_power == 4200.0
        assert r.bulk_charging_voltage == 28.4
        assert r.parallel_max_number == 6
        assert r.battery_type is BatteryType.UserDefined
        assert r.ac_input_voltage_range is ACInputVoltageRange.UPS
        assert r.output_source_priority is OutputSourcePriority.SBU
        assert r.charger_source_priority is ChargerSourcePriority.SolarFirst
        assert r.parallel_mode is ParallelMode.Master

    def test_unknown_enum_name_is_none(self):
        r = voltronic_to_snapshot({"qpiri": {"battery_type": "Bogus"}}).ratings
        assert r.battery_type is None


class TestVoltronicFaultsAndCaps:
    def test_warnings_set(self):
        assert _snap().faults.warnings == {WarningKey.OVERLOAD, WarningKey.FAN_LOCKED}

    def test_capabilities(self):
        caps = _snap().capabilities
        assert {"pv2", "scc", "device_soc"} <= caps

    def test_empty_sections(self):
        snap = voltronic_to_snapshot({})
        assert snap.metrics.grid_voltage is None
        assert snap.faults.warnings == set()


class TestWarningKeyFromFlags:
    def test_bare_and_warn_prefixed_dedup(self):
        keys = WarningKey.from_flags(
            {"overload": True, "warn_overload": True, "line_fail": False,
             "_reserved_0": True, "not_a_real_flag": True}
        )
        assert keys == {WarningKey.OVERLOAD}

    def test_empty(self):
        assert WarningKey.from_flags({}) == set()
