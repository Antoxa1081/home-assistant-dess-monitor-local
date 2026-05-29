"""Tests for the agent transport's pure helpers (api/protocols/agent_http.py):
command-bucket routing, signed-current splitting, device-status-bit
synthesis, and QFWS warn_* extraction. These encode every agent-path fix
from the Easun SMG-II debugging sessions."""
from custom_components.dess_monitor_local.api.protocols import agent_http

# A representative flat agent snapshot (postgen-easun-smg2 shape).
_RAW = {
    "grid_voltage": "237.0",
    "battery_current": "-14.0",
    "battery_voltage": "27.3",
    "output_active_power": "408",
    "pv_charging_power": "0",
    "operating_mode": "OffGrid",
    "qmod.operating_mode": "OffGrid",
    # QPIRI-ish config fields (name-routed, no prefix):
    "output_source_priority": "UtilityFirst",
    "charger_source_priority": "SolarAndUtility",
    "max_charging_current": "50.0",
    "bulk_charging_voltage": "28.6",
    # warn flags:
    "warn_any": "1",
    "warn_pv_low_voltage": "1",
    "warn_overload": "0",
    "warn_over_temperature": "0",
}


class TestSplitQpiri:
    def test_name_routed_when_no_prefix(self):
        d = agent_http.split_raw_by_command(_RAW, "QPIRI")
        assert d["output_source_priority"] == "UtilityFirst"
        assert d["bulk_charging_voltage"] == "28.6"

    def test_prefix_takes_precedence(self):
        raw = {"qpiri.output_source_priority": "SBU", "output_source_priority": "UtilityFirst"}
        d = agent_http.split_raw_by_command(raw, "QPIRI")
        assert d["output_source_priority"] == "SBU"


class TestSplitQpigs:
    def test_excludes_config_and_warn_keys(self):
        d = agent_http.split_raw_by_command(_RAW, "QPIGS")
        assert "output_source_priority" not in d   # QPIRI field
        assert "bulk_charging_voltage" not in d     # QPIRI field
        assert not any(k.startswith("warn_") for k in d)  # warnings

    def test_signed_current_split_discharge(self):
        d = agent_http.split_raw_by_command(_RAW, "QPIGS")
        # battery_current -14 -> 0 charge, 14 discharge.
        assert float(d["battery_charging_current"]) == 0.0
        assert float(d["battery_discharge_current"]) == 14.0

    def test_signed_current_split_charge(self):
        raw = dict(_RAW, battery_current="6.5")
        d = agent_http.split_raw_by_command(raw, "QPIGS")
        assert float(d["battery_charging_current"]) == 6.5
        assert float(d["battery_discharge_current"]) == 0.0

    def test_status_bits_synthesized(self):
        # True off-grid snapshot: grid actually down (0 V).
        raw = dict(_RAW, grid_voltage="0.0")
        d = agent_http.split_raw_by_command(raw, "QPIGS")
        b7 = d["device_status_bits_b7_b0"]
        assert len(b7) == 8
        assert b7[-1] == "1"   # b0 inverter_on (OffGrid)
        assert b7[3] == "1"    # b4 line_fail (grid down)


class TestSplitQfws:
    def test_warn_flags_become_bool(self):
        d = agent_http.split_raw_by_command(_RAW, "QFWS")
        assert d["warn_pv_low_voltage"] is True
        assert d["warn_overload"] is False

    def test_only_warn_and_fault_keys(self):
        d = agent_http.split_raw_by_command(_RAW, "QFWS")
        assert all(
            k.startswith("warn_") or k in ("fault_code", "fault_description")
            for k in d
        )


class TestSynthBits:
    def test_b7b0_grid_present_mains(self):
        raw = {"operating_mode": "Mains", "grid_voltage": "237.0", "load_percent": "11"}
        bits = agent_http._synth_b7_b0({}, raw)
        assert bits[-1] == "1"   # inverter_on
        assert bits[3] == "0"    # line_fail off (grid present)

    def test_b7b0_overload(self):
        raw = {"operating_mode": "Mains", "grid_voltage": "237", "load_percent": "150"}
        bits = agent_http._synth_b7_b0({}, raw)
        assert bits[6] == "1"    # b1 overload (index 6 from MSB)

    def test_b10b8_charging(self):
        qpigs = {"battery_current": "10.0"}
        raw = {"grid_voltage": "237", "pv_charging_power": "0"}
        bits = agent_http._synth_b10_b8(qpigs, raw)
        assert bits[0] == "1"    # charging_to_battery
        assert bits[1] == "1"    # ac_charging (grid up)

    def test_b10b8_pv_charging(self):
        qpigs = {"battery_current": "5.0"}
        raw = {"grid_voltage": "0", "pv_charging_power": "1500"}
        bits = agent_http._synth_b10_b8(qpigs, raw)
        assert bits[2] == "1"    # scc_charging (PV producing)


class TestParseAgentUri:
    def test_valid(self):
        host, port, dev = agent_http.parse_agent_uri("agent://10.0.0.5:8787/easun-1")
        assert (host, port, dev) == ("10.0.0.5", 8787, "easun-1")

    def test_missing_device_raises(self):
        import pytest
        with pytest.raises(ValueError):
            agent_http.parse_agent_uri("agent://10.0.0.5:8787/")
