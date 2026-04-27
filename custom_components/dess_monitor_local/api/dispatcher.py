"""Top-level read/write dispatchers for inverter device URIs.

The ``device`` string identifies both the protocol and the connection
target. URI schemes::

    agent://<host>:<port>/<providerDeviceId>   solar-system-agent HTTP snapshot
    modbus://<host>:<port>                     SMG-II via Modbus RTU-over-TCP
    pi18://<host>:<port>                       InfiniSolar-V (PI18) over TCP
    pi18-serial://<path>                       PI18 over RS232 (rare)
    tcp://<host>:<port>                        Voltronic Axpert via Elfin TCP
    /dev/ttyUSB0  or  COM3                     Voltronic Axpert via serial

All read paths return a flat dict shaped like the Voltronic QPIGS /
QPIRI / QMOD response, so sensors don't need to know which adapter
sourced the data.
"""
from __future__ import annotations

import asyncio
import logging

import serial_asyncio_fast as serial_asyncio

from .decoders.enums import (
    BatteryTypeSetting,
    ChargeSourcePrioritySetting,
    OperatingMode,
    OutputSourcePrioritySetting,
)
from .decoders.voltronic import decode_direct_response
from .protocols.agent_http import (
    AGENT_STALE_THRESHOLD_MS,
    fetch_agent_snapshot,
    parse_agent_uri,
    post_agent_setting,
    split_raw_by_command,
)
from .protocols.elfin_tcp import (
    ElfinTCPProtocol,
    parse_tcp_uri,
    send_voltronic_set_command,
)
from .protocols.modbus_rtu import (
    parse_modbus_uri,
    read_smg2_snapshot,
    smg2_to_qpigs,
    smg2_to_qpiri,
    write_modbus_single_register,
)
from .protocols.pi18_tcp import query_pi18
from .protocols.serial_uart import SERIAL_BAUDRATE, SerialCommandProtocol

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# READ
# ---------------------------------------------------------------------------


async def get_direct_data(
    device: str, command_str: str, timeout: float = 30.0
) -> dict:
    """Universal read dispatcher.

    See module docstring for supported URI schemes. Returns ``{}`` on any
    transport-level failure — the coordinator treats that as "no data
    this tick" rather than escalating.
    """
    command = command_str.upper()

    # ---- agent:// — pre-decoded snapshot ----
    if device.startswith("agent://"):
        try:
            host, port, provider_device_id = parse_agent_uri(device)
        except ValueError as err:
            _LOGGER.warning("invalid agent URI %s: %s", device, err)
            return {}

        payload = await fetch_agent_snapshot(host, port, provider_device_id, timeout)
        if not payload:
            return {}

        age_ms = payload.get("ageMs")
        if isinstance(age_ms, (int, float)) and age_ms > AGENT_STALE_THRESHOLD_MS:
            _LOGGER.debug(
                "agent snapshot for %s is stale (%sms) — dropping",
                provider_device_id,
                age_ms,
            )
            return {}

        raw = payload.get("raw") or {}
        if not isinstance(raw, dict):
            return {}

        if command == "QMOD":
            sub = split_raw_by_command(raw, "QMOD")
            mode_name = sub.get("operating_mode")
            if mode_name is None:
                return {}
            try:
                return {"operating_mode": OperatingMode[mode_name]}
            except KeyError:
                _LOGGER.debug(
                    "agent returned unknown operating_mode '%s'", mode_name
                )
                return {}

        return split_raw_by_command(raw, command)

    # ---- modbus:// — virtual QPIGS/QPIRI/QMOD on top of SMG-II registers ----
    if device.startswith("modbus://"):
        try:
            host, port = parse_modbus_uri(device)
        except Exception:
            return {}

        try:
            sensors, config = await read_smg2_snapshot(host, port)
        except Exception:
            return {}

        if command == "QPIGS":
            return smg2_to_qpigs(sensors)
        if command == "QPIRI":
            return smg2_to_qpiri(config)
        if command == "QMOD":
            om = (sensors.get("operation_mode") or "").lower()
            if any(x in om for x in ("mains", "bypass", "charging")):
                mode = OperatingMode.Line
            elif any(x in om for x in ("off-grid", "offgrid", "off grid")):
                mode = OperatingMode.Battery
            elif "standby" in om:
                mode = OperatingMode.Standby
            elif "fault" in om:
                mode = OperatingMode.Fault
            else:
                mode = OperatingMode.PowerOn
            return {"operating_mode": mode}

        return {"sensors": sensors, "config": config}

    # ---- pi18:// — InfiniSolar-V over TCP / serial ----
    if device.startswith("pi18://") or device.startswith("pi18-serial://"):
        return await query_pi18(device, command, timeout)

    # ---- Voltronic Axpert: Elfin TCP or serial ----
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    transport: asyncio.Transport | None = None

    def on_response(data, err):
        if not fut.done():
            fut.set_result(None if err else data)

    try:
        if device.startswith("tcp://"):
            host, port = parse_tcp_uri(device)
            transport, _ = await loop.create_connection(
                lambda: ElfinTCPProtocol(command, on_response),
                host,
                port,
            )
        else:
            transport, _ = await serial_asyncio.create_serial_connection(
                loop,
                lambda: SerialCommandProtocol(command, on_response),
                device,
                baudrate=SERIAL_BAUDRATE,
                bytesize=8,
                parity="N",
                stopbits=1,
            )
    except Exception:
        return {}

    try:
        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            result = None

        if result and isinstance(result, str):
            try:
                return decode_direct_response(command, result) or {}
            except Exception:
                return {}
        return {}
    finally:
        if transport:
            transport.close()


# ---------------------------------------------------------------------------
# WRITE
# ---------------------------------------------------------------------------
#
# Setting enums (UPPER_SNAKE) are local-readable names; the agent
# expects canonical CamelCase strings. Translation tables keep the two
# vocabularies consistent — keep in sync with ``settings-registry.ts``
# in the agent repo, or the agent will 400.

_OUTPUT_PRIORITY_TO_AGENT: dict = {
    OutputSourcePrioritySetting.UTILITY_FIRST: "UtilityFirst",
    OutputSourcePrioritySetting.SBU_PRIORITY: "SBU",
    OutputSourcePrioritySetting.SOLAR_FIRST: "SolarFirst",
}

_CHARGER_PRIORITY_TO_AGENT: dict = {
    ChargeSourcePrioritySetting.UTILITY_FIRST: "UtilityFirst",
    ChargeSourcePrioritySetting.SOLAR_FIRST: "SolarFirst",
    ChargeSourcePrioritySetting.SOLAR_AND_UTILITY: "SolarAndUtility",
}


async def set_direct_data(
    device: str, command_str: str, timeout: float = 30.0
) -> dict:
    """Send a Voltronic *set* command (PBATC/POP/PCP/...) over Elfin TCP.

    Modbus / agent variants live in their own ``set_*`` helpers below;
    this stays a thin TCP-only path so the legacy callers keep working.
    """
    if not device.startswith("tcp://"):
        return {"error": "only tcp://host:port supported for set_direct_data"}
    host, port = parse_tcp_uri(device)
    return await send_voltronic_set_command(host, port, command_str, timeout)


async def set_direct_data_agent(
    device: str, setting_key: str, value, timeout: float = 30.0
) -> dict:
    return await post_agent_setting(device, setting_key, value, timeout)


# ---- Per-setting helpers (semantic API exposed to platform code) ---------


async def set_battery_type(device: str, battery_type: BatteryTypeSetting) -> dict:
    if device.startswith("modbus://"):
        return {"error": "set_battery_type is not implemented for modbus devices"}
    return await set_direct_data(device, battery_type.value)


async def set_output_source_priority(
    device: str, mode: OutputSourcePrioritySetting
) -> dict:
    if device.startswith("agent://"):
        agent_value = _OUTPUT_PRIORITY_TO_AGENT.get(mode)
        if agent_value is None:
            return {"ok": False, "error": f"no agent mapping for {mode}"}
        return await post_agent_setting(device, "output_source_priority", agent_value)

    if device.startswith("modbus://"):
        try:
            host, port = parse_modbus_uri(device)
        except Exception:
            return {"error": "invalid modbus device string"}
        mapping = {
            OutputSourcePrioritySetting.UTILITY_FIRST: 0,
            OutputSourcePrioritySetting.SOLAR_FIRST: 1,
            OutputSourcePrioritySetting.SBU_PRIORITY: 2,
        }
        value = mapping.get(mode)
        if value is None:
            return {"error": f"mode {mode} is not mappable to SMG output_priority"}
        return await write_modbus_single_register(host, port, 301, value)

    return await set_direct_data(device, mode.value)


async def set_charge_source_priority(
    device: str, mode: ChargeSourcePrioritySetting
) -> dict:
    if device.startswith("agent://"):
        agent_value = _CHARGER_PRIORITY_TO_AGENT.get(mode)
        if agent_value is None:
            return {"ok": False, "error": f"no agent mapping for {mode}"}
        return await post_agent_setting(device, "charger_source_priority", agent_value)

    if device.startswith("modbus://"):
        try:
            host, port = parse_modbus_uri(device)
        except Exception:
            return {"error": "invalid modbus device string"}
        mapping = {
            ChargeSourcePrioritySetting.UTILITY_FIRST: 0,
            ChargeSourcePrioritySetting.SOLAR_FIRST: 1,
            ChargeSourcePrioritySetting.SOLAR_AND_UTILITY: 2,
        }
        value = mapping.get(mode)
        if value is None:
            return {"error": f"mode {mode} is not mappable to SMG battery_charging_priority"}
        return await write_modbus_single_register(host, port, 331, value)

    return await set_direct_data(device, mode.value)


async def set_battery_bulk_voltage(device: str, voltage: float) -> dict:
    if device.startswith("agent://"):
        return await post_agent_setting(device, "bulk_charging_voltage", float(voltage))
    if device.startswith("modbus://"):
        try:
            host, port = parse_modbus_uri(device)
        except Exception:
            return {"error": "invalid modbus device string"}
        reg_value = max(0, min(0xFFFF, int(round(voltage * 10.0))))
        return await write_modbus_single_register(host, port, 324, reg_value)
    return await set_direct_data(device, f"PBAV{voltage:.2f}")


async def set_battery_float_voltage(device: str, voltage: float) -> dict:
    if device.startswith("agent://"):
        return await post_agent_setting(device, "float_charging_voltage", float(voltage))
    if device.startswith("modbus://"):
        try:
            host, port = parse_modbus_uri(device)
        except Exception:
            return {"error": "invalid modbus device string"}
        reg_value = max(0, min(0xFFFF, int(round(voltage * 10.0))))
        return await write_modbus_single_register(host, port, 325, reg_value)
    return await set_direct_data(device, f"PBFV{voltage:.2f}")


async def set_rated_battery_voltage(device: str, voltage: int) -> dict:
    if device.startswith("modbus://"):
        return {"error": "set_rated_battery_voltage is not implemented for modbus devices"}
    return await set_direct_data(device, f"PBRV{voltage}")


async def set_max_combined_charge_current(device: str, amps: int) -> dict:
    if device.startswith("agent://"):
        return await post_agent_setting(device, "max_charging_current", int(amps))
    if device.startswith("modbus://"):
        try:
            host, port = parse_modbus_uri(device)
        except Exception:
            return {"error": "invalid modbus device string"}
        reg_value = max(0, min(0xFFFF, int(round(amps * 10.0))))
        return await write_modbus_single_register(host, port, 332, reg_value)
    return await set_direct_data(device, f"MCHGC{amps:03d}")


async def set_battery_charge_current(device: str, amps: int) -> dict:
    if device.startswith("agent://"):
        return await post_agent_setting(device, "max_charging_current", int(amps))
    if device.startswith("modbus://"):
        try:
            host, port = parse_modbus_uri(device)
        except Exception:
            return {"error": "invalid modbus device string"}
        reg_value = max(0, min(0xFFFF, int(round(amps * 10.0))))
        return await write_modbus_single_register(host, port, 332, reg_value)
    return await set_direct_data(device, f"PBATC{amps:03d}")


async def set_max_utility_charge_current(device: str, amps: int) -> dict:
    if device.startswith("agent://"):
        return await post_agent_setting(device, "max_utility_charging_current", int(amps))
    if device.startswith("modbus://"):
        try:
            host, port = parse_modbus_uri(device)
        except Exception:
            return {"error": f"invalid modbus device: {device}"}
        return await write_modbus_single_register(host, port, 333, int(amps * 10))
    return await set_direct_data(device, f"MUCHGC{amps:03d}")
