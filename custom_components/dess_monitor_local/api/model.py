"""Protocol-neutral device model.

The target data shape for every adapter (see
``wiki/Domain-Model-Refactor-Plan.md``): typed, semantic fields where ``None``
means the protocol can't measure it — no Voltronic-QPIGS-shaped fabrication.

Three buckets mirror the protocol command groups but with neutral types:
- ``metrics`` — live telemetry (QPIGS + QMOD analogue)
- ``ratings`` — device nameplate / config (QPIRI analogue)
- ``faults``  — warnings / faults (QPIWS + QFWS analogue)

Pure module — no Home Assistant imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .decoders.enums import (
    ACInputVoltageRange,
    BatteryType,
    ChargerSourcePriority,
    OperatingMode,
    OutputSourcePriority,
    ParallelMode,
    PI18BatteryPowerDirection,
    PI18DCACPowerDirection,
    PI18LinePowerDirection,
    PI18MPPTStatus,
)


class WarningKey(StrEnum):
    """Canonical, protocol-neutral warning identifiers.

    Adapters map their native faults onto this set. The value equals the
    legacy flag key, so ``WarningKey(key)`` maps a decoded flag straight in.
    The members below are the PI30 QPIWS set (the broadest); PI18/agent
    ``warn_*`` flags map onto the same members as those adapters migrate.
    """

    # PI30 QPIWS warnings/faults
    INVERTER_FAULT = "inverter_fault"
    BUS_OVER = "bus_over"
    BUS_UNDER = "bus_under"
    BUS_SOFT_FAIL = "bus_soft_fail"
    LINE_FAIL = "line_fail"
    OPV_SHORT = "opv_short"
    INVERTER_VOLTAGE_TOO_LOW = "inverter_voltage_too_low"
    INVERTER_VOLTAGE_TOO_HIGH = "inverter_voltage_too_high"
    OVER_TEMPERATURE = "over_temperature"
    FAN_LOCKED = "fan_locked"
    BATTERY_VOLTAGE_HIGH = "battery_voltage_high"
    BATTERY_LOW_ALARM = "battery_low_alarm"
    BATTERY_UNDER_SHUTDOWN = "battery_under_shutdown"
    OVERLOAD = "overload"
    EEPROM_FAULT = "eeprom_fault"
    INVERTER_OVER_CURRENT = "inverter_over_current"
    INVERTER_SOFT_FAIL = "inverter_soft_fail"
    SELF_TEST_FAIL = "self_test_fail"
    OP_DC_VOLTAGE_OVER = "op_dc_voltage_over"
    BATTERY_OPEN = "battery_open"
    CURRENT_SENSOR_FAIL = "current_sensor_fail"
    BATTERY_SHORT = "battery_short"
    POWER_LIMIT = "power_limit"
    PV_VOLTAGE_HIGH = "pv_voltage_high"
    MPPT_OVERLOAD_FAULT = "mppt_overload_fault"
    MPPT_OVERLOAD_WARNING = "mppt_overload_warning"
    BATTERY_TOO_LOW_TO_CHARGE = "battery_too_low_to_charge"

    @classmethod
    def from_flags(cls, flags: dict) -> set[WarningKey]:
        """Active canonical warnings from a decoded ``{flag: bool}`` dict.

        Accepts both the bare PI30 keys and ``warn_``-prefixed PI18/agent
        keys; unknown keys are ignored.
        """
        out: set[WarningKey] = set()
        for key, value in flags.items():
            if not value:
                continue
            name = key[5:] if key.startswith("warn_") else key
            member = cls._value2member_map_.get(name)
            if member is not None:
                out.add(member)
        return out


@dataclass(frozen=True)
class PvInput:
    """One PV / MPPT input. Any field is ``None`` when unavailable."""

    voltage: float | None = None
    current: float | None = None
    power: float | None = None


@dataclass
class DeviceStatus:
    """Parsed live status flags (PI30 status bits). ``None`` = not reported."""

    inverter_on: bool | None = None
    line_fail: bool | None = None
    battery_low: bool | None = None
    battery_high: bool | None = None
    bus_over: bool | None = None
    overload: bool | None = None
    charging_to_battery: bool | None = None
    charging_ac_active: bool | None = None
    charging_scc_active: bool | None = None


@dataclass
class Metrics:
    """Live telemetry — the QPIGS + QMOD analogue (changes every poll)."""

    mode: OperatingMode | None = None
    # grid / output
    grid_voltage: float | None = None
    grid_frequency: float | None = None
    grid_power: float | None = None
    ac_output_voltage: float | None = None
    ac_output_frequency: float | None = None
    ac_output_active_power: float | None = None
    ac_output_apparent_power: float | None = None
    load_percent: float | None = None
    bus_voltage: float | None = None
    # battery
    battery_voltage: float | None = None
    battery_current: float | None = None        # signed: + charge / − discharge
    battery_power: float | None = None          # signed
    battery_soc: float | None = None            # device/BMS %, None if unknown
    scc_battery_voltage: float | None = None
    scc2_battery_voltage: float | None = None
    # pv
    pv1: PvInput = field(default_factory=PvInput)
    pv2: PvInput | None = None                  # None when single-MPPT
    # temperatures
    temp_heatsink: float | None = None
    temp_dcdc: float | None = None
    temp_mppt1: float | None = None
    temp_mppt2: float | None = None
    # PI18 directions / sub-statuses
    battery_power_direction: PI18BatteryPowerDirection | None = None
    dcac_power_direction: PI18DCACPowerDirection | None = None
    line_power_direction: PI18LinePowerDirection | None = None
    mppt1_status: PI18MPPTStatus | None = None
    mppt2_status: PI18MPPTStatus | None = None
    # parsed PI30 status bits
    status: DeviceStatus = field(default_factory=DeviceStatus)

    @property
    def battery_charge_current(self) -> float | None:
        if self.battery_current is None:
            return None
        return max(0.0, self.battery_current)

    @property
    def battery_discharge_current(self) -> float | None:
        if self.battery_current is None:
            return None
        return max(0.0, -self.battery_current)


@dataclass
class Ratings:
    """Device nameplate / configuration — the QPIRI analogue. ``None`` when
    the protocol doesn't report a value."""

    grid_voltage: float | None = None
    input_current: float | None = None
    ac_output_voltage: float | None = None
    output_frequency: float | None = None
    output_current: float | None = None
    output_apparent_power: float | None = None
    output_active_power: float | None = None
    battery_voltage: float | None = None
    battery_capacity_ah: float | None = None
    bulk_charging_voltage: float | None = None
    float_charging_voltage: float | None = None
    low_battery_to_bypass_voltage: float | None = None
    shutdown_battery_voltage: float | None = None
    high_battery_to_battery_mode_voltage: float | None = None
    max_charging_current: float | None = None
    max_utility_charging_current: float | None = None
    battery_type: BatteryType | None = None
    ac_input_voltage_range: ACInputVoltageRange | None = None
    output_source_priority: OutputSourcePriority | None = None
    charger_source_priority: ChargerSourcePriority | None = None
    parallel_mode: ParallelMode | None = None
    parallel_max_number: int | None = None


@dataclass
class Faults:
    """Warnings / faults — the QPIWS + QFWS analogue."""

    warnings: set[WarningKey] = field(default_factory=set)
    fault_code: int | None = None
    warning_code: int | None = None
    fault_description: str | None = None

    @property
    def has_fault(self) -> bool:
        return bool(self.fault_code) or WarningKey.INVERTER_FAULT in self.warnings

    @property
    def has_warning(self) -> bool:
        return bool(self.warning_code) or bool(self.warnings)

    @property
    def any(self) -> bool:
        return bool(self.warnings) or bool(self.fault_code) or bool(self.warning_code)


@dataclass
class DeviceSnapshot:
    """A protocol-neutral point-in-time view of one inverter."""

    # identity
    model: str | None = None
    firmware: str | None = None
    serial: str | None = None
    # three semantic buckets
    metrics: Metrics = field(default_factory=Metrics)
    ratings: Ratings = field(default_factory=Ratings)
    faults: Faults = field(default_factory=Faults)
    # which optional field groups this device exposes (drives entity creation)
    capabilities: set[str] = field(default_factory=set)
    # diagnostic escape hatch — raw protocol values for troubleshooting
    raw: dict = field(default_factory=dict)
