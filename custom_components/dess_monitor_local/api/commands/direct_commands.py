import asyncio
import re
import struct
from enum import Enum, unique, IntEnum

import serial_asyncio_fast as serial_asyncio

# ==========================
# ВСПОМОГАТЕЛЬНЫЕ ДЕКОДЕРЫ
# ==========================
# ==========================
# COMPAT LAYER FOR HA SELECTS
# ==========================
from enum import Enum

class OutputSourcePrioritySetting(Enum):
    UTILITY_FIRST = "POP00"
    SBU_PRIORITY = "POP01"
    SOLAR_FIRST = "POP02"

class ChargeSourcePrioritySetting(Enum):
    UTILITY_FIRST = "PCP00"
    SOLAR_FIRST = "PCP01"
    SOLAR_AND_UTILITY = "PCP02"

async def set_output_source_priority(device: str, mode: OutputSourcePrioritySetting) -> dict:
    """
    Backward-compatible API for HA selects.
    Voltronic: POPxx
    SUNPOLO: если ваш протокол поддерживает POP/PCP — тоже сработает (CRC16+CR),
             иначе вернёт NAK/FAIL в set_direct_data.
    """
    return await set_direct_data(device, mode.value)

async def set_charge_source_priority(device: str, mode: ChargeSourcePrioritySetting) -> dict:
    """
    Voltronic: PCPxx
    SUNPOLO: аналогично — зависит от поддержки команды в вашей прошивке.
    """
    return await set_direct_data(device, mode.value)

async def set_max_utility_charge_current(device: str, amps: int) -> dict:
    """
    Voltronic: MUCHGCxxx
    SUNPOLO: если поддерживает MUCHGC — ок.
    """
    cmd = f"MUCHGC{int(amps):03d}"
    return await set_direct_data(device, cmd)

def decode_ascii_response(hex_string: str) -> str:
    """Преобразовать строку 'AA BB CC' в ASCII."""
    hex_values = hex_string.strip().split()
    byte_values = bytes(int(b, 16) for b in hex_values)
    ascii_str = byte_values.decode("ascii", errors="ignore").strip()
    if ascii_str.startswith("("):
        ascii_str = ascii_str[1:]
    return ascii_str


def is_hex_string(s: str) -> bool:
    """Проверяет, состоит ли строка только из hex-символов или байтов в формате 'AA BB CC'."""
    s = s.strip().replace(" ", "")
    return bool(re.fullmatch(r"[0-9A-Fa-f]+", s)) and len(s) % 2 == 0


# ==========================
# CRC ДЛЯ VOLTRONIC / SUNPOLO ASCII
# ==========================


def crc16(data: bytes) -> bytes:
    """CRC16, как в протоколе Voltronic/Axpert/SUNPOLO (ASCII-команды)."""
    crc = 0
    for b in data:
        x = (crc >> 8) ^ b
        x ^= x >> 4
        crc = ((crc << 8) ^ (x << 12) ^ (x << 5) ^ x) & 0xFFFF
    return struct.pack(">H", crc)


# ==========================
# ENUMS / TRANSFORMS
# ==========================


class BatteryType(Enum):
    AGM = "0"
    Flooded = "1"
    UserDefined = "2"
    Pylontech = "3"
    WECO = "5"
    Soltaro = "6"
    LIB = "8"
    LIC = "9"


class ACInputVoltageRange(Enum):
    Appliance = "0"
    UPS = "1"


class OutputSourcePriority(Enum):
    SolarUtilityBattery = "0"  # Solar-Utility-Battery
    SolarBatteryUtility = "1"  # Solar-Battery-Utility


class ChargerSourcePriority(Enum):
    SolarFirst = "0"
    SolarAndUtility = "1"
    OnlySolar = "2"


class ParallelMode(Enum):
    OffGrid = "0"
    Hybrid = "1"


class Topology(Enum):
    Transformerless = "0"
    Transformer = "1"


class SunpoloInverterMode(Enum):
    PowerOn = "00"
    Standby = "01"
    Bypass = "02"
    Battery = "03"
    Fault = "04"
    Hybrid = "05"


# --- Ваши старые Enums (оставляю) ---
class OutputSourcePriorityVoltronic(Enum):
    UtilityFirst = "0"  # сеть
    SolarFirst = "1"
    SBU = "2"  # Solar → Battery → Utility
    BatteryOnly = "4"
    UtilityOnly = "5"
    SolarAndUtility = "6"
    Smart = "7"


class ChargerSourcePriorityVoltronic(Enum):
    UtilityFirst = "0"
    SolarFirst = "1"
    SolarAndUtility = "2"
    OnlySolar = "3"


class ParallelModeVoltronic(Enum):
    Master = "0"
    Slave = "1"
    Standalone = "2"


class OperatingModeVoltronic(Enum):
    PowerOn = "P"
    Standby = "S"
    Line = "L"
    Battery = "B"
    ShutdownApproaching = "D"
    Fault = "F"


def transform_qpiri_value(index: int, value: str) -> str:
    """Legacy transform для классического QPIRI (Voltronic)."""
    try:
        match index:
            case 12:
                return BatteryType(value).name
            case 15:
                return ACInputVoltageRange(value).name
            case 16:
                return OutputSourcePriorityVoltronic(value).name
            case 17:
                return ChargerSourcePriorityVoltronic(value).name
            case 21:
                return ParallelModeVoltronic(value).name
            case _:
                return value
    except ValueError:
        return value


# ==========================
# DECODERS: VOLTRONIC (Legacy)
# ==========================


def decode_qpigs(ascii_str: str) -> dict:
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
        "reserved_cccc",
    ]
    return dict(zip(fields, values))


def decode_qpigs2(ascii_str: str) -> dict:
    values = ascii_str.split()
    fields = [
        "pv_current",
        "pv_voltage",
        "pv_daily_energy",
    ]
    return dict(zip(fields, values))


def decode_qpiri(ascii_str: str) -> dict:
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
        "reserved_ccc",
    ]
    return {
        field: transform_qpiri_value(i, value)
        for i, (field, value) in enumerate(zip(fields, values))
    }


def decode_qmod(ascii_str: str) -> dict:
    mode_code = ascii_str.strip()[0]
    try:
        mode = OperatingModeVoltronic(mode_code)
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
        "elapsed_time",
    ]
    return dict(zip(fields, values))


# ==========================
# DECODERS: SUNPOLO6K (NEW)
# ==========================


def _strip_sunpolo_ascii(text: str) -> str:
    """
    У SUNPOLO ответ выглядит как:
      ^D106....<CRC16><CR>
    Мы в протоколе уже отрезаем CRC16 bytes; тут только чистим скобки/CR/LF.
    """
    s = (text or "").strip()
    s = s.replace("\r", "").replace("\n", "")
    s = s.strip().replace("(", "").replace(")", "")
    return s


def _csv_parts(s: str) -> list[str]:
    return [p.strip() for p in s.split(",") if p is not None]


def _to_int(x: str):
    try:
        return int(x)
    except Exception:
        return None


def _div10(x: str):
    try:
        return float(x) / 10.0
    except Exception:
        return None


def decode_p005gs(ascii_str: str) -> dict:
    """
    ^P005GS -> Device: ^D106BBBB,CCC,DDDD,EEE,FFFF,GGGG,HHH,III,JJJ,KKK,LLL,MMM,NNN,OOO,PPP,QQQ,RRRR,SSSS,TTTT,UUUU,V,W,X,Y,Z,a,b,c
    """
    s = _strip_sunpolo_ascii(ascii_str)
    if s.startswith("^D106"):
        s = s[5:]
    s = s.strip().strip(",")

    parts = _csv_parts(s)

    def get(i, default=""):
        return parts[i] if i < len(parts) else default

    out = {
        # BBBB,CCC,DDDD,EEE,FFFF,GGGG,HHH,III
        "grid_voltage_v": _div10(get(0)),
        "grid_frequency_hz": _div10(get(1)),
        "ac_output_voltage_v": _div10(get(2)),
        "ac_output_frequency_hz": _div10(get(3)),
        "output_apparent_power_va": _to_int(get(4)),
        "output_active_power_w": _to_int(get(5)),
        "load_percent": _to_int(get(6)),
        "battery_voltage_v": _div10(get(7)),

        # LLL,MMM,NNN,OOO
        "battery_discharge_current_a": _to_int(get(10)),
        "battery_charge_current_a": _to_int(get(11)),
        "battery_capacity_percent": _to_int(get(12)),
        "inverter_heatsink_temp_c": _to_int(get(13)),

        # PV watts/volts (RRRR,SSSS,TTTT,UUUU)
        "pv1_power_w": _to_int(get(16)),
        "pv2_power_w": _to_int(get(17)),
        "pv1_voltage_v": _div10(get(18)),
        "pv2_voltage_v": _div10(get(19)),
    }

    # сохранить неизвестные JJJ/KKK/PPP/QQQ и хвостовые флаги
    out["raw_fields"] = parts
    return out


def decode_p007piri(ascii_str: str) -> dict:
    """
    ^P007PIRI -> Device: ^D088BBBB,CCC,DDDD,EEE,FFF,GGGG,HHHH,III,JJJ,KKK,LLL,MMM,NNN,O,PPP,QQQ,R,S,T,U,V,W,X,Y,Z,aa
    """
    s = _strip_sunpolo_ascii(ascii_str)
    if s.startswith("^D088"):
        s = s[5:]
    s = s.strip().strip(",")

    parts = _csv_parts(s)

    def get(i, default=""):
        return parts[i] if i < len(parts) else default

    # Преобразования:
    # BBBB=2300 -> 230.0V; CCC=243 -> 24.3A; EEE=500 -> 50.0Hz; III=480 -> 48.0V; MMM=564 -> 56.4V etc.
    out = {
        "rated_grid_voltage_v": _div10(get(0)),
        "rated_grid_current_a": _div10(get(1)),
        "rated_output_voltage_v": _div10(get(2)),
        "rated_output_frequency_hz": _div10(get(3)),
        "rated_output_current_a": _div10(get(4)),
        "rated_output_apparent_power_va": _to_int(get(5)),
        "rated_output_active_power_w": _to_int(get(6)),
        "rated_battery_voltage_v": _div10(get(7)),
        "battery_recharge_voltage_v": _div10(get(8)),
        "battery_full_restore_discharge_voltage_v": _div10(get(9)),
        "battery_under_voltage_v": _div10(get(10)),
        "battery_bulk_voltage_v": _div10(get(11)),
        "battery_float_voltage_v": _div10(get(12)),
        "battery_type": (BatteryType(get(13)).name if get(13) in {e.value for e in BatteryType} else get(13)),
        "max_ac_charging_current_a": _to_int(get(14)),
        "max_charging_current_a": _to_int(get(15)),
        "input_voltage_range": (ACInputVoltageRange(get(16)).name if get(16) in {e.value for e in ACInputVoltageRange} else get(16)),
        "output_source_priority": (OutputSourcePriority(get(17)).name if get(17) in {e.value for e in OutputSourcePriority} else get(17)),
        "charger_source_priority": (ChargerSourcePriority(get(18)).name if get(18) in {e.value for e in ChargerSourcePriority} else get(18)),
        "parallel_max_number": _to_int(get(19)),
        "type": (ParallelMode(get(20)).name if get(20) in {e.value for e in ParallelMode} else get(20)),
        "topology": (Topology(get(21)).name if get(21) in {e.value for e in Topology} else get(21)),
        "output_mode": _to_int(get(22)),  # 0 single, 1 parallel, 2/3/4 3-phase roles
        "solar_supply_priority": _to_int(get(23)),
        "country_customized_regulations": get(25),
    }
    out["raw_fields"] = parts
    return out


def decode_p006mod(ascii_str: str) -> dict:
    """
    ^P006MOD -> Device: ^D005BB
      BB:
        00 Power on
        01 Standby
        02 Bypass
        03 Battery
        04 Fault
        05 Hybrid
    """
    s = _strip_sunpolo_ascii(ascii_str)
    if s.startswith("^D005"):
        s = s[5:]
    s = s.strip()
    bb = s[:2] if len(s) >= 2 else s
    try:
        mode = SunpoloInverterMode(bb).name
    except Exception:
        mode = "Unknown"
    return {"inverter_mode": mode, "raw": bb}


def decode_sunpolo_model(ascii_str: str) -> dict:
    """
    QMN -> SUNPOLO 6K: ((AAIII-6000
    В доке ответ начинается с '(('.
    """
    s = _strip_sunpolo_ascii(ascii_str)
    s = s.lstrip("(")
    return {"model": s.strip()}


# ==========================
# УНИВЕРСАЛЬНЫЙ ПАРСЕР
# ==========================


def decode_direct_response(command: str, input_str: str) -> dict:
    """
    Универсальный парсер: определяет, hex это или ASCII, и декодирует в dict.
    Поддерживает:
      - классический Voltronic: QPIGS/QPIRI/QMOD/...
      - SUNPOLO6K: ^P005GS/^P007PIRI/^P006MOD/QMN/...
    """
    if not input_str:
        return {"error": "empty response"}

    if input_str == "null":
        return {"error": "null response received. Command not accepted."}

    # если это hex — сначала декодируем в ASCII
    if is_hex_string(input_str):
        ascii_str = decode_ascii_response(input_str)
    else:
        ascii_str = input_str.strip()

    # убираем скобки и CR/LF
    ascii_str = ascii_str.strip().replace("\r", "").replace("\n", "")

    if ascii_str.startswith("NAK") or "NAK" in ascii_str:
        return {"error": "NAK response received. Command not accepted."}

    cmd = (command or "").strip()

    # --- SUNPOLO ---
    if cmd.upper() == "^P005GS":
        return decode_p005gs(ascii_str)
    if cmd.upper() == "^P007PIRI":
        return decode_p007piri(ascii_str)
    if cmd.upper() == "^P006MOD":
        return decode_p006mod(ascii_str)
    if cmd.upper() == "QMN":
        return decode_sunpolo_model(ascii_str)

    # --- Legacy Voltronic ---
    match cmd.upper():
        case "QPIGS":
            # Voltronic ответы чаще в формате "(...." и разделены пробелами
            cleaned = ascii_str.strip().replace("(", "").replace(")", "")
            return decode_qpigs(cleaned)
        case "QPIGS2":
            cleaned = ascii_str.strip().replace("(", "").replace(")", "")
            return decode_qpigs2(cleaned)
        case "QPIRI":
            cleaned = ascii_str.strip().replace("(", "").replace(")", "")
            return decode_qpiri(cleaned)
        case "QMOD":
            cleaned = ascii_str.strip().replace("(", "").replace(")", "")
            return decode_qmod(cleaned)
        case "QID" | "QSID":
            cleaned = ascii_str.strip().replace("(", "").replace(")", "")
            return decode_qid(cleaned)
        case "QFLAG":
            cleaned = ascii_str.strip().replace("(", "").replace(")", "")
            return decode_qflag(cleaned)
        case "QVFW":
            cleaned = ascii_str.strip().replace("(", "").replace(")", "")
            return decode_qvfw(cleaned)
        case "QBEQI":
            cleaned = ascii_str.strip().replace("(", "").replace(")", "")
            return decode_qbeqi(cleaned)
        case _:
            return {"Raw": ascii_str}


# ==========================
# КАРТЫ КОМАНД
# ==========================

# SUNPOLO6K команды (ASCII, CRC добавляем сами)
sunpolo_commands = {
    "P005GS": "^P005GS",
    "PIRI": "^P007PIRI",
    "MOD": "^P006MOD",
    "FWS": "^P006FWS",
    "VFW": "^P006VFW",
    "FLAG": "^P007FLAG",
    "ID": "^P005ID",
    "DI": "^P005DI",
    "QMN": "QMN",
}

# Legacy voltronic команды (ASCII, CRC добавляем сами)
voltronic_commands = {
    "QPIGS": "QPIGS",
    "QPIGS2": "QPIGS2",
    "QPIRI": "QPIRI",
    "QMOD": "QMOD",
    "QPIWS": "QPIWS",
    "QVFW": "QVFW",
    "QMCHGCR": "QMCHGCR",
    "QMUCHGCR": "QMUCHGCR",
    "QFLAG": "QFLAG",
    "QSID": "QSID",
    "QID": "QID",
    "QMN": "QMN",
    "QBEQI": "QBEQI",
}

def get_command_ascii(command_name: str) -> str:
    name = (command_name or "").upper()
    if name in sunpolo_commands:
        return sunpolo_commands[name]
    if name in voltronic_commands:
        return voltronic_commands[name]
    return "Unknown command"


# ==========================
# ПРОТОКОЛЫ TCP/Serial
# ==========================

class VoltronicTCPProtocol(asyncio.Protocol):
    """Классический Voltronic/Axpert TCP: ASCII-команда + CRC16 + CR; ответ обычно ASCII до CR."""
    def __init__(self, command_ascii: str, on_response):
        self.transport = None
        self.command_ascii = command_ascii
        self.on_response = on_response
        self.buffer = bytearray()

    def connection_made(self, transport):
        self.transport = transport
        cmd = self.command_ascii.encode("ascii")
        packet = cmd + crc16(cmd) + b"\r"
        self.transport.write(packet)

    def data_received(self, data: bytes):
        self.buffer.extend(data)
        if b"\r" in self.buffer or b"\n" in self.buffer:
            raw = self.buffer.split(b"\r", 1)[0].strip()
            try:
                response = raw.decode(errors="ignore")
                self.on_response(response, None)
            except Exception as e:
                self.on_response(None, e)
            if self.transport:
                self.transport.close()

    def connection_lost(self, exc):
        if exc:
            self.on_response(None, exc)


class SunpoloTCPProtocol(asyncio.Protocol):
    """
    SUNPOLO6K TCP:
      TX: b'^P005GS' + CRC16 + b'\r'
      RX: b'^D106....' + CRC16(2 bytes) + b'\r'
    """
    def __init__(self, command_ascii: str, on_response):
        self.transport = None
        self.command_ascii = command_ascii
        self.on_response = on_response
        self.buffer = bytearray()

    def connection_made(self, transport):
        self.transport = transport
        cmd = self.command_ascii.encode("ascii")
        packet = cmd + crc16(cmd) + b"\r"
        self.transport.write(packet)

    def data_received(self, data: bytes):
        self.buffer.extend(data)
        if b"\r" not in self.buffer:
            return

        frame = self.buffer.split(b"\r", 1)[0]  # до CR

        try:
            # В конце: 2 байта CRC16 ответа. Отрезаем их.
            payload = frame[:-2] if len(frame) >= 3 else frame
            text = payload.decode("ascii", errors="ignore")
            text = text.replace("\n", "").replace("\r", "")
            self.on_response(text, None)
        except Exception as e:
            self.on_response(None, e)
        finally:
            if self.transport:
                self.transport.close()

    def connection_lost(self, exc):
        if exc:
            self.on_response(None, exc)


class SerialCommandProtocol(asyncio.Protocol):
    """Классический Voltronic по UART/USB: ASCII-команды с CRC."""
    def __init__(self, command_ascii: str, on_response):
        self.transport = None
        self.command_ascii = command_ascii
        self.on_response = on_response
        self.buffer = bytearray()

    def connection_made(self, transport):
        self.transport = transport
        cmd = self.command_ascii.encode("ascii")
        packet = cmd + crc16(cmd) + b"\r"
        self.transport.write(packet)

    def data_received(self, data: bytes):
        self.buffer.extend(data)
        if b"\r" in self.buffer:
            raw = self.buffer.split(b"\r", 1)[0].strip()
            try:
                response = raw.decode(errors="ignore")
                self.on_response(response, None)
            except Exception as e:
                self.on_response(None, e)
            if self.transport:
                self.transport.close()

    def connection_lost(self, exc):
        if exc:
            self.on_response(None, exc)


# ==========================
# MODBUS RTU-over-TCP (SMG-II) (ваш код без изменений)
# ==========================

UNIT_ID = 1  # для SMG-II обычно 1


def i16(v: int) -> int:
    return v - 65536 if v >= 32768 else v


def modbus_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


async def _read_modbus_block(
    host: str,
    port: int,
    start: int,
    count: int,
    unit_id: int = UNIT_ID,
    timeout: float = 30.0,
) -> list[int]:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout
    )

    try:
        req = bytearray()
        req.append(unit_id & 0xFF)
        req.append(3)
        req.append((start >> 8) & 0xFF)
        req.append(start & 0xFF)
        req.append((count >> 8) & 0xFF)
        req.append(count & 0xFF)
        crc = modbus_crc16(bytes(req))
        req.append(crc & 0xFF)
        req.append((crc >> 8) & 0xFF)

        writer.write(req)
        await writer.drain()

        header2 = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
        if len(header2) < 2:
            raise Exception("Short Modbus header")

        uid, func = header2[0], header2[1]
        if uid != unit_id:
            raise Exception(f"Unexpected unit id: {uid}")

        if func & 0x80:
            exc_and_crc = await asyncio.wait_for(reader.readexactly(3), timeout=timeout)
            exc_code = exc_and_crc[0]
            raise Exception(f"Modbus exception {exc_code}")

        bc_bytes = await asyncio.wait_for(reader.readexactly(1), timeout=timeout)
        byte_count = bc_bytes[0]

        data_plus_crc = await asyncio.wait_for(
            reader.readexactly(byte_count + 2), timeout=timeout
        )
        if len(data_plus_crc) < byte_count + 2:
            raise Exception("Short Modbus data")

        data = data_plus_crc[:-2]
        crc_lo, crc_hi = data_plus_crc[-2], data_plus_crc[-1]
        recv_crc = crc_lo | (crc_hi << 8)

        calc_crc = modbus_crc16(header2 + bc_bytes + data)
        if recv_crc != calc_crc:
            raise Exception("Modbus CRC mismatch")

        if byte_count % 2 != 0:
            raise Exception("Modbus byte_count not even")

        regs: list[int] = []
        for i in range(0, min(byte_count, count * 2), 2):
            regs.append((data[i] << 8) | data[i + 1])

        return regs

    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _write_modbus_single_register(
    host: str,
    port: int,
    address: int,
    value: int,
    unit_id: int = UNIT_ID,
    timeout: float = 30.0,
) -> dict:
    async def _send_once(func_code: int) -> dict:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        try:
            req = bytearray()
            req.append(unit_id & 0xFF)
            req.append(func_code & 0xFF)
            req.append((address >> 8) & 0xFF)
            req.append(address & 0xFF)

            if func_code == 0x06:
                req.append((value >> 8) & 0xFF)
                req.append(value & 0xFF)
            elif func_code == 0x10:
                req.append(0x00)
                req.append(0x01)
                req.append(0x02)
                req.append((value >> 8) & 0xFF)
                req.append(value & 0xFF)
            else:
                raise ValueError(f"Unsupported func_code {func_code}")

            crc = modbus_crc16(bytes(req))
            req.append(crc & 0xFF)
            req.append((crc >> 8) & 0xFF)

            writer.write(req)
            await writer.drain()

            resp = await asyncio.wait_for(reader.readexactly(8), timeout=timeout)

            body = resp[:-2]
            crc_lo, crc_hi = resp[-2], resp[-1]
            recv_crc = crc_lo | (crc_hi << 8)
            calc_crc = modbus_crc16(body)
            if recv_crc != calc_crc:
                raise Exception("Modbus write CRC mismatch")

            uid, fback = body[0], body[1]
            if uid != unit_id:
                raise Exception(f"Unexpected unit id in write response: {uid}")

            if fback & 0x80:
                exc_code = body[2]
                raise Exception(f"Modbus exception in write: {exc_code}")

            return {"status": "OK", "func": func_code}

        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    try:
        return await _send_once(0x06)
    except Exception as e1:
        try:
            return await _send_once(0x10)
        except Exception as e2:
            return {"error": f"modbus write failed (0x06: {e1}, 0x10: {e2})"}


async def read_modbus_snapshot_async(host: str, port: int) -> tuple[dict, dict]:
    block_200 = await _read_modbus_block(host, port, 201, 31)

    def R200(addr: int) -> int:
        return block_200[addr - 201]

    OPERATION_MODES = {
        0: "Power On",
        1: "Standby",
        2: "Mains",
        3: "Off-Grid",
        4: "Bypass",
        5: "Charging",
        6: "Fault",
    }

    sensors = {
        "operation_mode": OPERATION_MODES.get(R200(201)),
        "mains_voltage": R200(202) / 10.0,
        "mains_frequency": R200(203) / 100.0,
        "mains_power": i16(R200(204)),
        "inverter_voltage": R200(205) / 10.0,
        "inverter_current": i16(R200(206)) / 10.0,
        "inverter_frequency": R200(207) / 100.0,
        "inverter_power": i16(R200(208)),
        "inverter_charge_power": i16(R200(209)),
        "output_voltage": R200(210) / 10.0,
        "output_current": i16(R200(211)) / 10.0,
        "output_frequency": R200(212) / 100.0,
        "output_active_power": i16(R200(213)),
        "battery_voltage": R200(215) / 10.0,
        "battery_current": i16(R200(216)) / 10.0,
        "battery_power": i16(R200(217)),
        "pv_voltage": R200(219) / 10.0,
        "pv_current": R200(220) / 10.0,
        "pv_power": i16(R200(223)),
        "pv_charge_power": i16(R200(224)),
        "load_percent": R200(225),
        "temp_dcdc": R200(226),
        "temp_inverter": R200(227),
    }

    block_300 = await _read_modbus_block(host, port, 300, 38, UNIT_ID, 30)  # TODO: change

    def R300(addr: int) -> int:
        return block_300[addr - 300]

    config = {
        "output_mode": R300(300),
        "output_priority": R300(301),
        "input_voltage_range": R300(302),
        "buzzer_mode": R300(303),
        "lcd_backlight": R300(305),
        "lcd_auto_return": R300(306),
        "energy_saving_mode": R300(307),
        "overload_auto_restart": R300(308),
        "overtemp_auto_restart": R300(309),
        "overload_transfer_to_bypass": R300(310),
        "battery_eq_enabled": R300(313),
        "output_voltage_setting": R300(320) / 10.0,
        "output_freq_setting": R300(321) / 100.0,
        "battery_ovp": R300(323) / 10.0,
        "max_charge_voltage": R300(324) / 10.0,
        "float_charge_voltage": R300(325) / 10.0,
        "battery_discharge_recovery_mains": R300(326) / 10.0,
        "battery_low_protection_mains": R300(327) / 10.0,
        "battery_low_protection_offgrid": R300(329) / 10.0,
        "battery_charging_priority": R300(331),
        "max_charging_current": R300(332) / 10.0,
        "max_mains_charging_current": R300(333) / 10.0,
        "eq_charging_voltage": R300(334) / 10.0,
        "eq_time_minutes": R300(335),
        "eq_timeout": R300(336),
        "eq_interval_days": R300(337),
    }

    return sensors, config


def modbus_to_qpigs(s: dict) -> dict:
    bus_voltage = 400

    battery_charging_current = max(0, int(s["battery_current"]))
    battery_discharge_current = max(0, int(-s["battery_current"]))

    pv_current_int = s["pv_current"]
    pv_voltage_value = s["pv_voltage"]
    return {
        "grid_voltage": f"{s['mains_voltage']:.1f}",
        "grid_frequency": f"{s['mains_frequency']:.1f}",
        "ac_output_voltage": f"{s['output_voltage']:.1f}",
        "ac_output_frequency": f"{s['output_frequency']:.2f}",
        "output_apparent_power": f"{abs(s['output_active_power']):04d}",
        "output_active_power": f"{abs(s['output_active_power']):04d}",
        "load_percent": f"{s['load_percent']:03d}",
        "bus_voltage": f"{bus_voltage}",
        "battery_voltage": f"{s['battery_voltage']:.2f}",
        "battery_charging_current": f"{battery_charging_current:03d}",
        "battery_capacity": "100",
        "inverter_heat_sink_temperature": f"{s['temp_inverter']:.1f}",
        "inverter_dcdc_module_temperature": f"{s['temp_dcdc']:.1f}",
        "pv_input_current": f"{pv_current_int:.1f}",
        "pv_input_voltage": f"{pv_voltage_value:.1f}",
        "scc_battery_voltage": f"{s['battery_voltage']:.2f}",
        "battery_discharge_current": f"{battery_discharge_current:05d}",
        "device_status_bits_b7_b0": "00010000",
        "battery_voltage_offset": "00",
        "eeprom_version": "00",
        "pv_charging_power": f"{int(s['pv_power']):05d}",
        "device_status_bits_b10_b8": "010",
        "grid_ac_in_power": f"{abs(s['mains_power']):05d}",
    }


def modbus_to_qpiri(c: dict) -> dict:
    ac_range_name = ACInputVoltageRange.UPS.name if c["input_voltage_range"] == 1 else ACInputVoltageRange.Appliance.name

    OUTPUT_PRIORITY = {
        0: OutputSourcePriorityVoltronic.UtilityFirst.name,
        1: OutputSourcePriorityVoltronic.SolarFirst.name,
        2: OutputSourcePriorityVoltronic.SBU.name,
    }
    output_priority = OUTPUT_PRIORITY.get(c["output_priority"], OutputSourcePriorityVoltronic.UtilityFirst.name)

    CHARGER_PRIORITY = {
        0: ChargerSourcePriorityVoltronic.UtilityFirst.name,
        1: ChargerSourcePriorityVoltronic.SolarFirst.name,
        2: ChargerSourcePriorityVoltronic.SolarAndUtility.name,
        3: ChargerSourcePriorityVoltronic.OnlySolar.name,
    }
    charger_priority = CHARGER_PRIORITY.get(c["battery_charging_priority"], ChargerSourcePriorityVoltronic.UtilityFirst.name)

    return {
        "rated_grid_voltage": "230.0",
        "rated_input_current": "15.2",
        "rated_ac_output_voltage": "230.0",
        "rated_output_frequency": "50.0",
        "rated_output_current": "15.2",
        "rated_output_apparent_power": "4000",
        "rated_output_active_power": "4000",
        "rated_battery_voltage": "24.0",
        "low_battery_to_ac_bypass_voltage": f"{c['battery_low_protection_mains']:.1f}",
        "shut_down_battery_voltage": f"{c['battery_low_protection_offgrid']:.1f}",
        "bulk_charging_voltage": f"{c['max_charge_voltage']:.1f}",
        "float_charging_voltage": f"{c['float_charge_voltage']:.1f}",
        "battery_type": "UserDefined",
        "max_utility_charging_current": f"{int(c['max_mains_charging_current']):02d}",
        "max_charging_current": f"{int(c['max_charging_current']):03d}",
        "ac_input_voltage_range": ac_range_name,
        "output_source_priority": output_priority,
        "charger_source_priority": charger_priority,
        "parallel_max_number": "6",
        "reserved_uu": "01",
        "reserved_v": "0",
        "parallel_mode": "Master",
        "high_battery_voltage_to_battery_mode": f"{c['battery_discharge_recovery_mains']:.1f}",
        "solar_work_condition_in_parallel": "0",
        "solar_max_charging_power_auto_adjust": "1_",
        "rated_battery_capacity": "200",
        "reserved_b": "0",
        "reserved_ccc": "0",
    }


# ==========================
# ГЛАВНАЯ ФУНКЦИЯ ЧТЕНИЯ (UPDATED)
# ==========================


async def get_direct_data(device: str, command_str: str, timeout: float = 30.0) -> dict:
    """
    Универсальный доступ к инвертору.

    • device = "modbus://host:port"  → SMG-II по Modbus RTU-over-TCP.
    • device = "tcp://host:port"    → Voltronic/Axpert/SUNPOLO через ELFIN/TCP.
    • device = "/dev/ttyUSB0"       → Voltronic по UART.

    Для SUNPOLO6K используйте команды:
      '^P005GS', '^P007PIRI', '^P006MOD', 'QMN', ...
    """

    command = command_str.strip()

    # ---- Виртуальные QPIGS/QPIRI/QMOD поверх Modbus (SMG-II) ----
    if device.startswith("modbus://"):
        try:
            _, addr = device.split("modbus://", 1)
            host, port_str = addr.split(":")
            port = int(port_str)
        except Exception:
            return {}

        try:
            sensors, config = await read_modbus_snapshot_async(host, port)
        except Exception:
            return {}

        cmd_upper = command.upper()
        if cmd_upper == "QPIGS":
            return modbus_to_qpigs(sensors)
        if cmd_upper == "QPIRI":
            return modbus_to_qpiri(config)
        if cmd_upper == "QMOD":
            om = (sensors.get("operation_mode") or "").lower()
            if any(x in om for x in ("mains", "bypass", "charging")):
                mode = OperatingModeVoltronic.Line
            elif any(x in om for x in ("off-grid", "offgrid", "off grid")):
                mode = OperatingModeVoltronic.Battery
            elif "standby" in om:
                mode = OperatingModeVoltronic.Standby
            elif "fault" in om:
                mode = OperatingModeVoltronic.Fault
            else:
                mode = OperatingModeVoltronic.PowerOn
            return {"operating_mode": mode}

        return {"sensors": sensors, "config": config}

    # ---- TCP/Serial ----
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    transport: asyncio.Transport | None = None

    def on_response(data, err):
        if not fut.done():
            fut.set_result(None if err else data)

    try:
        if device.startswith("tcp://"):
            _, addr = device.split("tcp://", 1)
            host, port_str = addr.split(":")
            port = int(port_str)

            # Определяем: SUNPOLO или классический Voltronic
            # SUNPOLO-команды обычно начинаются с '^P' или равны 'QMN'
            is_sunpolo = command.upper().startswith("^P") or command.upper() in {"QMN"}

            proto_cls = SunpoloTCPProtocol if is_sunpolo else VoltronicTCPProtocol

            transport, _ = await loop.create_connection(
                lambda: proto_cls(command, on_response),
                host,
                port,
            )
        else:
            # Serial оставляем legacy (Voltronic), но при желании можно также SUNPOLO по UART
            transport, _ = await serial_asyncio.create_serial_connection(
                loop,
                lambda: SerialCommandProtocol(command, on_response),
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

        if result and isinstance(result, str):
            try:
                parsed = decode_direct_response(command, result)
                return parsed or {}
            except Exception:
                return {}
        return {}
    finally:
        if transport:
            transport.close()


# ==========================
# УПРАВЛЯЮЩИЕ КОМАНДЫ (оставил как было)
# ==========================

async def set_direct_data(device: str, command_str: str, timeout: float = 30.0) -> dict:
    """
    Отправляет управляющую команду на классический Voltronic через TCP.
    Для SUNPOLO set-команды в доке начинаются с '^S...' (CRC16 + CR) — можно использовать этот же метод,
    просто передайте '^S...' как command_str.
    """
    if device.startswith("tcp://"):
        _, data = device.split("tcp://", 1)
        host, port_str = data.split(":")
        port = int(port_str)
    else:
        return {"error": "only tcp://host:port supported for set_direct_data"}

    try:
        reader, writer = await asyncio.open_connection(host, port)
        cmd = command_str.strip().encode("ascii")
        packet = cmd + crc16(cmd) + b"\r"

        writer.write(packet)
        await writer.drain()

        try:
            data = await asyncio.wait_for(reader.read(256), timeout=timeout)
        except asyncio.TimeoutError:
            return {"error": "timeout waiting for ACK/NAK"}

        writer.close()
        await writer.wait_closed()

        # Для SUNPOLO ответ может быть '^1'/'^0' + CRC bytes; в ASCII может выглядеть странно.
        resp = data.decode(errors="ignore").strip()

        if "ACK" in resp:
            return {"status": "ACK"}
        elif "NAK" in resp:
            return {"status": "NAK"}
        elif resp.startswith("^1"):
            return {"status": "OK"}
        elif resp.startswith("^0"):
            return {"status": "FAIL"}
        elif not resp:
            return {"error": "empty response"}
        else:
            return {"raw": resp}

    except Exception as e:
        return {"error": str(e)}


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
