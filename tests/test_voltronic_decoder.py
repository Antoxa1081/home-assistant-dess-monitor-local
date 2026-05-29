"""Tests for the Voltronic PI30 decoders (api/decoders/voltronic.py).

Covers the field-position contract (the QPIGS index-15 discharge-current
slot that caused the 'stuck at 0' false alarm) and the QPIWS warning
bit map.
"""
from custom_components.dess_monitor_local.api.decoders import voltronic

# A real Anern 4200 QPIGS payload (from issue #5 diagnostics), without
# the leading "(" or trailing CRC — i.e. what decode_qpigs receives.
_QPIGS = (
    "239.3 50.0 230.6 49.9 2144 2136 053 400 26.70 000 062 0043 "
    "12.0 140.4 00.00 00022 00010110 00 00 01697 110"
)


class TestDecodeQpigs:
    def test_field_positions(self):
        d = voltronic.decode_qpigs(_QPIGS)
        assert d["grid_voltage"] == "239.3"
        assert d["battery_voltage"] == "26.70"
        assert d["battery_charging_current"] == "000"
        # Index 15 — the discharge-current slot that was suspected of
        # being misaligned. It must hold the real value, not status bits.
        assert d["battery_discharge_current"] == "00022"
        assert d["device_status_bits_b7_b0"] == "00010110"
        assert d["pv_charging_power"] == "01697"

    def test_status_bits_crc_bleed_sanitized(self):
        # Short frame where the CRC byte 's' bled into the last token.
        # decode_qpigs must store clean binary digits (TECH_DEBT fix).
        short = (
            "239.3 50.0 230.6 49.9 2144 2136 053 400 26.70 000 062 0043 "
            "12.0 140.4 00.00 00022 00010110 00 00 01697 110s"
        )
        d = voltronic.decode_qpigs(short)
        assert d["device_status_bits_b10_b8"] == "110"
        # Must be int-parseable now (the bug was int('110s', 2) crashing).
        assert int(d["device_status_bits_b10_b8"], 2) == 6

    def test_short_frame_truncates_gracefully(self):
        # zip() stops at the shorter sequence — missing trailing fields
        # simply aren't present, no exception.
        d = voltronic.decode_qpigs("239.3 50.0 230.6")
        assert d["grid_voltage"] == "239.3"
        assert "battery_discharge_current" not in d


class TestDecodeQpiws:
    def _bits(self, **flags) -> str:
        """Build a 32-char bitstring with the named flags set."""
        idx = {name: i for i, name in enumerate(voltronic._QPIWS_FIELDS)}
        arr = ["0"] * len(voltronic._QPIWS_FIELDS)
        for name, on in flags.items():
            if on:
                arr[idx[name]] = "1"
        return "".join(arr)

    def test_all_clear(self):
        d = voltronic.decode_qpiws("0" * 32)
        assert d["inverter_fault"] is False
        assert d["overload"] is False
        assert all(v is False for v in d.values())

    def test_overload_bit(self):
        d = voltronic.decode_qpiws(self._bits(overload=True))
        assert d["overload"] is True
        assert d["inverter_fault"] is False

    def test_multiple_bits(self):
        d = voltronic.decode_qpiws(
            self._bits(inverter_fault=True, over_temperature=True, fan_locked=True)
        )
        assert d["inverter_fault"] is True
        assert d["over_temperature"] is True
        assert d["fan_locked"] is True
        assert d["overload"] is False

    def test_tolerates_crc_bleed_and_whitespace(self):
        # Trailing non-0/1 chars (CRC bleed) and spaces must be stripped.
        raw = "  " + self._bits(overload=True) + "s\x00"
        d = voltronic.decode_qpiws(raw)
        assert d["overload"] is True

    def test_short_response_defaults_false(self):
        # Fewer bits than the field map -> missing bits default to False.
        # "0010": index 2 = bus_over = '1'.
        d = voltronic.decode_qpiws("0010")
        assert d["bus_over"] is True       # index 2
        assert d["overload"] is False      # index 16, beyond input


class TestDecodeQmod:
    def test_known_mode(self):
        d = voltronic.decode_qmod("L")
        # operating_mode maps to the OperatingMode enum member.
        assert d["operating_mode"].name == "Line"

    def test_unknown_mode(self):
        d = voltronic.decode_qmod("Z")
        assert d["operating_mode"] == "Unknown"


class TestDecodeQpiri:
    # 28 space-separated fields; values chosen to hit each enum slot.
    _QPIRI = (
        "230.0 18.2 230.0 50.0 18.2 4200 4200 24.0 24.5 24.0 28.4 27.2 "
        "2 002 100 1 2 1 1 01 0 0 27.0 0 1 24.5 10 22.0"
    )

    def test_enum_name_mapping(self):
        d = voltronic.decode_qpiri(self._QPIRI)
        # index 12 -> BatteryType "2" -> UserDefined
        assert d["battery_type"] == "UserDefined"
        # index 15 -> ACInputVoltageRange "1" -> UPS
        assert d["ac_input_voltage_range"] == "UPS"
        # index 16 -> OutputSourcePriority "2" -> SBU
        assert d["output_source_priority"] == "SBU"
        # index 17 -> ChargerSourcePriority "1" -> SolarFirst
        assert d["charger_source_priority"] == "SolarFirst"

    def test_raw_numeric_fields_passthrough(self):
        d = voltronic.decode_qpiri(self._QPIRI)
        assert d["bulk_charging_voltage"] == "28.4"
        assert d["float_charging_voltage"] == "27.2"

    def test_unknown_enum_value_passthrough(self):
        # An out-of-range battery_type code is returned verbatim, not crashed.
        bad = self._QPIRI.split()
        bad[12] = "9"
        d = voltronic.decode_qpiri(" ".join(bad))
        assert d["battery_type"] == "9"


class TestTransformQpiriValue:
    def test_parallel_mode_index_21(self):
        # ParallelMode "0" -> Master.
        assert voltronic.transform_qpiri_value(21, "0") == "Master"

    def test_non_enum_index_passthrough(self):
        assert voltronic.transform_qpiri_value(0, "230.0") == "230.0"

    def test_bad_value_passthrough(self):
        assert voltronic.transform_qpiri_value(12, "zzz") == "zzz"


class TestQpigs2:
    def test_fields(self):
        d = voltronic.decode_qpigs2("0012 0345 0067")
        assert d["pv_current"] == "0012"
        assert d["pv_voltage"] == "0345"
        assert d["pv_daily_energy"] == "0067"


class TestHexHelpers:
    def test_is_hex_string_true(self):
        assert voltronic.is_hex_string("28 32 33 39") is True

    def test_is_hex_string_odd_length_false(self):
        assert voltronic.is_hex_string("2 8 3") is False

    def test_is_hex_string_non_hex_false(self):
        assert voltronic.is_hex_string("239.3 50.0") is False

    def test_decode_ascii_response_strips_leading_paren(self):
        # "(QPIGS" in hex: 28=( 51=Q ...
        out = voltronic.decode_ascii_response("28 51 50 49 47 53")
        assert out == "QPIGS"

    def test_decode_direct_response_hex_dump_path(self):
        # A hex-dumped QPIGS reply routes through decode_ascii_response.
        # "(239.3 50.0" -> hex
        hexstr = " ".join(f"{b:02x}" for b in b"(239.3 50.0")
        d = voltronic.decode_direct_response("QMN", hexstr)
        # QMN just returns the model string; proves the hex path decoded.
        assert "239.3" in d["Model"]


class TestSimpleDecoders:
    def test_qmn(self):
        assert voltronic.decode_qmn(" MODEL-X ")["Model"] == "MODEL-X"

    def test_qvfw_strips_prefix(self):
        assert voltronic.decode_qvfw("VERFW:00123.45")["Firmware Version"] == "00123.45"

    def test_qbeqi_fields(self):
        d = voltronic.decode_qbeqi("1 060 030 050 27.2 0 120 0 000")
        assert d["equalization_function"] == "1"
        assert d["max_charging_current"] == "050"


class TestDecodeDirectResponseDispatch:
    def test_qpigs_routed(self):
        d = voltronic.decode_direct_response("QPIGS", "(" + _QPIGS)
        assert d["battery_discharge_current"] == "00022"

    def test_qpiws_routed(self):
        d = voltronic.decode_direct_response("QPIWS", "(" + "0" * 32)
        assert "overload" in d

    def test_nak(self):
        d = voltronic.decode_direct_response("QPIGS", "(NAKss")
        assert "error" in d

    def test_empty(self):
        d = voltronic.decode_direct_response("QPIGS", "")
        assert "error" in d
