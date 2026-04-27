"""PI18 / InfiniSolar-V codec.

Wire format::

    Request   ^P<nnn><body><CRC><CR>
    Response  ^D<nnn><payload><CRC><CR>      (success)
              ^1<CRC><CR>                      (set command ACK)
              ^0<CRC><CR>                      (set command NAK)

``nnn`` is the 3-digit decimal length of everything after ``^Pnnn`` /
``^Dnnn`` (body + 2-byte CRC + 1-byte CR). CRC is XMODEM CRC-16.

Decoders project PI18 fields onto the same Axpert-shaped dicts produced
by :mod:`.voltronic`, so sensors don't notice which dialect supplied the
data. PI18-only fields (PV2 voltage, MPPT temps, direction flags) are
recognised but dropped — exposing them would mean expanding the sensor
schema, not this adapter.

Source spec: ``PI18_InfiniSolar-V-protocol-20170926``.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..crc import crc16_xmodem_bytes
from .enums import (
    ACInputVoltageRange,
    BatteryType,
    ChargerSourcePriority,
    OperatingMode,
    OutputSourcePriority,
)


# Logical command name (shared across protocols) → PI18 native command body.
LOGICAL_TO_NATIVE: Mapping[str, str] = {
    "QPIGS": "GS",
    "QPIRI": "PIRI",
    "QMOD": "MOD",
    "QPI": "PI",
    "QID": "ID",
    "QVFW": "VFW",
    "QFWS": "FWS",
    "QFLAG": "FLAG",
    "QDI": "DI",
    "QMCHGCR": "MCHGCR",
    "QMUCHGCR": "MUCHGCR",
}


def _safe_int(token: str, default: int = 0) -> int:
    try:
        return int(token)
    except (TypeError, ValueError):
        return default


def _strip_pi18_frame(raw: bytes) -> bytes:
    """Drop the ``^Dnnn`` header, trailing ``\\r``, and 2-byte CRC.

    The CRC is *not* re-validated here. Transports that talk directly to
    a device should validate the frame before handing the bytes off; for
    relayed/buffered sources the CRC tends to get rewritten and a strict
    check produces false negatives.
    """
    if not raw:
        return raw
    payload = raw
    cr = payload.find(b"\r")
    if cr != -1:
        payload = payload[:cr]
    if len(payload) >= 2:
        payload = payload[:-2]
    if payload.startswith(b"^D") and len(payload) >= 5:
        payload = payload[5:]
    elif payload.startswith(b"("):
        payload = payload[1:]
    return payload


def build_request_frame(command: str) -> bytes:
    """Build the PI18 ``^P<nnn>...<CRC><CR>`` request envelope."""
    cmd = command.upper()
    body = LOGICAL_TO_NATIVE.get(cmd, cmd)
    body_bytes = body.encode("ascii")
    # nnn covers body + 2-byte CRC + 1-byte CR.
    length = len(body_bytes) + 3
    head = f"^P{length:03d}".encode("ascii") + body_bytes
    return head + crc16_xmodem_bytes(head) + b"\r"


# ---------------------------------------------------------------------------
# GS — General status. Maps onto the ``qpigs`` section.
# ---------------------------------------------------------------------------


_GS_FIELDS = (
    "grid_voltage",                    # 0  AAAA  0.1 V
    "grid_frequency",                  # 1  BBB   0.1 Hz
    "ac_output_voltage",               # 2  CCCC  0.1 V
    "ac_output_frequency",             # 3  DDD   0.1 Hz
    "output_apparent_power",           # 4  EEEE  VA
    "output_active_power",             # 5  FFFF  W
    "load_percent",                    # 6  GGG   %
    "battery_voltage",                 # 7  HHH   0.1 V
    "scc_battery_voltage",             # 8  III   0.1 V
    "_battery_voltage_scc2",           # 9  JJJ   0.1 V (PI18 only)
    "battery_discharge_current",       # 10 KKK   A
    "battery_charging_current",        # 11 LLL   A
    "battery_capacity",                # 12 MMM   %
    "inverter_heat_sink_temperature",  # 13 NNN   °C
    "_mppt1_temp",                     # 14 OOO   °C
    "_mppt2_temp",                     # 15 PPP   °C
    "pv_charging_power",               # 16 QQQQ  W
    "_pv2_input_power",                # 17 RRRR  W
    "pv_input_voltage",                # 18 SSSS  0.1 V
    "_pv2_input_voltage",              # 19 TTTT  0.1 V
    "_settings_changed",               # 20 U     0/1
    "_mppt1_status",                   # 21 V     0/1/2
    "_mppt2_status",                   # 22 W     0/1/2
    "_load_connected",                 # 23 X     0/1
    "_battery_power_dir",              # 24 Y     0/1/2
    "_dcac_power_dir",                 # 25 Z     0/1/2
    "_line_power_dir",                 # 26 a     0/1/2
    "_local_parallel_id",              # 27 b
)


def _decode_gs(tokens: list[str]) -> dict[str, Any]:
    """Project a ``^P005GS`` reply onto the Axpert-shaped ``qpigs`` dict."""
    padded = list(tokens) + [""] * (len(_GS_FIELDS) - len(tokens))
    raw = dict(zip(_GS_FIELDS, padded))

    # PI18 doesn't expose pv_input_current directly — synthesise from
    # power/voltage; clamp on zero voltage to avoid div/0.
    pv_v_int = _safe_int(raw["pv_input_voltage"])
    pv_p_int = _safe_int(raw["pv_charging_power"])
    pv_v = pv_v_int / 10.0
    pv_input_current = pv_p_int / pv_v if pv_v else 0.0

    # PI18 status bits aren't directly exposed; mirror SMG-II's "running,
    # AC-charging" baseline so any sensor that reads them gets a constant
    # rather than KeyError.
    status_b7_b0 = "00010001"
    status_b10_b8 = "010"

    return {
        "grid_voltage": f"{_safe_int(raw['grid_voltage']) / 10.0:.1f}",
        "grid_frequency": f"{_safe_int(raw['grid_frequency']) / 10.0:.1f}",
        "ac_output_voltage": f"{_safe_int(raw['ac_output_voltage']) / 10.0:.1f}",
        "ac_output_frequency": f"{_safe_int(raw['ac_output_frequency']) / 10.0:.1f}",
        "output_apparent_power": f"{_safe_int(raw['output_apparent_power']):04d}",
        "output_active_power": f"{_safe_int(raw['output_active_power']):04d}",
        "load_percent": f"{_safe_int(raw['load_percent']):03d}",
        "bus_voltage": "400",
        "battery_voltage": f"{_safe_int(raw['battery_voltage']) / 10.0:.2f}",
        "battery_charging_current": f"{_safe_int(raw['battery_charging_current']):03d}",
        "battery_capacity": f"{_safe_int(raw['battery_capacity']):03d}",
        "inverter_heat_sink_temperature": f"{_safe_int(raw['inverter_heat_sink_temperature']):.1f}",
        "pv_input_current": f"{pv_input_current:.1f}",
        "pv_input_voltage": f"{pv_v:.1f}",
        "scc_battery_voltage": f"{_safe_int(raw['scc_battery_voltage']) / 10.0:.2f}",
        "battery_discharge_current": f"{_safe_int(raw['battery_discharge_current']):05d}",
        "device_status_bits_b7_b0": status_b7_b0,
        "battery_voltage_offset": "00",
        "eeprom_version": "00",
        "pv_charging_power": f"{pv_p_int:05d}",
        "device_status_bits_b10_b8": status_b10_b8,
    }


# ---------------------------------------------------------------------------
# PIRI — Rated information. Maps onto ``qpiri`` section.
# ---------------------------------------------------------------------------


_PIRI_FIELDS = (
    "rated_grid_voltage",
    "rated_input_current",
    "rated_ac_output_voltage",
    "rated_output_frequency",
    "rated_output_current",
    "rated_output_apparent_power",
    "rated_output_active_power",
    "rated_battery_voltage",
    "battery_recharge_voltage",
    "battery_redischarge_voltage",
    "battery_under_voltage",
    "battery_bulk_voltage",
    "battery_float_voltage",
    "battery_type_code",
    "max_ac_charging_current",
    "max_charging_current",
    "input_voltage_range_code",
    "output_priority_code",
    "charger_priority_code",
    "parallel_max",
    "machine_type",
    "topology",
    "output_model_setting",
    "solar_power_priority_code",
    "mppt_string",
)


# PI18 R (output priority) only describes 0=Solar-Utility-Battery and
# 1=Solar-Battery-Utility. Map to the closest Axpert enum members.
_PI18_OUTPUT_PRIORITY: Mapping[int, str] = {
    0: OutputSourcePriority.SolarFirst.name,
    1: OutputSourcePriority.SBU.name,
}

_PI18_CHARGER_PRIORITY: Mapping[int, str] = {
    0: ChargerSourcePriority.SolarFirst.name,
    1: ChargerSourcePriority.SolarAndUtility.name,
    2: ChargerSourcePriority.OnlySolar.name,
}


def _decode_piri(tokens: list[str]) -> dict[str, Any]:
    padded = list(tokens) + [""] * (len(_PIRI_FIELDS) - len(tokens))
    raw = dict(zip(_PIRI_FIELDS, padded))

    try:
        battery_type = BatteryType(raw["battery_type_code"]).name
    except ValueError:
        battery_type = raw["battery_type_code"]

    try:
        ac_range = ACInputVoltageRange(raw["input_voltage_range_code"]).name
    except ValueError:
        ac_range = raw["input_voltage_range_code"]

    output_priority = _PI18_OUTPUT_PRIORITY.get(
        _safe_int(raw["output_priority_code"], -1),
        raw["output_priority_code"],
    )
    charger_priority = _PI18_CHARGER_PRIORITY.get(
        _safe_int(raw["charger_priority_code"], -1),
        raw["charger_priority_code"],
    )

    return {
        "rated_grid_voltage": f"{_safe_int(raw['rated_grid_voltage']) / 10.0:.1f}",
        "rated_input_current": f"{_safe_int(raw['rated_input_current']) / 10.0:.1f}",
        "rated_ac_output_voltage": f"{_safe_int(raw['rated_ac_output_voltage']) / 10.0:.1f}",
        "rated_output_frequency": f"{_safe_int(raw['rated_output_frequency']) / 10.0:.1f}",
        "rated_output_current": f"{_safe_int(raw['rated_output_current']) / 10.0:.1f}",
        "rated_output_apparent_power": f"{_safe_int(raw['rated_output_apparent_power']):04d}",
        "rated_output_active_power": f"{_safe_int(raw['rated_output_active_power']):04d}",
        "rated_battery_voltage": f"{_safe_int(raw['rated_battery_voltage']) / 10.0:.1f}",
        "low_battery_to_ac_bypass_voltage": f"{_safe_int(raw['battery_redischarge_voltage']) / 10.0:.1f}",
        "shut_down_battery_voltage": f"{_safe_int(raw['battery_under_voltage']) / 10.0:.1f}",
        "bulk_charging_voltage": f"{_safe_int(raw['battery_bulk_voltage']) / 10.0:.1f}",
        "float_charging_voltage": f"{_safe_int(raw['battery_float_voltage']) / 10.0:.1f}",
        "battery_type": battery_type,
        "max_utility_charging_current": f"{_safe_int(raw['max_ac_charging_current']):02d}",
        "max_charging_current": f"{_safe_int(raw['max_charging_current']):03d}",
        "ac_input_voltage_range": ac_range,
        "output_source_priority": output_priority,
        "charger_source_priority": charger_priority,
        "parallel_max_number": raw["parallel_max"] or "0",
        "reserved_uu": "00",
        "reserved_v": "0",
        # PI18 has no PIRI parallel master/slave readout. Default to standalone.
        "parallel_mode": "Standalone",
        "high_battery_voltage_to_battery_mode": f"{_safe_int(raw['battery_recharge_voltage']) / 10.0:.1f}",
        "solar_work_condition_in_parallel": "0",
        "solar_max_charging_power_auto_adjust": "1_",
        "rated_battery_capacity": "200",
        "reserved_b": "0",
        "reserved_ccc": "0",
    }


# ---------------------------------------------------------------------------
# MOD — Working mode. Maps to ``operating_mode``.
# ---------------------------------------------------------------------------


_PI18_MODE_TO_OPERATING_MODE: Mapping[int, OperatingMode] = {
    0: OperatingMode.PowerOn,
    1: OperatingMode.Standby,
    2: OperatingMode.Line,            # Bypass
    3: OperatingMode.Battery,
    4: OperatingMode.Fault,
    5: OperatingMode.Line,            # Hybrid (Line/Grid)
}


def _decode_mod(payload: str) -> dict[str, Any]:
    code = _safe_int(payload.strip(), -1)
    mode = _PI18_MODE_TO_OPERATING_MODE.get(code)
    if mode is None:
        return {"operating_mode": "Unknown"}
    return {"operating_mode": mode}


# ---------------------------------------------------------------------------
# Top-level decode entry point
# ---------------------------------------------------------------------------


def decode_pi18_response(command: str, raw: bytes) -> dict[str, Any]:
    if not raw or raw == b"null":
        return {"error": "null response received. Command not accepted."}

    # Set commands answer with a bare ^1 / ^0 marker, no length header.
    if raw[:2] == b"^1":
        return {"status": "ACK"}
    if raw[:2] == b"^0":
        return {"status": "NAK"}

    payload_bytes = _strip_pi18_frame(raw)
    payload = payload_bytes.decode("ascii", errors="ignore").strip()
    if not payload:
        return {"error": "empty PI18 payload"}

    cmd = command.upper()
    native = LOGICAL_TO_NATIVE.get(cmd, cmd)

    if native == "MOD":
        return _decode_mod(payload)
    if native == "PI":
        return {"protocol_id": payload}

    tokens = [t.strip() for t in payload.split(",")]
    if native == "GS":
        return _decode_gs(tokens)
    if native == "PIRI":
        return _decode_piri(tokens)

    # Fallback for commands we encode but don't decode in detail yet.
    return {"raw_tokens": tokens}
