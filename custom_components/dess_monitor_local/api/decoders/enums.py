"""Enumerations shared across inverter protocols.

Readback enums (BatteryType, OutputSourcePriority, etc.) mirror the values
returned by Voltronic/PI18 firmware. PI18 uses a slightly different code
space for some fields — adapters translate codes to ``.name`` strings so
sensors stay protocol-agnostic.

Setting enums (BatteryTypeSetting, ...) carry the wire commands used on
the *write* path (e.g. POP00/POP01/POP02) and are intentionally separate
from the readback enums so the two value spaces can never cross.
"""
from __future__ import annotations

from enum import Enum, IntEnum, unique


class BatteryType(Enum):
    AGM = "0"
    Flooded = "1"
    UserDefined = "2"
    LIB = "3"
    LIC = "4"
    RESERVED = "5"
    RESERVED_1 = "6"
    RESERVED_2 = "7"


class ACInputVoltageRange(Enum):
    Appliance = "0"
    UPS = "1"


class OutputSourcePriority(Enum):
    UtilityFirst = "0"
    SolarFirst = "1"
    SBU = "2"
    BatteryOnly = "4"
    UtilityOnly = "5"
    SolarAndUtility = "6"
    Smart = "7"


class ChargerSourcePriority(Enum):
    UtilityFirst = "0"
    SolarFirst = "1"
    SolarAndUtility = "2"
    OnlySolar = "3"


class ParallelMode(Enum):
    Master = "0"
    Slave = "1"
    Standalone = "2"


class OperatingMode(Enum):
    PowerOn = "P"
    Standby = "S"
    Line = "L"
    Battery = "B"
    ShutdownApproaching = "D"
    Fault = "F"


class BatteryTypeSetting(Enum):
    AGM = "PBT00"
    FLOODED = "PBT01"
    USER = "PBT02"
    LIFEP04 = "PBT03"


class OutputSourcePrioritySetting(Enum):
    UTILITY_FIRST = "POP00"
    SBU_PRIORITY = "POP01"
    SOLAR_FIRST = "POP02"


class ChargeSourcePrioritySetting(Enum):
    UTILITY_FIRST = "PCP00"
    SOLAR_FIRST = "PCP01"
    SOLAR_AND_UTILITY = "PCP02"


@unique
class DeviceStatusBitsB7B0(IntEnum):
    FAULT = 1 << 7
    RESERVED_B6 = 1 << 6
    BUS_OVER = 1 << 5
    LINE_FAIL = 1 << 4
    BATTERY_LOW = 1 << 3
    BATTERY_HIGH = 1 << 2
    INVERTER_OVERLOAD = 1 << 1
    INVERTER_ON = 1 << 0


@unique
class DeviceStatusBitsB10B8(IntEnum):
    CHARGING_TO_BATTERY = 1 << 2
    CHARGING_AC_ACTIVE = 1 << 1
    CHARGING_SCC_ACTIVE = 1 << 0


def _extract_bits(raw: str, count: int) -> str:
    """Strip everything that's not 0/1 and return exactly ``count`` bits."""
    bits = [c for c in (raw or "") if c in "01"][:count]
    return "".join(bits).rjust(count, "0")


def parse_device_status_bits_b7_b0(raw: str) -> dict:
    bits = _extract_bits(raw, 8)
    value = int(bits, 2)
    return {
        "fault": bool(value & DeviceStatusBitsB7B0.FAULT),
        "line_fail": bool(value & DeviceStatusBitsB7B0.LINE_FAIL),
        "bus_over": bool(value & DeviceStatusBitsB7B0.BUS_OVER),
        "battery_low": bool(value & DeviceStatusBitsB7B0.BATTERY_LOW),
        "battery_high": bool(value & DeviceStatusBitsB7B0.BATTERY_HIGH),
        "inverter_overload": bool(value & DeviceStatusBitsB7B0.INVERTER_OVERLOAD),
        "inverter_on": bool(value & DeviceStatusBitsB7B0.INVERTER_ON),
        "_raw_b7_b0": bits,
    }


def parse_device_status_bits_b10_b8(raw: str) -> dict:
    bits = _extract_bits(raw, 3)
    value = int(bits, 2)
    return {
        "charging_to_battery": bool(value & DeviceStatusBitsB10B8.CHARGING_TO_BATTERY),
        "charging_scc_active": bool(value & DeviceStatusBitsB10B8.CHARGING_SCC_ACTIVE),
        "charging_ac_active": bool(value & DeviceStatusBitsB10B8.CHARGING_AC_ACTIVE),
        "_raw_b10_b8": bits,
    }
