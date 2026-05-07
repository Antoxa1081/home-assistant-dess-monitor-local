"""Plausibility bounds for inverter readings.

Used as defense-in-depth at the sensor layer: a frame that passes CRC but
contains corrupted field positions (e.g., from gateway-induced byte
interleaving when a second TCP client talks to the same Elfin bridge)
can still produce numeric values that decode cleanly. The bounds below
catch that residual class of failures before it contaminates energy
accumulators.

Limits are intentionally generous — the goal is to drop physically
impossible readings, not to second-guess the inverter on anything that
could plausibly be real.
"""
from __future__ import annotations


# Battery: 24 V LFP can sag to ~22 V under load; 96 V series banks can
# read into the 110+ V range during equalization. Keep a wide window so
# we never reject a real high-SOC reading.
MIN_BATTERY_VOLTAGE_V = 10.0
MAX_BATTERY_VOLTAGE_V = 120.0

# Continuous battery current for residential-class systems tops out around
# 200 A combined (e.g., 5 kW @ 24 V). Allow 2.5× headroom for short
# transient surges that the inverter may legitimately report.
MAX_BATTERY_CURRENT_A = 500.0

# Hardware ceiling on instantaneous power for any inverter family this
# integration speaks to. Largest single-phase Voltronic Axpert in this
# segment is ~12 kW; PI18 InfiniSolar tops out around 18 kW; SMG-II
# parallel banks can sum higher. 50 kW gives comfortable headroom while
# still rejecting megawatt-scale parser artifacts.
MAX_PLAUSIBLE_POWER_W = 50_000.0


def is_plausible_battery_current(value: float) -> bool:
    return 0.0 <= value <= MAX_BATTERY_CURRENT_A


def is_plausible_battery_voltage(value: float) -> bool:
    return MIN_BATTERY_VOLTAGE_V <= value <= MAX_BATTERY_VOLTAGE_V


def is_plausible_power(value: float) -> bool:
    return -MAX_PLAUSIBLE_POWER_W <= value <= MAX_PLAUSIBLE_POWER_W


def max_step_wh(elapsed_seconds: float) -> float:
    """Hard ceiling on the energy delta a single trapezoidal step may add.

    A floor of 100 Wh prevents false positives on the very first tick after
    startup, when ``elapsed_seconds`` can be near zero.
    """
    return max(MAX_PLAUSIBLE_POWER_W * elapsed_seconds / 3600.0, 100.0)
