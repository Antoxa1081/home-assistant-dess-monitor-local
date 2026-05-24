"""Top-level read/write dispatchers for inverter device URIs.

The ``device`` string identifies both the protocol and the connection
target. URI schemes::

    agent://<host>:<port>/<providerDeviceId>   solar-system-agent HTTP snapshot
    modbus://<host>:<port>                     SMG-II via Modbus RTU-over-TCP
    pi18://<host>:<port>                       InfiniSolar-V (PI18) over TCP
    pi18-serial://<path>                       PI18 over RS232 (rare)
    eybond://<host>:<port>/<devaddr>           Voltronic PI30 via EyBond dongle
    eybond-pi18://<host>:<port>/<devaddr>      PI18 via EyBond dongle
    tcp://<host>:<port>                        Voltronic Axpert via Elfin TCP
    /dev/ttyUSB0  or  COM3                     Voltronic Axpert via serial

All read paths return a flat dict shaped like the Voltronic QPIGS /
QPIRI / QMOD response, so sensors don't need to know which adapter
sourced the data.
"""
from __future__ import annotations
import logging
from .adapters.factory import get_adapter
from .decoders.enums import (
    BatteryTypeSetting,
    ChargeSourcePrioritySetting,
    OutputSourcePrioritySetting,
)

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# READ
# ---------------------------------------------------------------------------

async def get_direct_data(
    device: str, command_str: str, timeout: float = 30.0, strict_crc: bool = False
) -> dict:
    """Universal read dispatcher using the adapter pattern."""
    adapter = get_adapter(device, timeout, strict_crc)
    return await adapter.get_data(command_str.upper())

# ---------------------------------------------------------------------------
# WRITE
# ---------------------------------------------------------------------------

async def set_direct_data(
    device: str, command_str: str, timeout: float = 30.0
) -> dict:
    """Send a raw set command to the device."""
    adapter = get_adapter(device, timeout)
    return await adapter.set_data(command_str.upper())

async def set_direct_data_agent(
    device: str, setting_key: str, value, timeout: float = 30.0
) -> dict:
    """Agent-specific set command."""
    adapter = get_adapter(device, timeout)
    if hasattr(adapter, "set_setting"):
        return await adapter.set_setting(setting_key, value)
    return {"error": "Adapter does not support generic set_setting"}


# ---- Per-setting helpers (semantic API exposed to platform code) ---------

async def set_battery_type(device: str, battery_type: BatteryTypeSetting) -> dict:
    adapter = get_adapter(device)
    return await adapter.set_battery_type(battery_type)

async def set_output_source_priority(
    device: str, mode: OutputSourcePrioritySetting
) -> dict:
    adapter = get_adapter(device)
    return await adapter.set_output_source_priority(mode)

async def set_charge_source_priority(
    device: str, mode: ChargeSourcePrioritySetting
) -> dict:
    adapter = get_adapter(device)
    return await adapter.set_charge_source_priority(mode)

async def set_battery_bulk_voltage(device: str, voltage: float) -> dict:
    adapter = get_adapter(device)
    return await adapter.set_battery_bulk_voltage(voltage)

async def set_battery_float_voltage(device: str, voltage: float) -> dict:
    adapter = get_adapter(device)
    return await adapter.set_battery_float_voltage(voltage)

async def set_rated_battery_voltage(device: str, voltage: int) -> dict:
    adapter = get_adapter(device)
    return await adapter.set_rated_battery_voltage(voltage)

async def set_max_combined_charge_current(device: str, amps: int) -> dict:
    adapter = get_adapter(device)
    return await adapter.set_max_combined_charge_current(amps)

async def set_battery_charge_current(device: str, amps: int) -> dict:
    adapter = get_adapter(device)
    return await adapter.set_battery_charge_current(amps)

async def set_max_utility_charge_current(
    device: str, amps: int, float_format: bool = False
) -> dict:
    adapter = get_adapter(device)
    return await adapter.set_max_utility_charge_current(amps, float_format)
