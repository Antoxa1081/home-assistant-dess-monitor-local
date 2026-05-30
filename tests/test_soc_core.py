"""Tests for the pure SoC estimator (soc_core.py).

This is the regression suite for the vSoC bug history: Coulomb counting,
efficiency, float deadband, snap-to-100%, the integral-windup fix
(comment 4528140344 / 4554608022 in issue #5), timestamp de-spam, BMS
mirror, and capacity rescaling.
"""
from datetime import UTC, datetime

import pytest

from custom_components.dess_monitor_local.helpers.soc_core import (
    BATTERY_MODE_LEAD_ACID,
    BATTERY_MODE_LI_BMS,
    BATTERY_MODE_LI_VOLTAGE,
    SYNC_DEBOUNCE_TICKS,
    SocEstimator,
)

WALL = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)


def make(capacity=200.0, mode=BATTERY_MODE_LI_VOLTAGE, deadband=True):
    est = SocEstimator()
    est.set_mode(mode)
    est.set_deadband(enabled=deadband, window=0.5, noise_floor=1.5)
    est.set_capacity(capacity)
    return est


class TestUnconfigured:
    def test_no_capacity_returns_none(self):
        est = SocEstimator()
        out = est.update(
            signed_current_a=10, voltage=27.0, now=0.0,
            sync_voltage=28.0, floating_voltage=27.2,
        )
        assert out is None

    def test_no_sync_voltage_returns_none(self):
        est = make()
        out = est.update(
            signed_current_a=10, voltage=27.0, now=0.0,
            sync_voltage=None, floating_voltage=None,
        )
        assert out is None


class TestCapacityRescale:
    def test_first_capacity_anchors_to_soc(self):
        est = SocEstimator()
        est.soc_percent = 50.0  # as if restored
        est.set_capacity(200.0)
        assert est.accumulated_charge_ah == pytest.approx(100.0)

    def test_first_capacity_no_soc_defaults_full(self):
        est = SocEstimator()
        est.set_capacity(200.0)
        assert est.accumulated_charge_ah == pytest.approx(200.0)
        assert est.soc_percent == 100.0

    def test_capacity_change_preserves_percent(self):
        est = make(capacity=100.0)
        est.accumulated_charge_ah = 80.0  # 80%
        est.set_capacity(200.0)
        # 80% must be preserved -> 160 Ah, soc 80%.
        assert est.accumulated_charge_ah == pytest.approx(160.0)
        assert est.soc_percent == pytest.approx(80.0)

    def test_zero_capacity_marks_unconfigured(self):
        est = make()
        est.set_capacity(0.0)
        assert est.capacity_ah is None

    def test_capacity_change_rounds_soc(self):
        # A capacity edit must publish a 2-dp SoC, not 14-digit fp noise.
        est = make(capacity=200.0)
        est.accumulated_charge_ah = 199.99999999999997
        est.set_capacity(200.0001)
        assert est.soc_percent == round(est.soc_percent, 2)
        assert est.soc_percent == 100.0


class TestCoulombCounting:
    def test_discharge_drops_soc(self):
        est = make(capacity=100.0)
        est.accumulated_charge_ah = 100.0
        est.soc_percent = 100.0
        # Prime the trapezoid (first tick sets baseline, no integration).
        est.update(signed_current_a=-10, voltage=25.0, now=0.0,
                   sync_voltage=28.0, floating_voltage=27.2)
        # 1 hour at -10 A on a 100 Ah bank = -10 Ah = -10%.
        out = est.update(signed_current_a=-10, voltage=25.0, now=3600.0,
                         sync_voltage=28.0, floating_voltage=27.2)
        assert out == pytest.approx(90.0, abs=0.05)

    def test_charge_raises_soc(self):
        est = make(capacity=100.0)
        est.accumulated_charge_ah = 50.0
        est.soc_percent = 50.0
        est.update(signed_current_a=10, voltage=25.0, now=0.0,
                   sync_voltage=28.0, floating_voltage=27.2)
        out = est.update(signed_current_a=10, voltage=25.0, now=3600.0,
                         sync_voltage=28.0, floating_voltage=27.2)
        # +10 Ah * 0.99 charge_eff = +9.9% -> ~59.9%.
        assert out == pytest.approx(59.9, abs=0.1)

    def test_lead_acid_charge_efficiency_lower(self):
        est = make(capacity=100.0, mode=BATTERY_MODE_LEAD_ACID)
        est.accumulated_charge_ah = 50.0
        est.soc_percent = 50.0
        est.update(signed_current_a=10, voltage=25.0, now=0.0,
                   sync_voltage=28.0, floating_voltage=27.2)
        out = est.update(signed_current_a=10, voltage=25.0, now=3600.0,
                         sync_voltage=28.0, floating_voltage=27.2)
        # Lead charge_eff 0.90 -> +9% -> ~59%.
        assert out == pytest.approx(59.0, abs=0.1)


class TestFloatDeadband:
    def test_deadband_suppresses_quantisation_noise(self):
        est = make(capacity=200.0)
        est.accumulated_charge_ah = 100.0  # 50%
        est.soc_percent = 50.0
        # Voltage just BELOW the snap threshold but within the deadband
        # window of float (27.0 vs float 27.2, window 0.5) so we isolate
        # the deadband from the snap-to-100% path (sync 28.0 > voltage).
        # Quantised 1 A "noise" must NOT integrate -> SoC holds.
        before = est.accumulated_charge_ah
        for i in range(1, 10):
            est.update(signed_current_a=-1.0, voltage=27.0, now=float(i * 14),
                       sync_voltage=28.0, floating_voltage=27.2)
        assert est.accumulated_charge_ah == pytest.approx(before)

    def test_deadband_off_integrates_noise(self):
        est = make(capacity=200.0, deadband=False)
        est.accumulated_charge_ah = 100.0
        est.soc_percent = 50.0
        # Voltage below the snap threshold so no snap interferes; with the
        # deadband off the 1 A noise integrates and charge drops.
        est.update(signed_current_a=-1.0, voltage=25.0, now=0.0,
                   sync_voltage=28.0, floating_voltage=27.2)
        est.update(signed_current_a=-1.0, voltage=25.0, now=3600.0,
                   sync_voltage=28.0, floating_voltage=27.2)
        assert est.accumulated_charge_ah < 100.0


class TestSnapToFull:
    def test_snaps_at_float_with_zero_current(self):
        # The abs()-tail fix: a full battery sitting in float at 0 A must snap.
        est = make(capacity=200.0)
        est.accumulated_charge_ah = 190.0  # 95%, slightly under
        est.soc_percent = 95.0
        out = None
        for i in range(SYNC_DEBOUNCE_TICKS + 1):
            out = est.update(signed_current_a=0.0, voltage=27.2, now=float(i * 14),
                             sync_voltage=27.2, floating_voltage=27.2,
                             wall_now=WALL)
        assert out == 100.0
        assert est.accumulated_charge_ah == pytest.approx(200.0)

    def test_timestamp_pinned_to_crossing_tick(self):
        est = make(capacity=200.0)
        est.accumulated_charge_ah = 200.0
        est.soc_percent = 100.0
        stamps = []
        for i in range(20):
            wall = datetime(2026, 5, 24, 12, 0, i, tzinfo=UTC)
            est.update(signed_current_a=0.0, voltage=27.2, now=float(i),
                       sync_voltage=27.2, floating_voltage=27.2, wall_now=wall)
            stamps.append(est.last_sync_at)
        # Timestamp set exactly once (on the crossing tick) then frozen —
        # never re-stamped during the long float hold -> no recorder spam.
        distinct = {s for s in stamps if s is not None}
        assert len(distinct) == 1

    def test_does_not_snap_under_real_discharge(self):
        est = make(capacity=200.0)
        est.accumulated_charge_ah = 100.0  # 50%
        est.soc_percent = 50.0
        out = None
        for i in range(10):
            # Voltage high but heavy discharge (-40 A, tail is 10 A).
            out = est.update(signed_current_a=-40.0, voltage=27.2, now=float(i * 14),
                             sync_voltage=27.2, floating_voltage=27.2)
        assert out < 100.0


class TestIntegralWindupFix:
    """Regression for the SoC-frozen-at-100%-during-discharge bug."""

    def test_counter_grows_during_float(self):
        est = make(capacity=200.0)
        est.accumulated_charge_ah = 200.0
        est.soc_percent = 100.0
        for i in range(100):
            est.update(signed_current_a=0.0, voltage=27.2, now=float(i),
                       sync_voltage=27.2, floating_voltage=27.2, wall_now=WALL)
        # Unbounded growth gives inertia.
        assert est.at_sync_ticks >= 100

    def test_real_discharge_hard_resets_counter_in_one_tick(self):
        est = make(capacity=200.0)
        est.accumulated_charge_ah = 200.0
        est.soc_percent = 100.0
        # Wind the counter up over a long float.
        for i in range(100):
            est.update(signed_current_a=0.0, voltage=27.2, now=float(i),
                       sync_voltage=27.2, floating_voltage=27.2, wall_now=WALL)
        assert est.at_sync_ticks >= 100
        # One heavy-discharge tick must hard-reset to 0 immediately
        # (the windup bug took ~10 h of decay to do this).
        est.update(signed_current_a=-40.0, voltage=27.2, now=1000.0,
                   sync_voltage=27.2, floating_voltage=27.2)
        assert est.at_sync_ticks == 0

    def test_soc_unfreezes_and_drops_after_windup(self):
        est = make(capacity=200.0)
        est.accumulated_charge_ah = 200.0
        est.soc_percent = 100.0
        for i in range(3600):  # 1 h of float at 1 s ticks -> huge windup
            est.update(signed_current_a=0.0, voltage=27.2, now=float(i),
                       sync_voltage=27.2, floating_voltage=27.2, wall_now=WALL)
        # Now sustained heavy discharge; SoC must start falling within a
        # couple of ticks, NOT stay pinned at 100 for hours.
        t = 3600.0
        last = 100.0
        for _ in range(5):
            t += 14.0
            last = est.update(signed_current_a=-40.0, voltage=26.0, now=t,
                              sync_voltage=27.2, floating_voltage=27.2)
        assert last < 100.0


class TestBmsMirror:
    def test_mirrors_bms_soc(self):
        est = make(mode=BATTERY_MODE_LI_BMS)
        out = est.update(signed_current_a=-14, voltage=27.3, now=0.0,
                         sync_voltage=28.0, floating_voltage=27.2,
                         bms_soc=73.0)
        assert out == 73.0
        assert est.at_sync_ticks == 0

    def test_falls_through_when_bms_missing(self):
        est = make(mode=BATTERY_MODE_LI_BMS)
        est.accumulated_charge_ah = 100.0  # 50% of 200
        est.soc_percent = 50.0
        out = est.update(signed_current_a=0.0, voltage=25.0, now=0.0,
                         sync_voltage=28.0, floating_voltage=27.2,
                         bms_soc=None)
        # No BMS reading -> integrator path -> SoC reflects accumulated.
        assert out == pytest.approx(50.0, abs=0.5)


class TestSetMode:
    def test_mode_change_resets_debounce(self):
        est = make()
        est.at_sync_ticks = 50
        est.set_mode(BATTERY_MODE_LEAD_ACID)
        assert est.at_sync_ticks == 0

    def test_same_mode_keeps_debounce(self):
        est = make(mode=BATTERY_MODE_LI_VOLTAGE)
        est.at_sync_ticks = 50
        est.set_mode(BATTERY_MODE_LI_VOLTAGE)
        assert est.at_sync_ticks == 50

    def test_unknown_mode_ignored(self):
        est = make()
        est.set_mode("nonsense")
        assert est.mode == BATTERY_MODE_LI_VOLTAGE
