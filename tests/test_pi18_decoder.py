"""Tests for the PI18 / InfiniSolar-V decoders (api/decoders/pi18.py)."""
import pytest

from custom_components.dess_monitor_local.api.decoders import pi18


class TestSafeInt:
    def test_valid(self):
        assert pi18._safe_int("42") == 42

    def test_garbage_defaults(self):
        assert pi18._safe_int("abc") == 0
        assert pi18._safe_int("", default=7) == 7
        assert pi18._safe_int(None) == 0


class TestStripFrame:
    def test_strips_header_and_crc(self):
        # ^Dnnn header (5 bytes) + body + 2 CRC bytes + CR.
        raw = b"^D025HELLO\x12\x34\r"
        assert pi18._strip_pi18_frame(raw) == b"HELLO"

    def test_paren_variant(self):
        raw = b"(HELLO\x12\x34\r"
        assert pi18._strip_pi18_frame(raw) == b"HELLO"


class TestDecodeMod:
    def test_battery_mode(self):
        # PI18 mode code 3 = battery/off-grid -> OperatingMode.Battery.
        d = pi18.decode_pi18_response("QMOD", b"^D0053\x00\x00\r")
        assert d["operating_mode"].name == "Battery"

    def test_unknown_code(self):
        d = pi18.decode_pi18_response("QMOD", b"^D0059\x00\x00\r")
        assert d["operating_mode"] == "Unknown"


class TestDecodeFws:
    def test_no_fault(self):
        # First token is fault_code; rest are warn bits.
        tokens = ["0"] + ["0"] * 16
        d = pi18._decode_fws(tokens)
        assert d["fault_code"] == 0
        assert d["has_fault"] is False
        assert d["fault_description"] == "No fault"

    def test_known_fault_code(self):
        tokens = ["2"] + ["0"] * 16
        d = pi18._decode_fws(tokens)
        assert d["fault_code"] == 2
        assert d["has_fault"] is True
        assert d["fault_description"] == "Over temperature"

    def test_unknown_fault_code(self):
        tokens = ["999"] + ["0"] * 16
        d = pi18._decode_fws(tokens)
        assert d["has_fault"] is True
        assert "Unknown" in d["fault_description"]

    def test_warning_bits_become_bools(self):
        tokens = ["0", "1"] + ["0"] * 15  # second token = first warn flag
        d = pi18._decode_fws(tokens)
        first_warn = pi18._FWS_WARNING_FIELDS[0]
        assert d[first_warn] is True

    def test_short_input_padded(self):
        d = pi18._decode_fws(["0"])
        # All warn flags default to False when absent.
        assert all(
            d[name] is False for name in pi18._FWS_WARNING_FIELDS
        )


class TestSetCommandAck:
    def test_ack(self):
        assert pi18.decode_pi18_response("POP", b"^1\x00\x00\r")["status"] == "ACK"

    def test_nak(self):
        assert pi18.decode_pi18_response("POP", b"^0\x00\x00\r")["status"] == "NAK"

    def test_null(self):
        assert "error" in pi18.decode_pi18_response("QMOD", b"null")


class TestBuildRequestFrame:
    def test_logical_command_translated(self):
        # QPIGS -> native "GS"; envelope ^P<nnn>GS<CRC>\r.
        frame = pi18.build_request_frame("QPIGS")
        assert frame.startswith(b"^P")
        assert b"GS" in frame
        assert frame.endswith(b"\r")

    def test_length_field_counts_body_plus_crc_plus_cr(self):
        frame = pi18.build_request_frame("QPIGS")
        # ^Pnnn where nnn = len(body) + 3 (2 CRC + 1 CR). body="GS" -> 5.
        nnn = int(frame[2:5])
        assert nnn == len("GS") + 3

    def test_unknown_command_passthrough(self):
        frame = pi18.build_request_frame("CUSTOMX")
        assert b"CUSTOMX" in frame


class TestDecodePiri:
    def _piri(self, **over):
        # 25 fields; defaults are plausible 24V values.
        vals = [
            "2300",  # rated_grid_voltage 230.0
            "182",   # rated_input_current 18.2
            "2300",  # rated_ac_output_voltage
            "500",   # rated_output_frequency 50.0
            "182",   # rated_output_current
            "4200",  # rated_output_apparent_power
            "4200",  # rated_output_active_power
            "240",   # rated_battery_voltage 24.0
            "250",   # battery_recharge_voltage
            "245",   # battery_redischarge_voltage
            "230",   # battery_under_voltage
            "286",   # battery_bulk_voltage 28.6
            "272",   # battery_float_voltage 27.2
            "3",     # battery_type_code -> LIB
            "30",    # max_ac_charging_current
            "100",   # max_charging_current
            "1",     # input_voltage_range_code -> UPS
            "1",     # output_priority_code -> SBU (PI18 map)
            "2",     # charger_priority_code -> OnlySolar (PI18 map)
            "6",     # parallel_max
            "0", "0", "0", "0", "0",
        ]
        return vals

    def test_scaling_and_enums(self):
        d = pi18._decode_piri(self._piri())
        assert d["rated_grid_voltage"] == "230.0"
        assert d["bulk_charging_voltage"] == "28.6"
        assert d["float_charging_voltage"] == "27.2"
        assert d["battery_type"] == "LIB"
        assert d["ac_input_voltage_range"] == "UPS"
        # PI18 output priority code 1 -> SBU per _PI18_OUTPUT_PRIORITY.
        assert d["output_source_priority"] == "SBU"
        # charger code 2 -> OnlySolar.
        assert d["charger_source_priority"] == "OnlySolar"

    def test_unknown_battery_type_passthrough(self):
        vals = self._piri()
        vals[13] = "9"  # not a known BatteryType code
        d = pi18._decode_piri(vals)
        assert d["battery_type"] == "9"

    def test_no_fabricated_nameplate_placeholders(self):
        # Phase D: PI18 has no PIRI readout for these — they must not be
        # fabricated (snapshot reports None, the sensors are gated).
        d = pi18._decode_piri(self._piri())
        for fabricated in (
            "parallel_mode", "rated_battery_capacity", "reserved_uu",
            "reserved_v", "reserved_b", "reserved_ccc",
            "solar_work_condition_in_parallel",
            "solar_max_charging_power_auto_adjust",
        ):
            assert fabricated not in d
        # Real PIRI fields stay.
        assert d["parallel_max_number"] == "6"
        assert d["high_battery_voltage_to_battery_mode"] == "25.0"


class TestSmallDecoders:
    def test_decode_id_with_length_prefix(self):
        # LL=05 then digits; only first 5 chars are the serial.
        assert pi18._decode_id("0512345XXXXX")["serial_number"] == "12345"

    def test_decode_t_formats_datetime(self):
        assert pi18._decode_t("20260524120000")["device_time"] == "2026-05-24 12:00:00"

    def test_decode_t_short_passthrough(self):
        assert pi18._decode_t("short")["device_time"] == "short"

    def test_decode_energy_total(self):
        assert pi18._decode_energy("ET", "00012345")["total_energy_kwh"] == 12345

    def test_decode_energy_daily(self):
        d = pi18._decode_energy("ED20260524", "00000500")
        assert d["daily_energy_wh"] == 500
        assert d["energy_period"] == "20260524"

    def test_decode_vfw(self):
        d = pi18._decode_vfw(["00001", "00002", "00003"])
        assert d["cpu_main_version"] == "00001"
        assert d["cpu_slave2_version"] == "00003"

    def test_decode_selectable_currents(self):
        d = pi18._decode_selectable_currents(["010", "020", "030"], "opts")
        assert d["opts"] == [10, 20, 30]

    def test_decode_di_scaling_and_types(self):
        # _DI_FIELDS: first is voltage (0.1), bools at the tail.
        tokens = ["2300"] + ["0"] * (len(pi18._DI_FIELDS) - 1)
        d = pi18._decode_di(tokens)
        name0, scale0 = pi18._DI_FIELDS[0]
        assert d[name0] == pytest.approx(230.0)


class TestEnumName:
    def test_known(self):
        from custom_components.dess_monitor_local.api.decoders.enums import PI18MPPTStatus
        assert pi18._enum_name(PI18MPPTStatus, "2", "Abnormal") == "Charging"

    def test_default_on_miss(self):
        from custom_components.dess_monitor_local.api.decoders.enums import PI18MPPTStatus
        assert pi18._enum_name(PI18MPPTStatus, "9", "Abnormal") == "Abnormal"


class TestDecodeGs:
    def test_synthesizes_split_currents_and_pv2(self):
        # GS tokens: build a minimal valid set. Field order per _GS_FIELDS.
        # We only assert on stable derived fields.
        tokens = [
            "2393",   # grid_voltage (0.1V)
            "500",    # grid_frequency
            "2306",   # ac_output_voltage
            "499",    # ac_output_frequency
            "2144",   # output_apparent_power
            "2136",   # output_active_power
            "53",     # load_percent
            "267",    # battery_voltage (0.1V) -> 26.7
            "000",    # scc_battery_voltage
            "000",    # _battery_voltage_scc2
            "022",    # battery_discharge_current
            "000",    # battery_charging_current
            "062",    # battery_capacity
            "043",    # inverter_heat_sink_temperature
        ]
        d = pi18._decode_gs(tokens)
        assert d["battery_voltage"] == "26.70"
        assert d["battery_discharge_current"] == "00022"
        assert d["battery_charging_current"] == "000"

    def test_no_fabricated_bus_voltage_or_status_bits(self):
        # Phase D: PI18 has neither a bus-voltage nor PI30 status-bit field;
        # neither may be fabricated (the PI30 status sensors go unavailable).
        d = pi18._decode_gs(["2393", "500", "2306", "499"])
        assert "bus_voltage" not in d
        assert "device_status_bits_b7_b0" not in d
        assert "device_status_bits_b10_b8" not in d
