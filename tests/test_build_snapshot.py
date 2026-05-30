"""Tests for the build_snapshot dispatcher (api/dispatcher.py) — Phase B.

Routes already-fetched legacy sections to the right adapter's
snapshot_from_sections, with no I/O. Pure — no Home Assistant."""
from custom_components.dess_monitor_local.api.dispatcher import build_snapshot
from custom_components.dess_monitor_local.api.model import WarningKey


def test_voltronic_uri_routes_to_voltronic_mapping():
    snap = build_snapshot(
        "tcp://1.2.3.4:8899",
        {"qpigs": {"battery_voltage": "27.00", "battery_charging_current": "005",
                   "battery_discharge_current": "00000", "bus_voltage": "400"}},
    )
    assert snap.metrics.battery_voltage == 27.0
    assert snap.metrics.battery_current == 5.0
    assert snap.metrics.bus_voltage == 400.0  # voltronic keeps its real value


def test_modbus_uri_recovers_raw_and_drops_fabrication():
    sections = {
        "qmod": {
            "sensors": {"battery_voltage": 27.3, "battery_current": -14.0,
                        "mains_voltage": 237.0},
            "config": {"output_priority": 0},
            "faults": {"fault_code": 0},
        }
    }
    snap = build_snapshot("modbus://1.2.3.4:502", sections)
    assert snap.metrics.battery_voltage == 27.3
    assert snap.metrics.battery_current == -14.0       # signed
    assert snap.metrics.bus_voltage is None            # SMG fabrication dropped


def test_pi18_uri_drops_bus_voltage_and_maps_faults():
    snap = build_snapshot(
        "pi18://1.2.3.4:502",
        {"qpigs": {"bus_voltage": "400", "battery_voltage": "26.00"},
         "qfws": {"warn_overload": True}},
    )
    assert snap.metrics.bus_voltage is None            # PI18 fabrication dropped
    assert snap.metrics.battery_voltage == 26.0
    assert WarningKey.OVERLOAD in snap.faults.warnings


def test_unknown_sections_yield_empty_snapshot():
    snap = build_snapshot("modbus://1.2.3.4:502", {"qpigs": {}})  # no raw recoverable
    assert snap.metrics.battery_voltage is None
