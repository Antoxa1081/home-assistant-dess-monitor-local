"""Tests for plausibility bounds (sanity.py) — the defense-in-depth layer
that stopped the 1.8 MWh megawatt-spike incident from poisoning the
energy accumulators."""
import pytest

from custom_components.dess_monitor_local.helpers import sanity


class TestBatteryCurrent:
    @pytest.mark.parametrize("v", [0.0, 1.0, 50.0, 500.0])
    def test_plausible(self, v):
        assert sanity.is_plausible_battery_current(v) is True

    @pytest.mark.parametrize("v", [-0.1, 500.1, 10_010_110.0, 99999.0])
    def test_implausible(self, v):
        assert sanity.is_plausible_battery_current(v) is False

    def test_zero_is_allowed(self):
        # All-zeros "no data" frames must pass the bound (they're filtered
        # elsewhere, not here).
        assert sanity.is_plausible_battery_current(0.0) is True


class TestBatteryVoltage:
    @pytest.mark.parametrize("v", [10.0, 26.3, 51.2, 120.0])
    def test_plausible(self, v):
        assert sanity.is_plausible_battery_voltage(v) is True

    @pytest.mark.parametrize("v", [0.0, 9.9, 120.1, 5_300_000.0])
    def test_implausible(self, v):
        assert sanity.is_plausible_battery_voltage(v) is False


class TestPower:
    @pytest.mark.parametrize("v", [0.0, 5000.0, -5000.0, 50_000.0, -50_000.0])
    def test_plausible(self, v):
        assert sanity.is_plausible_power(v) is True

    @pytest.mark.parametrize("v", [50_000.1, -50_000.1, 1_800_000.0, -260_000_000.0])
    def test_implausible(self, v):
        # The 260 MW value is exactly the field-shift artifact from issue #5.
        assert sanity.is_plausible_power(v) is False

    def test_negative_power_allowed(self):
        # Discharge is negative power and must be accepted.
        assert sanity.is_plausible_power(-341.0) is True


class TestMaxStepWh:
    def test_floor_at_startup(self):
        # Near-zero elapsed must still allow a 100 Wh floor so the first
        # tick after startup isn't falsely rejected.
        assert sanity.max_step_wh(0.0) == 100.0

    def test_scales_with_time(self):
        # 50 kW ceiling over one hour = 50_000 Wh.
        assert sanity.max_step_wh(3600.0) == pytest.approx(50_000.0)

    def test_blocks_megawatt_step(self):
        # The poisoned 1.46 MWh step over a 40 s gap is far above ceiling.
        ceiling = sanity.max_step_wh(40.0)
        assert 1_460_000.0 > ceiling
        assert ceiling == pytest.approx(50_000.0 * 40.0 / 3600.0)
