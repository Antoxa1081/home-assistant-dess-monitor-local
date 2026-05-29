"""Tests for device-status bit parsing (api/decoders/enums.py)."""
from custom_components.dess_monitor_local.api.decoders import enums


class TestExtractBits:
    def test_exact_length(self):
        assert enums._extract_bits("10110010", 8) == "10110010"

    def test_pads_short_input(self):
        # Right-justified, zero-padded to count.
        assert enums._extract_bits("101", 8) == "00000101"

    def test_strips_non_binary_chars(self):
        # The "110s" CRC-bleed case from TECH_DEBT: trailing 's' dropped.
        assert enums._extract_bits("110s", 3) == "110"

    def test_truncates_to_count(self):
        assert enums._extract_bits("1111111111", 3) == "111"


class TestParseB7B0:
    def test_all_clear(self):
        d = enums.parse_device_status_bits_b7_b0("00000000")
        assert d["fault"] is False
        assert d["inverter_on"] is False

    def test_fault_and_inverter_on(self):
        # b7 = fault (MSB), b0 = inverter_on (LSB).
        d = enums.parse_device_status_bits_b7_b0("10000001")
        assert d["fault"] is True
        assert d["inverter_on"] is True
        assert d["line_fail"] is False

    def test_line_fail_bit(self):
        # b4 = line_fail -> "00010000".
        d = enums.parse_device_status_bits_b7_b0("00010000")
        assert d["line_fail"] is True

    def test_raw_preserved(self):
        d = enums.parse_device_status_bits_b7_b0("00010110")
        assert d["_raw_b7_b0"] == "00010110"


class TestParseB10B8:
    def test_crc_bleed_tolerated(self):
        # "110s" — trailing CRC char must not crash int(bits, 2).
        d = enums.parse_device_status_bits_b10_b8("110s")
        assert d["_raw_b10_b8"] == "110"
        assert d["charging_to_battery"] is True

    def test_scc_active(self):
        # b8 = scc (LSB of the 3-bit field) -> "001".
        d = enums.parse_device_status_bits_b10_b8("001")
        assert d["charging_scc_active"] is True
        assert d["charging_to_battery"] is False
