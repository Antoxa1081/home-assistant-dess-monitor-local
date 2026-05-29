"""Tests for util.resolve_number_with_unit — the lenient numeric parser
used when projecting firmware strings carrying stray unit suffixes."""
from custom_components.dess_monitor_local import util


class TestResolveNumberWithUnit:
    def test_plain_float(self):
        assert util.resolve_number_with_unit("230.0") == 230.0

    def test_strips_unit_suffix(self):
        assert util.resolve_number_with_unit("230.0V") == 230.0
        assert util.resolve_number_with_unit("50 Hz") == 50.0

    def test_negative(self):
        assert util.resolve_number_with_unit("-5A") == -5.0

    def test_strips_trailing_underscore(self):
        # The QPIRI "solar_max_charging_power_auto_adjust" field is "1_".
        assert util.resolve_number_with_unit("1_") == 1.0

    def test_non_numeric_returns_original(self):
        assert util.resolve_number_with_unit("abc") == "abc"

    def test_empty_returns_original(self):
        assert util.resolve_number_with_unit("") == ""
