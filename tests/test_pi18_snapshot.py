"""Tests for the PI18 → domain-model projection
(api/decoders/pi18.py::pi18_to_snapshot). Pure — no Home Assistant.

PI18 reuses the Voltronic mapping for shared fields, adds PV2 / dual MPPT
temps / direction enums, and drops PI18's fabricated values."""
from custom_components.dess_monitor_local.api.decoders.enums import (
    BatteryType,
    OperatingMode,
    OutputSourcePriority,
    PI18BatteryPowerDirection,
    PI18DCACPowerDirection,
    PI18LinePowerDirection,
    PI18MPPTStatus,
)
from custom_components.dess_monitor_local.api.decoders.pi18 import pi18_to_snapshot
from custom_components.dess_monitor_local.api.model import WarningKey

_QPIGS = {
    "grid_voltage": "239.3", "grid_frequency": "50.0",
    "ac_output_voltage": "230.6", "ac_output_frequency": "50.0",
    "output_active_power": "2136", "output_apparent_power": "2144",
    "load_percent": "053", "bus_voltage": "400",            # fabricated
    "battery_voltage": "26.70", "battery_charging_current": "010",
    "battery_discharge_current": "00000", "battery_capacity": "062",
    "inverter_heat_sink_temperature": "0043",
    "pv_input_voltage": "140.4", "pv_input_current": "12.0",
    "pv_charging_power": "01697",
    "pv2_input_voltage": "138.0", "pv2_input_current": "5.0",
    "pv2_input_power": "00690",
    "mppt1_temperature": "45.0", "mppt2_temperature": "44.0",
    "scc2_battery_voltage": "27.00",
    "mppt1_status": "Charging", "mppt2_status": "NotCharging",
    "battery_power_direction": "Charging",
    "dcac_power_direction": "DCtoAC",
    "line_power_direction": "Input",
}
_QPIRI = {
    "rated_output_active_power": "4200", "rated_battery_voltage": "24.0",
    "bulk_charging_voltage": "28.4", "float_charging_voltage": "27.2",
    "battery_type": "UserDefined", "output_source_priority": "SBU",
    "charger_source_priority": "SolarFirst", "ac_input_voltage_range": "UPS",
    "parallel_max_number": "0", "parallel_mode": "Standalone",  # fabricated
    "rated_battery_capacity": "200",                            # fabricated
}
_QMOD = {"operating_mode": OperatingMode.Battery}
_QFWS = {"warn_overload": True, "warn_line_fail": True, "fault_code": 0}


def _snap():
    return pi18_to_snapshot(
        {"qpigs": _QPIGS, "qpiri": _QPIRI, "qmod": _QMOD, "qfws": _QFWS}
    )


class TestPi18DropsFabrication:
    def test_bus_voltage_and_ratings(self):
        snap = _snap()
        assert snap.metrics.bus_voltage is None          # was "400"
        assert snap.ratings.parallel_mode is None        # was "Standalone"
        assert snap.ratings.parallel_max_number is None  # placeholder
        assert snap.ratings.battery_capacity_ah is None  # was "200"


class TestPi18RealAndExtras:
    def test_shared_real_fields(self):
        snap = _snap()
        assert snap.metrics.battery_soc == 62.0          # PI18 reports it
        assert snap.metrics.battery_current == 10.0
        assert snap.metrics.mode is OperatingMode.Battery
        assert snap.ratings.output_active_power == 4200.0
        assert snap.ratings.battery_type is BatteryType.UserDefined
        assert snap.ratings.output_source_priority is OutputSourcePriority.SBU

    def test_pi18_extras(self):
        m = _snap().metrics
        assert m.pv2 is not None
        assert m.pv2.voltage == 138.0 and m.pv2.power == 690.0
        assert m.scc2_battery_voltage == 27.0
        assert m.temp_mppt1 == 45.0 and m.temp_mppt2 == 44.0
        assert m.mppt1_status is PI18MPPTStatus.Charging
        assert m.mppt2_status is PI18MPPTStatus.NotCharging
        assert m.battery_power_direction is PI18BatteryPowerDirection.Charging
        assert m.dcac_power_direction is PI18DCACPowerDirection.DCtoAC
        assert m.line_power_direction is PI18LinePowerDirection.Input

    def test_capabilities(self):
        caps = _snap().capabilities
        assert {"pv2", "directions", "mppt_temp", "scc2", "device_soc"} <= caps


class TestPi18Faults:
    def test_warnings_from_fws(self):
        # warn_* keys that map onto the canonical set (full PI18 reconciliation
        # is a Phase C concern); unmapped warn_* are ignored for now.
        assert _snap().faults.warnings == {WarningKey.OVERLOAD, WarningKey.LINE_FAIL}

    def test_fault_code_carried(self):
        snap = pi18_to_snapshot({"qfws": {"fault_code": 7, "fault_description": "x"}})
        assert snap.faults.fault_code == 7
        assert snap.faults.has_fault is True
