"""Tests for the coordinator's retry/freeze policy (failure_tracker.py) —
the logic behind the 'dashboard strobe' fix."""
from custom_components.dess_monitor_local.coordinators.failure_tracker import (
    FailureOutcome,
    FailureTracker,
)


class TestCounting:
    def test_starts_at_zero(self):
        t = FailureTracker()
        assert t.count("dev", "QPIGS") == 0

    def test_on_failure_increments(self):
        t = FailureTracker()
        assert t.on_failure("dev", "QPIGS") == 1
        assert t.on_failure("dev", "QPIGS") == 2
        assert t.count("dev", "QPIGS") == 2

    def test_success_resets(self):
        t = FailureTracker()
        t.on_failure("dev", "QPIGS")
        t.on_failure("dev", "QPIGS")
        t.on_success("dev", "QPIGS")
        assert t.count("dev", "QPIGS") == 0

    def test_per_command_isolation(self):
        t = FailureTracker()
        t.on_failure("dev", "QPIGS")
        assert t.count("dev", "QPIRI") == 0

    def test_per_device_isolation(self):
        t = FailureTracker()
        t.on_failure("dev1", "QPIGS")
        assert t.count("dev2", "QPIGS") == 0


class TestResolve:
    def test_freeze_below_threshold_with_last_known(self):
        t = FailureTracker(max_consecutive=3)
        data, outcome = t.resolve(count=1, last_known={"battery_voltage": "27.2"})
        assert outcome is FailureOutcome.FREEZE
        assert data == {"battery_voltage": "27.2"}

    def test_no_data_below_threshold_without_last_known(self):
        t = FailureTracker(max_consecutive=3)
        data, outcome = t.resolve(count=2, last_known={})
        assert outcome is FailureOutcome.NO_DATA
        assert data == {}

    def test_none_last_known_is_no_data(self):
        t = FailureTracker(max_consecutive=3)
        data, outcome = t.resolve(count=1, last_known=None)
        assert outcome is FailureOutcome.NO_DATA
        assert data == {}

    def test_unavailable_at_threshold(self):
        t = FailureTracker(max_consecutive=3)
        data, outcome = t.resolve(count=3, last_known={"x": "1"})
        assert outcome is FailureOutcome.UNAVAILABLE
        assert data == {}

    def test_unavailable_above_threshold(self):
        t = FailureTracker(max_consecutive=3)
        _, outcome = t.resolve(count=10, last_known={"x": "1"})
        assert outcome is FailureOutcome.UNAVAILABLE


class TestEndToEndScenario:
    """Mirror the real coordinator flow: 2 freezes, then unavailable, then
    recovery resets everything."""

    def test_freeze_then_unavailable_then_recover(self):
        t = FailureTracker(max_consecutive=3)
        last = {"battery_voltage": "27.2"}

        # Poll 1 fails -> count 1 -> freeze.
        _, o1 = t.resolve(t.on_failure("d", "QPIGS"), last)
        # Poll 2 fails -> count 2 -> freeze.
        _, o2 = t.resolve(t.on_failure("d", "QPIGS"), last)
        # Poll 3 fails -> count 3 -> unavailable.
        _, o3 = t.resolve(t.on_failure("d", "QPIGS"), last)
        assert [o1, o2, o3] == [
            FailureOutcome.FREEZE,
            FailureOutcome.FREEZE,
            FailureOutcome.UNAVAILABLE,
        ]

        # Recovery: a good read resets, next failure freezes again.
        t.on_success("d", "QPIGS")
        assert t.count("d", "QPIGS") == 0
        _, o4 = t.resolve(t.on_failure("d", "QPIGS"), last)
        assert o4 is FailureOutcome.FREEZE
