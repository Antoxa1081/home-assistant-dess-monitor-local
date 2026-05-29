"""Pure, Home-Assistant-free State-of-Charge estimator.

This is the algorithmic core extracted from ``DirectBatteryStateOfChargeSensor``
so it can be unit-tested without importing Home Assistant. It owns the
Coulomb-counter state and all the tricky behaviours that have been the
source of repeated bugs:

* Coulomb (Ah) integration with split charge/discharge efficiency.
* Float-mode deadband that cancels integer-quantised current noise.
* Snap-to-100% with an asymmetric debounce counter (grows in float for
  inertia, hard-resets on real discharge to avoid integral windup).
* Last-sync timestamp pinned to the exact crossing tick (no recorder spam).
* BMS-mirror mode.
* Proportional re-scaling when the user changes battery capacity.

Time is injected (``now`` monotonic seconds, ``wall_now`` datetime) so
tests are fully deterministic. The HA entity is a thin adapter that
feeds resolved inputs in and copies ``soc_percent`` out.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

BATTERY_MODE_LI_VOLTAGE = "Lithium (Voltage)"
BATTERY_MODE_LI_BMS = "Lithium (BMS)"
BATTERY_MODE_LEAD_ACID = "Lead-acid"


@dataclass(frozen=True)
class ChemistryParams:
    charge_eff: float
    discharge_eff: float
    tail_c_rate: float


# Coulombic efficiency (charge stays in the cells) — see the entity module
# for the physics rationale. LFP nearly lossless; lead loses real Coulombs
# to gassing / self-discharge. ``tail_c_rate`` is the absorption-tail
# current threshold as a fraction of nominal capacity.
CHEMISTRY_PARAMS: dict[str, ChemistryParams] = {
    BATTERY_MODE_LI_VOLTAGE: ChemistryParams(0.99, 1.0, 0.05),
    BATTERY_MODE_LEAD_ACID: ChemistryParams(0.90, 0.95, 0.02),
    BATTERY_MODE_LI_BMS: ChemistryParams(1.0, 1.0, 0.05),
}

DEFAULT_FLOAT_VOLTAGE_WINDOW_V = 0.5
DEFAULT_FLOAT_NOISE_FLOOR_A = 1.5
SYNC_DEBOUNCE_TICKS = 3


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class SocEstimator:
    """Stateful SoC estimator. One instance per battery."""

    def __init__(self) -> None:
        # ---- configuration (pushed in by the adapter) ----
        self.capacity_ah: float | None = None
        self.mode: str = BATTERY_MODE_LI_VOLTAGE
        self.deadband_enabled: bool = True
        self.float_voltage_window: float = DEFAULT_FLOAT_VOLTAGE_WINDOW_V
        self.float_noise_floor: float = DEFAULT_FLOAT_NOISE_FLOOR_A
        # ---- integrator state ----
        self.accumulated_charge_ah: float = 0.0
        self.at_sync_ticks: int = 0
        self.prev_current_a: float | None = None
        self.prev_effective_current_a: float | None = None
        self.prev_ts: float | None = None
        self.last_sync_at: datetime | None = None
        self.soc_percent: float | None = None

    # ------------------------------------------------------------------
    # Configuration mutators
    # ------------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        """Switch chemistry preset. Resets the snap debounce on an actual
        change so stale "near sync" state doesn't carry across chemistries."""
        if mode not in CHEMISTRY_PARAMS:
            return
        if mode != self.mode:
            self.at_sync_ticks = 0
        self.mode = mode

    def set_deadband(self, *, enabled: bool, window: float, noise_floor: float) -> None:
        self.deadband_enabled = enabled
        self.float_voltage_window = max(0.0, window)
        self.float_noise_floor = max(0.0, noise_floor)

    def set_capacity(self, value: float | None) -> None:
        """Set nominal capacity (Ah). Preserves the displayed SoC% by
        proportionally rescaling the accumulated charge, so changing the
        capacity number doesn't make SoC jump. ``None``/≤0 marks the
        estimator as unconfigured (SoC unavailable)."""
        if value is None or value <= 0:
            self.capacity_ah = None
            return

        old = self.capacity_ah
        self.capacity_ah = value

        if old is None:
            # First time capacity becomes known (incl. after restart):
            # anchor accumulated charge to whatever SoC% we currently hold.
            if self.soc_percent is not None:
                frac = _clamp(self.soc_percent / 100.0, 0.0, 1.0)
                self.accumulated_charge_ah = frac * value
            else:
                self.accumulated_charge_ah = value
                self.soc_percent = 100.0
        else:
            # User edited capacity — keep the SoC% constant.
            if old > 0:
                frac = self.accumulated_charge_ah / old
            elif self.soc_percent is not None:
                frac = self.soc_percent / 100.0
            else:
                frac = 1.0
            frac = _clamp(frac, 0.0, 1.0)
            self.accumulated_charge_ah = frac * value
            self.soc_percent = frac * 100.0

    def restore(
        self,
        *,
        soc_percent: float | None,
        accumulated_charge_ah: float,
        last_sync_at: datetime | None,
    ) -> None:
        """Seed state from persisted extra-data after a restart."""
        self.soc_percent = soc_percent
        self.accumulated_charge_ah = accumulated_charge_ah
        self.last_sync_at = last_sync_at

    # ------------------------------------------------------------------
    # The integrator
    # ------------------------------------------------------------------
    def update(
        self,
        *,
        signed_current_a: float,
        voltage: float,
        now: float,
        sync_voltage: float | None,
        floating_voltage: float | None,
        bms_soc: float | None = None,
        wall_now: datetime | None = None,
    ) -> float | None:
        """Advance the estimator one tick. Returns the new SoC% (or None
        when unconfigured / no sync reference). All time inputs injected.

        Args:
            signed_current_a: + charging, − discharging (A).
            voltage: battery terminal voltage.
            now: monotonic-ish seconds, used for the trapezoid Δt.
            sync_voltage: snap-to-100% voltage threshold (None → unavailable).
            floating_voltage: inverter float setpoint (or None).
            bms_soc: BMS-reported SoC% (only used in BMS mode).
            wall_now: wall-clock stamp for ``last_sync_at`` (or None).
        """
        if self.capacity_ah is None or self.capacity_ah <= 0:
            self.soc_percent = None
            return None

        max_capacity_ah = self.capacity_ah
        params = CHEMISTRY_PARAMS[self.mode]

        # ---- BMS mirror ----
        if self.mode == BATTERY_MODE_LI_BMS and bms_soc is not None:
            self.accumulated_charge_ah = (bms_soc / 100.0) * max_capacity_ah
            self.prev_current_a = signed_current_a
            self.prev_effective_current_a = None
            self.prev_ts = now
            self.at_sync_ticks = 0
            self.soc_percent = round(bms_soc, 2)
            return self.soc_percent

        # ---- voltage-based branch ----
        if sync_voltage is None:
            self.soc_percent = None
            return None

        elapsed_seconds = (now - self.prev_ts) if self.prev_ts is not None else 0.0

        in_float_deadband = (
            self.deadband_enabled
            and floating_voltage is not None
            and abs(voltage - floating_voltage) < self.float_voltage_window
            and abs(signed_current_a) <= self.float_noise_floor
        )

        if signed_current_a >= 0:
            effective_current_a = signed_current_a * params.charge_eff
        else:
            effective_current_a = signed_current_a / params.discharge_eff

        if in_float_deadband:
            # Idle tick — drop quantisation noise, reset the trapezoid baseline.
            self.prev_current_a = signed_current_a
            self.prev_effective_current_a = None
            self.prev_ts = now
        else:
            if self.prev_effective_current_a is not None:
                charge_increment = (elapsed_seconds / 3600.0) * (
                    self.prev_effective_current_a + effective_current_a
                ) / 2.0
                self.accumulated_charge_ah += charge_increment
            self.prev_current_a = signed_current_a
            self.prev_effective_current_a = effective_current_a
            self.prev_ts = now

        # ---- snap-to-100% (asymmetric debounce) ----
        tail_current_a = max_capacity_ah * params.tail_c_rate
        v_full_threshold = (
            min(sync_voltage, floating_voltage)
            if floating_voltage is not None and floating_voltage > 0
            else sync_voltage
        )
        at_voltage = voltage >= v_full_threshold
        at_tail = abs(signed_current_a) <= tail_current_a

        if at_voltage and at_tail:
            self.at_sync_ticks += 1
        elif signed_current_a < -tail_current_a:
            self.at_sync_ticks = 0
        else:
            self.at_sync_ticks = max(0, self.at_sync_ticks - 1)

        if self.at_sync_ticks >= SYNC_DEBOUNCE_TICKS:
            soc_percent = 100.0
            self.accumulated_charge_ah = max_capacity_ah
            if self.at_sync_ticks == SYNC_DEBOUNCE_TICKS and wall_now is not None:
                self.last_sync_at = wall_now
        else:
            self.accumulated_charge_ah = _clamp(
                self.accumulated_charge_ah, 0.0, max_capacity_ah
            )
            soc_percent = (self.accumulated_charge_ah / max_capacity_ah) * 100.0

        self.soc_percent = round(_clamp(soc_percent, 0.0, 100.0), 2)
        return self.soc_percent
