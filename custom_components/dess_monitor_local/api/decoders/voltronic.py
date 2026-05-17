"""Voltronic Axpert (PI30) ASCII response decoders.

Inputs are the bare ASCII payloads returned by the inverter (with the
leading ``(`` and trailing CRC + CR already stripped — or not — see
:func:`decode_direct_response`). Outputs are flat dicts whose keys match
the sensor field names used throughout this integration.
"""
from __future__ import annotations

import re
from typing import Any

from .enums import (
    ACInputVoltageRange,
    BatteryType,
    ChargerSourcePriority,
    OperatingMode,
    OutputSourcePriority,
    ParallelMode,
)


def decode_ascii_response(hex_string: str) -> str:
    """Convert an "AA BB CC" hex string into an ASCII payload.

    Used when raw bytes are passed through as a hex-dump (e.g. some
    test fixtures) rather than as live ASCII.
    """
    hex_values = hex_string.strip().split()
    byte_values = bytes(int(b, 16) for b in hex_values)
    ascii_str = byte_values.decode("ascii", errors="ignore").strip()
    if ascii_str.startswith("("):
        ascii_str = ascii_str[1:]
    return ascii_str


_QPIGS_FIELDS = (
    "grid_voltage",
    "grid_frequency",
    "ac_output_voltage",
    "ac_output_frequency",
    "output_apparent_power",
    "output_active_power",
    "load_percent",
    "bus_voltage",
    "battery_voltage",
    "battery_charging_current",
    "battery_capacity",
    "inverter_heat_sink_temperature",
    "pv_input_current",
    "pv_input_voltage",
    "scc_battery_voltage",
    "battery_discharge_current",
    "device_status_bits_b7_b0",
    "battery_voltage_offset",
    "eeprom_version",
    "pv_charging_power",
    "device_status_bits_b10_b8",
    "reserved_a",
    "reserved_bb",
    "reserved_cccc",
)


def decode_qpigs(ascii_str: str) -> dict:
    return dict(zip(_QPIGS_FIELDS, ascii_str.split()))


_QPIGS2_FIELDS = (
    "pv_current",
    "pv_voltage",
    "pv_daily_energy",
)


def decode_qpigs2(ascii_str: str) -> dict:
    return dict(zip(_QPIGS2_FIELDS, ascii_str.split()))


# PI30 QPIWS response — 32-character bitstring (some firmware variants
# emit 36, with the trailing 4 bits reserved). Each bit ``ai`` flags a
# specific warning or fault condition. The mapping follows the Voltronic
# Axpert "QPIWS Warning Status" spec; "_reserved_*" entries are
# acknowledged but typically clear.
_QPIWS_FIELDS = (
    "_reserved_0",                    # a0
    "inverter_fault",                 # a1
    "bus_over",                       # a2
    "bus_under",                      # a3
    "bus_soft_fail",                  # a4
    "line_fail",                      # a5  (also surfaced via QPIGS b7_b0)
    "opv_short",                      # a6
    "inverter_voltage_too_low",       # a7
    "inverter_voltage_too_high",      # a8
    "over_temperature",               # a9
    "fan_locked",                     # a10
    "battery_voltage_high",           # a11
    "battery_low_alarm",              # a12
    "_reserved_13",                   # a13
    "battery_under_shutdown",         # a14
    "_reserved_15",                   # a15
    "overload",                       # a16
    "eeprom_fault",                   # a17
    "inverter_over_current",          # a18
    "inverter_soft_fail",             # a19
    "self_test_fail",                 # a20
    "op_dc_voltage_over",             # a21
    "battery_open",                   # a22
    "current_sensor_fail",            # a23
    "battery_short",                  # a24
    "power_limit",                    # a25
    "pv_voltage_high",                # a26
    "mppt_overload_fault",            # a27
    "mppt_overload_warning",          # a28
    "battery_too_low_to_charge",      # a29
    "_reserved_30",                   # a30
    "_reserved_31",                   # a31
)


def decode_qpiws(ascii_str: str) -> dict:
    """Decode PI30 QPIWS — Warning Status — into a flat dict of named
    boolean flags.

    Tolerant to:
      * leading/trailing whitespace
      * variable response length (32 vs 36 vs 28 bits across firmwares)
      * stray non-0/1 chars (e.g. CRC bleed-through, the same b10_b8
        artefact noted in TECH_DEBT.md)

    Bits beyond the known mapping are silently dropped; missing bits
    default to ``False``.
    """
    bits = "".join(c for c in ascii_str if c in "01")
    return {
        name: (bool(int(bits[i])) if i < len(bits) else False)
        for i, name in enumerate(_QPIWS_FIELDS)
    }


_QPIRI_FIELDS = (
    "rated_grid_voltage",
    "rated_input_current",
    "rated_ac_output_voltage",
    "rated_output_frequency",
    "rated_output_current",
    "rated_output_apparent_power",
    "rated_output_active_power",
    "rated_battery_voltage",
    "low_battery_to_ac_bypass_voltage",
    "shut_down_battery_voltage",
    "bulk_charging_voltage",
    "float_charging_voltage",
    "battery_type",
    "max_utility_charging_current",
    "max_charging_current",
    "ac_input_voltage_range",
    "output_source_priority",
    "charger_source_priority",
    "parallel_max_number",
    "reserved_uu",
    "reserved_v",
    "parallel_mode",
    "high_battery_voltage_to_battery_mode",
    "solar_work_condition_in_parallel",
    "solar_max_charging_power_auto_adjust",
    "rated_battery_capacity",
    "reserved_b",
    "reserved_ccc",
)


def transform_qpiri_value(index: int, value: str) -> str:
    try:
        match index:
            case 12:
                return BatteryType(value).name
            case 15:
                return ACInputVoltageRange(value).name
            case 16:
                return OutputSourcePriority(value).name
            case 17:
                return ChargerSourcePriority(value).name
            case 21:
                return ParallelMode(value).name
            case _:
                return value
    except ValueError:
        return value


def decode_qpiri(ascii_str: str) -> dict:
    values = ascii_str.split()
    return {
        name: transform_qpiri_value(i, value)
        for i, (name, value) in enumerate(zip(_QPIRI_FIELDS, values))
    }


def decode_qmod(ascii_str: str) -> dict:
    code = ascii_str.strip()[:1]
    try:
        mode: Any = OperatingMode(code)
    except ValueError:
        mode = "Unknown"
    return {"operating_mode": mode}


def decode_qmn(ascii_str: str) -> dict:
    return {"Model": ascii_str.strip()}


def decode_qid(ascii_str: str) -> dict:
    return {"Device ID": ascii_str.strip()}


def decode_qflag(ascii_str: str) -> dict:
    return {"Enabled/Disabled Flags": ascii_str.strip()}


def decode_qvfw(ascii_str: str) -> dict:
    return {"Firmware Version": ascii_str.replace("VERFW:", "").strip()}


def decode_qbeqi(ascii_str: str) -> dict:
    fields = (
        "equalization_function",
        "equalization_time",
        "interval_days",
        "max_charging_current",
        "float_voltage",
        "reserved_1",
        "equalization_timeout",
        "immediate_activation_flag",
        "elapsed_time",
    )
    return dict(zip(fields, ascii_str.split()))


def is_hex_string(s: str) -> bool:
    s = s.strip().replace(" ", "")
    return bool(re.fullmatch(r"[0-9A-Fa-f]+", s)) and len(s) % 2 == 0


def decode_direct_response(command: str, input_str: str) -> dict:
    """Parse a Voltronic ASCII reply into a structured dict.

    Accepts both the raw "AA BB CC" hex dump form and the live ASCII
    form, then dispatches to the per-command decoder.
    """
    if not input_str:
        return {"error": "empty response"}
    if input_str == "null":
        return {"error": "null response received. Command not accepted."}

    if is_hex_string(input_str):
        ascii_str = decode_ascii_response(input_str)
    else:
        ascii_str = input_str.strip()

    ascii_str = (
        ascii_str.strip()
        .replace("(", "")
        .replace(")", "")
        .replace("\r", "")
        .replace("\n", "")
    )

    if ascii_str.startswith("NAK") or "NAK" in ascii_str:
        return {"error": "NAK response received. Command not accepted."}

    match command.upper():
        case "QPIGS":
            return decode_qpigs(ascii_str)
        case "QPIGS2":
            return decode_qpigs2(ascii_str)
        case "QPIRI":
            return decode_qpiri(ascii_str)
        case "QMOD":
            return decode_qmod(ascii_str)
        case "QMN":
            return decode_qmn(ascii_str)
        case "QID" | "QSID":
            return decode_qid(ascii_str)
        case "QFLAG":
            return decode_qflag(ascii_str)
        case "QVFW":
            return decode_qvfw(ascii_str)
        case "QBEQI":
            return decode_qbeqi(ascii_str)
        case "QPIWS":
            return decode_qpiws(ascii_str)
        case _:
            return {"Raw": ascii_str}


# Hex command table — kept for diagnostic / probing utilities.
direct_commands = {
    "QPIGS": "51 50 49 47 53 B7 A9 0D",
    "QPIGS2": "51 50 49 47 53 32 2B 8A 0D",
    "QPIRI": "51 50 49 52 49 F8 54 0D",
    "QMOD": "51 4D 4F 44 49 C1 0D",
    "QPIWS": "51 50 49 57 53 B4 DA 0D",
    "QVFW": "51 56 46 57 62 99 0D",
    "QMCHGCR": "51 4D 43 48 47 43 52 D8 55 0D",
    "QMUCHGCR": "51 4D 55 43 48 47 43 52 26 34 0D",
    "QFLAG": "51 46 4C 41 47 98 74 0D",
    "QSID": "51 53 49 44 BB 05 0D",
    "QID": "51 49 44 D6 EA 0D",
    "QMN": "51 4D 4E BB 64 0D",
    "QBEQI": "51 42 45 51 49 31 6B 0D",
}


def get_command_hex(command_name: str) -> str:
    return direct_commands.get(command_name.upper(), "Unknown command")


def get_command_name_by_hex(hex_string: str) -> str:
    normalized_input = hex_string.strip().upper().replace("  ", " ")
    for name, hex_cmd in direct_commands.items():
        if normalized_input == hex_cmd.upper():
            return name
    return "Unknown HEX command"
