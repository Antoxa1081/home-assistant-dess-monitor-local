"""Tests for the agent → domain-model projection
(api/adapters/agent.py::agent_to_snapshot). Pure — no Home Assistant.

The agent reuses the Voltronic mapping (it's faithful) and merges its
qfws warn_* faults on top."""
from custom_components.dess_monitor_local.api.adapters.agent import (
    agent_to_snapshot,
)
from custom_components.dess_monitor_local.api.decoders.enums import OperatingMode
from custom_components.dess_monitor_local.api.model import WarningKey

_QPIGS = {
    "grid_voltage": "238.0", "ac_output_voltage": "230.0",
    "output_active_power": "0500", "bus_voltage": "390",
    "battery_voltage": "27.10", "battery_charging_current": "005",
    "battery_discharge_current": "00000", "battery_capacity": "075",
    "pv_input_voltage": "150.0", "pv_charging_power": "00800",
}
_QPIRI = {
    "rated_output_active_power": "5000", "output_source_priority": "SolarFirst",
}
_QMOD = {"operating_mode": OperatingMode.Line}
_QFWS = {
    "fault_code": 0,
    "warn_overload": True,
    "warn_line_fail": False,
    "warn_pv_low_voltage": True,   # agent-only — not in canonical set yet
}


def test_reuses_voltronic_mapping_no_fabrication_loss():
    snap = agent_to_snapshot(
        {"qpigs": _QPIGS, "qpiri": _QPIRI, "qmod": _QMOD, "qfws": _QFWS}
    )
    m = snap.metrics
    # Agent is faithful — real bus_voltage is kept (not nulled like PI18/SMG).
    assert m.bus_voltage == 390.0
    assert m.battery_voltage == 27.1
    assert m.battery_soc == 75.0
    assert m.battery_current == 5.0
    assert m.mode is OperatingMode.Line
    assert snap.ratings.output_active_power == 5000.0


def test_faults_merged_from_qfws():
    snap = agent_to_snapshot(
        {"qpigs": _QPIGS, "qpiri": _QPIRI, "qmod": _QMOD, "qfws": _QFWS}
    )
    # warn_overload maps; warn_pv_low_voltage (agent-only) is ignored for now.
    assert WarningKey.OVERLOAD in snap.faults.warnings
    assert WarningKey.LINE_FAIL not in snap.faults.warnings
    assert snap.faults.fault_code == 0


def test_fault_code_and_description_carried():
    snap = agent_to_snapshot(
        {"qfws": {"fault_code": 9, "fault_description": "boom", "warn_overload": False}}
    )
    assert snap.faults.fault_code == 9
    assert snap.faults.fault_description == "boom"
    assert snap.faults.has_fault is True
