import asyncio
import re
import struct
from enum import Enum, unique, IntEnum

import serial_asyncio_fast as serial_asyncio


def decode_ascii_response(hex_string):
    hex_values = hex_string.strip().split()
    byte_values = bytes(int(b, 16) for b in hex_values)
    ascii_str = byte_values.decode('ascii', errors='ignore').strip()
    if ascii_str.startswith('('):
        ascii_str = ascii_str[1:]
    return ascii_str


def decode_qpigs(ascii_str):
    values = ascii_str.split()
    fields = [
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
        "reserved_cccc"
    ]
    return dict(zip(fields, values))


def decode_qpigs2(ascii_str):
    values = ascii_str.split()
    fields = [
        "pv_current",
        "pv_voltage",
        "pv_daily_energy"
    ]
    return dict(zip(fields, values))


class BatteryType(Enum):
    AGM = '0'
    Flooded = '1'
    UserDefined = '2'
    LIB = '3'
    LIC = '4'
    RESERVED = '5'
    RESERVED_1 = '6'
    RESERVED_2 = '7'


class ACInputVoltageRange(Enum):
    Appliance = '0'
    UPS = '1'


class OutputSourcePriority(Enum):
    UtilityFirst = '0'  # сеть
    SolarFirst = '1'
    SBU = '2'  # Solar → Battery → Utility
    BatteryOnly = '4'
    UtilityOnly = '5'
    SolarAndUtility = '6'
    Smart = '7'  # может быть задан в некоторых прошивках


class ChargerSourcePriority(Enum):
    UtilityFirst = '0'
    SolarFirst = '1'
    SolarAndUtility = '2'
    OnlySolar = '3'


class ParallelMode(Enum):
    Master = '0'
    Slave = '1'
    Standalone = '2'


class OperatingMode(Enum):
    PowerOn = 'P'  # Power On — The inverter is powered on and operational
    Standby = 'S'  # Standby — The inverter is in standby mode (e.g., no active load)
    Line = 'L'  # Line (Bypass) — Operating from utility/grid power, possibly bypassing the inverter
    Battery = 'B'  # Battery Inverter Mode — Operating from battery via inverter
    ShutdownApproaching = 'D'  # Shutdown Approaching — Critical state, preparing to shut down
    Fault = 'F'  # Fault — Error condition; inverter is in fault mode


def transform_qpiri_value(index, value):
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


def decode_qpiri(ascii_str):
    values = ascii_str.split()
    fields = [
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
        "reserved_ccc"
    ]

    return {
        field: transform_qpiri_value(i, value)
        for i, (field, value) in enumerate(zip(fields, values))
    }


def decode_qmod(ascii_str):
    mode_code = ascii_str.strip()[0]
    try:
        mode = OperatingMode(mode_code)
    except ValueError:
        mode = "Unknown"
    return {"operating_mode": mode}


def decode_qmn(ascii_str):
    return {"Model": ascii_str.strip()}


def decode_qid(ascii_str):
    return {"Device ID": ascii_str.strip()}


def decode_qflag(ascii_str):
    return {"Enabled/Disabled Flags": ascii_str.strip()}


def decode_qvfw(ascii_str):
    return {"Firmware Version": ascii_str.replace("VERFW:", "").strip()}


def decode_qbeqi(ascii_str):
    values = ascii_str.split()
    fields = [
        "equalization_function",
        "equalization_time",
        "interval_days",
        "max_charging_current",
        "float_voltage",
        "reserved_1",
        "equalization_timeout",
        "immediate_activation_flag",
        "elapsed_time"
    ]
    return dict(zip(fields, values))


def is_hex_string(s: str) -> bool:
    """Проверяет, состоит ли строка только из hex-символов или байтов в формате 'AA BB CC'."""
    s = s.strip().replace(" ", "")
    return bool(re.fullmatch(r"[0-9A-Fa-f]+", s)) and len(s) % 2 == 0



def decode_direct_response(command: str, input_str: str) -> dict:
    """Определяет тип входных данных (hex или ascii) и выполняет расшифровку."""
    if not input_str:
        return {"error": "empty response"}

    if input_str == "null":
        return {"error": "null response received. Command not accepted."}

    # 🔹 если это hex — сначала декодируем в ASCII
    if is_hex_string(input_str):
        ascii_str = decode_ascii_response(input_str)
    else:
        ascii_str = input_str.strip()

    # 🔹 очистим лишние символы скобок и CR/LF
    ascii_str = ascii_str.strip().replace("(", "").replace(")", "").replace("\r", "").replace("\n", "")

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
        case _:
            return {"Raw": ascii_str}


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



def crc16(data: bytes) -> bytes:
    """CRC16, как в протоколе Voltronic (QPIGS/QPI/QMOD)."""
    crc = 0
    for b in data:
        x = (crc >> 8) ^ b
        x ^= x >> 4
        crc = ((crc << 8) ^ (x << 12) ^ (x << 5) ^ x) & 0xFFFF
    return struct.pack(">H", crc)


class ElfinTCPProtocol(asyncio.Protocol):
    def __init__(self, command: str, on_response):
        self.transport = None
        self.command = command.upper()
        self.command_bytes = command.encode("ascii")
        self.on_response = on_response
        self.buffer = bytearray()

    def connection_made(self, transport):
        self.transport = transport
        packet = self.command_bytes + crc16(self.command_bytes) + b'\r'
        self.transport.write(packet)

    def data_received(self, data: bytes):
        self.buffer.extend(data)
        if b'\r' in self.buffer or b'\n' in self.buffer:
            raw = self.buffer.split(b'\r', 1)[0].strip()
            try:
                response = raw.decode(errors='ignore')
                self.on_response(response, None)
            except Exception as e:
                self.on_response(None, e)
            if self.transport:
                self.transport.close()

    def connection_lost(self, exc):
        if exc:
            self.on_response(None, exc)


class SerialCommandProtocol(asyncio.Protocol):
    def __init__(self, command: str, on_response):
        self.transport = None
        self.command = command.upper()
        self.command_bytes = command.encode("ascii")
        self.on_response = on_response
        self.buffer = bytearray()

    def connection_made(self, transport):
        self.transport = transport
        packet = self.command_bytes + crc16(self.command_bytes) + b'\r'
        self.transport.write(packet)

    def data_received(self, data: bytes):
        self.buffer.extend(data)
        if b'\r' in self.buffer:
            raw = self.buffer.split(b'\r', 1)[0].strip()
            try:
                response = raw.decode(errors='ignore')
                self.on_response(response, None)
            except Exception as e:
                self.on_response(None, e)
            if self.transport:
                self.transport.close()

    def connection_lost(self, exc):
        if exc:
            self.on_response(None, exc)


async def get_direct_data(device: str, command_str: str, timeout: float = 5.0) -> dict:
    """
    Отправляет команду (например "QPIGS") через Serial или TCP (ELFIN).
    Возвращает готовый dict с данными.
    При ошибке или таймауте возвращает {}.
    """
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def on_response(data, err):
        if not fut.done():
            if err:
                fut.set_result(None)
            else:
                fut.set_result(data)

    # --- выбор транспорта ---
    try:
        if device.startswith("tcp://"):
            _, addr = device.split("tcp://", 1)
            host, port = addr.split(":")
            port = int(port)
            transport, protocol = await loop.create_connection(
                lambda: ElfinTCPProtocol(command_str, on_response),
                host,
                port,
            )
        else:
            transport, protocol = await serial_asyncio.create_serial_connection(
                loop,
                lambda: SerialCommandProtocol(command_str, on_response),
                device,
                baudrate=2400,
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


        # print('result', result)
        # --- парсинг в dict ---
        if result and isinstance(result, str):
            try:
                parsed = decode_direct_response(command_str, result)
                return parsed or {}
            except Exception:
                return {}
        else:
            return {}
    finally:
        if transport:
            transport.close()

async def set_direct_data(device: str, command_str: str, timeout: float = 5.0) -> dict:
    """
    Отправляет управляющую команду (например PBATC030, POP00 и т.п.) на устройство.
    Возвращает {'status': 'ACK'} или {'status': 'NAK'} или {'error': '...'}.
    """
    if device.startswith("tcp://"):
        _, data = device.split("tcp://")
        host, port = data.split(":")
        port = int(port)
    else:
        return {"error": "only tcp://host:port supported for set_direct_data"}

    try:
        reader, writer = await asyncio.open_connection(host, port)
        cmd = command_str.strip().encode("ascii")
        packet = cmd + crc16(cmd) + b"\r"

        # print(f"[ELFIN] → {packet}")
        writer.write(packet)
        await writer.drain()

        try:
            data = await asyncio.wait_for(reader.read(128), timeout=timeout)
        except asyncio.TimeoutError:
            return {"error": "timeout waiting for ACK/NAK"}

        writer.close()
        await writer.wait_closed()

        resp = data.decode(errors="ignore").strip()
        # print(f"[ELFIN] ← {resp}")

        if "ACK" in resp:
            return {"status": "ACK"}
        elif "NAK" in resp:
            return {"status": "NAK"}
        elif not resp:
            return {"error": "empty response"}
        else:
            return {"raw": resp}

    except Exception as e:
        return {"error": str(e)}


# === Enums для текстовых/выборных настроек ===

class BatteryTypeSetting(Enum):
    AGM = "PBT00"
    FLOODED = "PBT01"
    USER = "PBT02"
    LIFEP04 = "PBT03"  # если поддерживается

class OutputSourcePrioritySetting(Enum):
    UTILITY_FIRST = "POP00"
    SBU_PRIORITY = "POP01"
    SOLAR_FIRST = "POP02"

class ChargeSourcePrioritySetting(Enum):
    UTILITY_FIRST = "PCP00"
    SOLAR_FIRST = "PCP01"
    SOLAR_AND_UTILITY = "PCP02"

# class InputVoltageRangeSetting(Enum):
#     APL = "PGR00"
#     UPS = "PGR01"

# === Функции-хелперы ===

async def set_battery_type(device: str, battery_type: BatteryTypeSetting) -> dict:
    return await set_direct_data(device, battery_type.value)

async def set_output_source_priority(device: str, mode: OutputSourcePrioritySetting) -> dict:
    return await set_direct_data(device, mode.value)

async def set_charge_source_priority(device: str, mode: ChargeSourcePrioritySetting) -> dict:
    return await set_direct_data(device, mode.value)

# async def set_input_voltage_range(device: str, mode: InputVoltageRangeSetting) -> dict:
#     return await set_direct_data(device, mode.value)

async def set_battery_bulk_voltage(device: str, voltage: float) -> dict:
    cmd = f"PBAV{voltage:.2f}"
    return await set_direct_data(device, cmd)

async def set_battery_float_voltage(device: str, voltage: float) -> dict:
    cmd = f"PBFV{voltage:.2f}"
    return await set_direct_data(device, cmd)

async def set_rated_battery_voltage(device: str, voltage: int) -> dict:
    cmd = f"PBRV{voltage}"
    return await set_direct_data(device, cmd)

async def set_max_combined_charge_current(device: str, amps: int) -> dict:
    cmd = f"MCHGC{amps:03d}"
    return await set_direct_data(device, cmd)

async def set_battery_charge_current(device: str, amps: int) -> dict:
    cmd = f"PBATC{amps:03d}"
    return await set_direct_data(device, cmd)

async def set_max_utility_charge_current(device: str, amps: int) -> dict:
    cmd = f"MUCHGC{amps:03d}"
    return await set_direct_data(device, cmd)

@unique
class DeviceStatusBitsB7B0(IntEnum):
    FAULT = 1 << 7  # b7
    RESERVED_B6 = 1 << 6  # b6
    BUS_OVER = 1 << 5  # b5
    LINE_FAIL = 1 << 4  # b4
    BATTERY_LOW = 1 << 3  # b3
    BATTERY_HIGH = 1 << 2  # b2
    INVERTER_OVERLOAD = 1 << 1  # b1
    INVERTER_ON = 1 << 0  # b0


@unique
class DeviceStatusBitsB10B8(IntEnum):
    CHARGING_TO_BATTERY = 1 << 2  # b10
    CHARGING_AC_ACTIVE = 1 << 1  # b9
    CHARGING_SCC_ACTIVE = 1 << 0  # b8

def _extract_bits(raw: str, count: int) -> str:
    """Очищает строку, оставляя только 0/1, и возвращает ровно count бит."""
    bits = [c for c in (raw or "") if c in "01"][:count]
    return "".join(bits).rjust(count, "0")


def parse_device_status_bits_b7_b0(raw: str) -> dict:
    bits = "".join(c for c in (raw or "") if c in "01")[:8].rjust(8, "0")
    value = int(bits, 2)
    return {
        "fault": bool(value & DeviceStatusBitsB7B0.FAULT),
        "line_fail": bool(value & DeviceStatusBitsB7B0.LINE_FAIL),
        "bus_over": bool(value & DeviceStatusBitsB7B0.BUS_OVER),
        # "bus_under": bool(value & DeviceStatusBitsB7B0.BUS_UNDER),
        "battery_low": bool(value & DeviceStatusBitsB7B0.BATTERY_LOW),
        "battery_high": bool(value & DeviceStatusBitsB7B0.BATTERY_HIGH),
        "inverter_overload": bool(value & DeviceStatusBitsB7B0.INVERTER_OVERLOAD),
        "inverter_on": bool(value & DeviceStatusBitsB7B0.INVERTER_ON),
        "_raw_b7_b0": bits,
    }

def parse_device_status_bits_b10_b8(raw: str) -> dict:
    bits = "".join(c for c in (raw or "") if c in "01")[:3].rjust(3, "0")
    value = int(bits, 2)
    return {
        "charging_to_battery": bool(value & DeviceStatusBitsB10B8.CHARGING_TO_BATTERY),
        "charging_scc_active": bool(value & DeviceStatusBitsB10B8.CHARGING_SCC_ACTIVE),
        "charging_ac_active":  bool(value & DeviceStatusBitsB10B8.CHARGING_AC_ACTIVE),
        "_raw_b10_b8": bits,
    }