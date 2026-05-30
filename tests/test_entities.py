"""Entity-level tests — run only where Home Assistant is importable.

These exercise the thin adapter layer between the coordinator data and
HA state: the fault-summary severity machine, status-bit binary sensors,
operating-mode enum coercion, and the time-to-* projections.

Strategy: construct the entity with a fake coordinator/device, neutralise
``async_write_ha_state`` (it needs a live HA platform), then drive
``_handle_coordinator_update`` and assert the computed attributes. No HA
event loop or hass fixture required, so it stays fast and version-robust.

CI runners without Home Assistant (the 3.12/3.13 pure matrix) skip this
whole module via ``importorskip``. A separate CI job installs HA to run it.
"""
import pytest

pytest.importorskip("homeassistant")

# Importing the real platform modules (no sys.modules stubs here) also
# acts as a smoke test: it would have caught the missing PI18-enum
# re-export that broke integration load.
from custom_components.dess_monitor_local import binary_sensor as bs  # noqa: E402
from custom_components.dess_monitor_local.sensors import (  # noqa: E402
    direct_energy_sensors as des,
)
from custom_components.dess_monitor_local.sensors import direct_sensor as ds  # noqa: E402


class _Dev:
    inverter_id = "easun_4200"
    name = "easun 4200"
    firmware_version = "0.0.1"


class _Coord:
    """Minimal stand-in for the DataUpdateCoordinator."""
    def __init__(self, data):
        self.data = {"easun_4200": data}

    # CoordinatorEntity registers a listener on add; never called here.
    def async_add_listener(self, *a, **k):
        return lambda: None


def _neutralise_write(entity):
    # async_write_ha_state needs a live platform; replace with a no-op so
    # we can call _handle_coordinator_update synchronously.
    entity.async_write_ha_state = lambda: None
    return entity


def _make(cls, data, *args):
    ent = cls(_Dev(), _Coord(data), *args)
    return _neutralise_write(ent)


# ---------------------------------------------------------------------------
# Smoke: every platform module imports (regression guard for load failures)
# ---------------------------------------------------------------------------
def test_all_platforms_import():
    from custom_components.dess_monitor_local import (  # noqa: F401
        binary_sensor,
        button,
        number,
        select,
        sensor,
        switch,
    )


# ---------------------------------------------------------------------------
# DirectOperatingModeSensor
# ---------------------------------------------------------------------------
class TestOperatingModeSensor:
    def test_enum_instance_coerced_to_name(self):
        from custom_components.dess_monitor_local.api.decoders.enums import OperatingMode
        ent = _make(ds.DirectOperatingModeSensor, {"qmod": {"operating_mode": OperatingMode.Battery}})
        ent._handle_coordinator_update()
        assert ent._attr_native_value == "Battery"

    def test_unknown_string_becomes_none(self):
        ent = _make(ds.DirectOperatingModeSensor, {"qmod": {"operating_mode": "Nonsense"}})
        ent._handle_coordinator_update()
        assert ent._attr_native_value is None

    def test_missing_section(self):
        ent = _make(ds.DirectOperatingModeSensor, {"qmod": {}})
        ent._handle_coordinator_update()
        assert ent._attr_native_value is None


# ---------------------------------------------------------------------------
# DirectInverterFaultSummarySensor — severity machine
# ---------------------------------------------------------------------------
class TestFaultSummary:
    def _make(self, qpiws=None, qfws=None):
        return _make_summary({"qpiws": qpiws or {}, "qfws": qfws or {}})

    def test_ok_when_clear(self):
        ent = self._make(qpiws={"overload": False, "inverter_fault": False})
        ent._handle_coordinator_update()
        assert ent._attr_native_value == "OK"

    def test_single_warning(self):
        ent = self._make(qpiws={"overload": True})
        ent._handle_coordinator_update()
        assert ent._attr_native_value == "Warning: Overload"

    def test_multiple_warnings_shows_count(self):
        ent = self._make(qpiws={"overload": True, "fan_locked": True})
        ent._handle_coordinator_update()
        # Highest severity (fan_locked is above overload) + "+1 more".
        assert "more" in ent._attr_native_value

    def test_pi18_fault_code_takes_priority(self):
        ent = self._make(qfws={"fault_code": 2, "fault_description": "Over temperature"})
        ent._handle_coordinator_update()
        assert ent._attr_native_value == "Fault: Over temperature"

    def test_agent_warn_prefix_recognised(self):
        # Agent uses warn_* naming; the summary must see it via _flag_set.
        ent = self._make(qfws={"warn_pv_low_voltage": True})
        ent._handle_coordinator_update()
        assert ent._attr_native_value.startswith("Warning")

    def test_active_count_attribute(self):
        ent = self._make(qpiws={"overload": True, "fan_locked": True})
        ent._handle_coordinator_update()
        assert ent._attr_extra_state_attributes["active_count"] == 2


def _make_summary(data):
    ent = ds.DirectInverterFaultSummarySensor(_Dev(), _Coord(data))
    return _neutralise_write(ent)


# ---------------------------------------------------------------------------
# Status-bit binary sensors
# ---------------------------------------------------------------------------
class TestStatusBitBinarySensors:
    def _bit(self, raw_field, parser, flag_key, qpigs):
        ent = bs._StatusBitBinarySensor(
            _Dev(), _Coord(qpigs),
            raw_field=raw_field, parser=parser, flag_key=flag_key,
            sensor_suffix=flag_key, name=flag_key, device_class=None,
        )
        _neutralise_write(ent)
        ent._handle_coordinator_update()
        return ent._attr_is_on

    def test_inverter_on_true(self):
        from custom_components.dess_monitor_local.api.decoders.enums import (
            parse_device_status_bits_b7_b0,
        )
        on = self._bit(
            "device_status_bits_b7_b0", parse_device_status_bits_b7_b0,
            "inverter_on", {"qpigs": {"device_status_bits_b7_b0": "00000001"}},
        )
        assert on is True

    def test_fault_false(self):
        from custom_components.dess_monitor_local.api.decoders.enums import (
            parse_device_status_bits_b7_b0,
        )
        on = self._bit(
            "device_status_bits_b7_b0", parse_device_status_bits_b7_b0,
            "fault", {"qpigs": {"device_status_bits_b7_b0": "00000001"}},
        )
        assert on is False

    def test_missing_field_is_none(self):
        from custom_components.dess_monitor_local.api.decoders.enums import (
            parse_device_status_bits_b7_b0,
        )
        on = self._bit(
            "device_status_bits_b7_b0", parse_device_status_bits_b7_b0,
            "fault", {"qpigs": {}},
        )
        assert on is None


class TestAnyWarning:
    def _make(self, data):
        ent = bs._AnyWarningBinarySensor(_Dev(), _Coord(data))
        return _neutralise_write(ent)

    def test_off_when_all_clear(self):
        ent = self._make({"qpiws": {"overload": False}, "qfws": {}})
        ent._handle_coordinator_update()
        assert ent._attr_is_on is False

    def test_on_via_qpiws_bit(self):
        ent = self._make({"qpiws": {"overload": True}, "qfws": {}})
        ent._handle_coordinator_update()
        assert ent._attr_is_on is True

    def test_on_via_pi18_fault_code(self):
        ent = self._make({"qpiws": {}, "qfws": {"fault_code": 5}})
        ent._handle_coordinator_update()
        assert ent._attr_is_on is True

    def test_on_via_agent_warn_flag(self):
        ent = self._make({"qpiws": {}, "qfws": {"warn_overload": True}})
        ent._handle_coordinator_update()
        assert ent._attr_is_on is True


# ---------------------------------------------------------------------------
# Time-to-* sensors (use a stub SoC sensor exposing native_value/capacity_ah)
# ---------------------------------------------------------------------------
class _StubSoc:
    def __init__(self, soc, capacity):
        self.native_value = soc
        self.capacity_ah = capacity


class TestTimeToFull:
    def test_hours_to_full(self):
        soc = _StubSoc(50.0, 100.0)
        ent = ds_energy_time_to_full(soc, qpigs={"battery_charging_current": "10"})
        ent._handle_coordinator_update()
        # (100-50)/100 * 100 Ah / 10 A = 5 h.
        assert ent._attr_native_value == pytest.approx(5.0, abs=0.01)

    def test_none_when_not_charging(self):
        soc = _StubSoc(50.0, 100.0)
        ent = ds_energy_time_to_full(soc, qpigs={"battery_charging_current": "0"})
        ent._handle_coordinator_update()
        assert ent._attr_native_value is None


def ds_energy_time_to_full(soc_sensor, qpigs):
    ent = des.DirectBatteryTimeToFullSensor(_Dev(), _Coord({"qpigs": qpigs}), soc_sensor)
    return _neutralise_write(ent)


# ---------------------------------------------------------------------------
# Domain-model migration (Phase C): metric sensors prefer the typed snapshot
# ---------------------------------------------------------------------------
class _SnapCoord(_Coord):
    """Coordinator stub exposing both legacy data and a snapshot."""

    def __init__(self, data, snapshot):
        super().__init__(data)
        self.snapshots = {"easun_4200": snapshot}


class TestSnapshotMetricMigration:
    def _snap(self, **metric_kwargs):
        from custom_components.dess_monitor_local.api.model import (
            DeviceSnapshot,
            Metrics,
        )
        return DeviceSnapshot(metrics=Metrics(**metric_kwargs))

    def test_snapshot_value_wins_over_legacy(self):
        # Legacy says 99.9, snapshot says 27.3 — the typed snapshot wins.
        snap = self._snap(battery_voltage=27.3)
        ent = ds.DirectBatteryVoltageSensor(
            _Dev(), _SnapCoord({"qpigs": {"battery_voltage": "99.9"}}, snap)
        )
        _neutralise_write(ent)
        ent._handle_coordinator_update()
        assert ent._attr_native_value == 27.3

    def test_fabricated_field_none_goes_unavailable(self):
        # SMG-II: bus_voltage is None in the model (was a fake "400") → None.
        snap = self._snap(bus_voltage=None)
        ent = ds.DirectBusVoltageSensor(
            _Dev(), _SnapCoord({"qpigs": {"bus_voltage": "400"}}, snap)
        )
        _neutralise_write(ent)
        ent._handle_coordinator_update()
        assert ent._attr_native_value is None

    def test_legacy_fallback_when_no_snapshots(self):
        # Coordinator without .snapshots → parse the legacy section.
        ent = ds.DirectBatteryVoltageSensor(
            _Dev(), _Coord({"qpigs": {"battery_voltage": "27.0"}})
        )
        _neutralise_write(ent)
        ent._handle_coordinator_update()
        assert ent._attr_native_value == 27.0
