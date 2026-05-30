"""Tests for the SMG-II Modbus pure helpers (api/protocols/modbus_rtu.py):
signed-register conversion, URI parsing, and the typed snapshot projection.

Also covers the pure RTU framing helpers and the EyBond-modbus transport
(SMG-II forwarded through a dongle's FC=4 channel)."""
import asyncio
from unittest.mock import patch

import pytest

from custom_components.dess_monitor_local.api.adapters import modbus as modadapter
from custom_components.dess_monitor_local.api.crc import crc16_modbus
from custom_components.dess_monitor_local.api.decoders.enums import (
    ACInputVoltageRange,
    ChargerSourcePriority,
    OutputSourcePriority,
)
from custom_components.dess_monitor_local.api.protocols import modbus_rtu


def _rtu_read_response(unit_id: int, regs: list[int]) -> bytes:
    body = bytearray([unit_id, 3, len(regs) * 2])
    for r in regs:
        body += bytes([(r >> 8) & 0xFF, r & 0xFF])
    crc = crc16_modbus(bytes(body))
    return bytes(body) + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


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


class TestSmg2ToSnapshot:
    def test_no_fabrication(self):
        snap = modbus_rtu.smg2_to_snapshot(_SENSORS, _CONFIG, {})
        m, r = snap.metrics, snap.ratings
        # Fields the SMG-II can't measure are None (no Voltronic-shaped fakes).
        assert m.bus_voltage is None              # was "400"
        assert m.battery_soc is None              # was "100"
        assert m.scc_battery_voltage is None      # was mirrored battery_voltage
        assert m.ac_output_apparent_power is None  # was = active power
        assert all(v is None for v in vars(m.status).values())  # no status bits
        assert r.output_active_power is None      # was "4000"
        assert r.battery_type is None             # was "UserDefined"
        assert r.parallel_max_number is None      # was "6"
        assert r.grid_voltage is None             # was "230.0"

    def test_real_fields_typed(self):
        snap = modbus_rtu.smg2_to_snapshot(_SENSORS, _CONFIG, {})
        m, r = snap.metrics, snap.ratings
        assert m.grid_voltage == 237.0
        assert m.battery_voltage == 27.3
        assert m.battery_current == -14.0          # signed (discharging)
        assert m.battery_charge_current == 0.0
        assert m.battery_discharge_current == 14.0
        assert m.pv1.voltage == 32.9
        assert m.temp_heatsink == 30.0 and m.temp_dcdc == 27.0
        assert r.bulk_charging_voltage == 28.6
        assert r.float_charging_voltage == 27.2
        assert r.ac_input_voltage_range is ACInputVoltageRange.UPS
        assert r.output_source_priority is OutputSourcePriority.UtilityFirst
        assert r.charger_source_priority is ChargerSourcePriority.SolarAndUtility

    def test_faults_and_capabilities(self):
        snap = modbus_rtu.smg2_to_snapshot(
            _SENSORS, _CONFIG,
            {"fault_code": 5, "warning_code": 0, "fault_description": "x"},
        )
        assert snap.faults.fault_code == 5
        assert snap.faults.has_fault is True
        assert "grid_power" in snap.capabilities
        assert snap.raw["sensors"] is _SENSORS

    def test_get_data_returns_raw_for_every_command(self):
        # Phase D: SMG-II no longer fabricates Voltronic-shaped QPIGS/QPIRI.
        # Every command returns the same raw register blocks; the coordinator
        # rebuilds the typed snapshot from them.
        async def fake_snapshot(read_block):
            return (dict(_SENSORS), dict(_CONFIG), {})

        uri = "modbus://10.0.0.5:502"
        expected = {"sensors": _SENSORS, "config": _CONFIG, "faults": {}}
        with patch.object(modadapter, "read_smg2_snapshot_via", side_effect=fake_snapshot):
            for cmd in ("QPIGS", "QPIRI", "QMOD", "QPIGS2", "QPIWS", "QFWS"):
                modadapter._clear_snapshot_cache()
                out = asyncio.run(modadapter.ModbusAdapter(uri).get_data(cmd))
                assert out == expected
                assert out  # truthy — must not trip the coordinator's failed-read guard

    def test_snapshot_rebuilds_from_raw_sections(self):
        # The coordinator's snapshot_from_sections recovers the model from the
        # raw {sensors,config,faults} dict carried in any fetched section.
        sections = {"qmod": {"sensors": _SENSORS, "config": _CONFIG, "faults": {}}}
        snap = modadapter.ModbusAdapter("modbus://10.0.0.5:502").snapshot_from_sections(
            sections
        )
        assert snap.metrics.battery_voltage == 27.3
        assert snap.metrics.bus_voltage is None        # no fabrication


class TestRtuFraming:
    def test_read_frame_fields_and_crc(self):
        frame = modbus_rtu.build_read_holding_frame(201, 31, unit_id=1)
        assert frame[0] == 1 and frame[1] == 3
        # start 201 = 0x00C9, count 31 = 0x001F (big-endian).
        assert frame[2:6] == bytes([0x00, 0xC9, 0x00, 0x1F])
        assert crc16_modbus(frame[:-2]) == (frame[-2] | (frame[-1] << 8))

    def test_parse_read_response_roundtrip(self):
        resp = _rtu_read_response(1, [0x1234, 0x5678])
        assert modbus_rtu.parse_read_holding_response(resp, 2, unit_id=1) == [
            0x1234, 0x5678,
        ]

    def test_parse_read_crc_mismatch(self):
        resp = bytearray(_rtu_read_response(1, [0x0001]))
        resp[-1] ^= 0xFF  # corrupt CRC
        with pytest.raises(ValueError):
            modbus_rtu.parse_read_holding_response(bytes(resp), 1, unit_id=1)

    def test_parse_read_exception_byte(self):
        # func | 0x80 marks a Modbus exception response.
        resp = bytes([1, 0x83, 2, 0x00, 0x00])
        with pytest.raises(ValueError):
            modbus_rtu.parse_read_holding_response(resp, 1, unit_id=1)

    def test_write_frame_echo_ok(self):
        frame = modbus_rtu.build_write_single_frame(301, 2, unit_id=1, func_code=0x06)
        assert frame[1] == 0x06
        # A 0x06 write echoes the request; parsing the echo is OK.
        assert modbus_rtu.parse_write_response(frame, unit_id=1)["status"] == "OK"

    def test_write_exception(self):
        resp = bytes([1, 0x86, 4, 0x00, 0x00])
        assert "error" in modbus_rtu.parse_write_response(resp, unit_id=1)


class TestEybondModbusTransport:
    def test_unit_id_from_uri_devaddr(self):
        t = modadapter._EybondModbusTransport(
            "eybond-modbus://0.0.0.0:8899/3?pn=PNX"
        )
        assert t.unit_id == 3

    def test_read_block_sends_frame_and_parses(self):
        captured = {}

        async def fake_send(uri, frame, timeout, context="", pn=None):
            captured["frame"] = frame
            captured["uri"] = uri
            return _rtu_read_response(3, [10, 20])

        t = modadapter._EybondModbusTransport(
            "eybond-modbus://0.0.0.0:8899/3?pn=PNX"
        )
        with patch.object(modadapter, "send_eybond_bytes", side_effect=fake_send):
            regs = asyncio.run(t.read_block(201, 2))
        assert regs == [10, 20]
        # Outgoing frame is a func-3 read addressed to unit id 3.
        assert captured["frame"][0] == 3 and captured["frame"][1] == 3

    def test_read_block_no_response_raises(self):
        async def fake_send(uri, frame, timeout, context="", pn=None):
            return None

        t = modadapter._EybondModbusTransport("eybond-modbus://0.0.0.0:8899/1?pn=P")
        with patch.object(modadapter, "send_eybond_bytes", side_effect=fake_send):
            with pytest.raises(ConnectionError):
                asyncio.run(t.read_block(201, 2))


class TestSnapshotCache:
    def test_one_read_serves_all_commands_within_ttl(self):
        calls = {"n": 0}

        async def fake_snapshot(read_block):
            calls["n"] += 1
            return (dict(_SENSORS), dict(_CONFIG), {})

        modadapter._clear_snapshot_cache()
        uri = "eybond-modbus://0.0.0.0:8899/1?pn=PN_CACHE"
        with patch.object(modadapter, "read_smg2_snapshot_via", side_effect=fake_snapshot):
            # New adapter per command (matches real dispatch), same URI.
            for cmd in ("QPIGS", "QPIRI", "QMOD", "QPIGS2", "QPIWS", "QFWS"):
                out = asyncio.run(modadapter.ModbusAdapter(uri).get_data(cmd))
                assert out is not None
        # All six commands of a cycle shared a single snapshot read.
        assert calls["n"] == 1

    def test_distinct_uris_cache_independently(self):
        calls = {"n": 0}

        async def fake_snapshot(read_block):
            calls["n"] += 1
            return (dict(_SENSORS), dict(_CONFIG), {})

        modadapter._clear_snapshot_cache()
        with patch.object(modadapter, "read_smg2_snapshot_via", side_effect=fake_snapshot):
            asyncio.run(modadapter.ModbusAdapter("eybond-modbus://0.0.0.0:8899/1?pn=A").get_data("QPIGS"))
            asyncio.run(modadapter.ModbusAdapter("eybond-modbus://0.0.0.0:8899/2?pn=B").get_data("QPIGS"))
        assert calls["n"] == 2

    def test_failed_snapshot_cached_as_empty(self):
        calls = {"n": 0}

        async def boom(read_block):
            calls["n"] += 1
            raise ConnectionError("dongle gone")

        modadapter._clear_snapshot_cache()
        uri = "eybond-modbus://0.0.0.0:8899/1?pn=PN_FAIL"
        with patch.object(modadapter, "read_smg2_snapshot_via", side_effect=boom):
            a = asyncio.run(modadapter.ModbusAdapter(uri).get_data("QPIGS"))
            b = asyncio.run(modadapter.ModbusAdapter(uri).get_data("QPIRI"))
        assert a == {} and b == {}
        # Failure cached too — not re-hammered for every command in the cycle.
        assert calls["n"] == 1
