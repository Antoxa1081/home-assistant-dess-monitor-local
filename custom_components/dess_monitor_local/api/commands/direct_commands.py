import asyncio
import re
import struct
from enum import Enum, unique, IntEnum

import serial_asyncio_fast as serial_asyncio

# ==========================
# ВСПОМОГАТЕЛЬНЫЕ ДЕКОДЕРЫ
# ==========================


def decode_ascii_response(hex_string: str) -> str:
    """Преобразовать строку 'AA BB CC' в ASCII."""
    hex_values = hex_string.strip().split()
    byte_values = bytes(int(b, 16) for b in hex_values)
    ascii_str = byte_values.decode("ascii", errors="ignore").strip()
    if ascii_str.startswith("("):
        ascii_str = ascii_str[1:]
    return ascii_str


def decode_qpigs(ascii_str: str) -> dict:
    """Разбор ответа QPIGS (ASCII) в dict."""
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
    UtilityFirst = "0"  # сеть
    SolarFirst = "1"
    SBU = "2"  # Solar → Battery → Utility
    BatteryOnly = "4"
    UtilityOnly = "5"
    SolarAndUtility = "6"
    Smart = "7"  # в некоторых прошивках


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
        mode = OperatingMode(mode_code)
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


def is_hex_string(s: str) -> bool:
    """Проверяет, состоит ли строка только из hex-символов или байтов в формате 'AA BB CC'."""
    s = s.strip().replace(" ", "")
    return bool(re.fullmatch(r"[0-9A-Fa-f]+", s)) and len(s) % 2 == 0


def decode_direct_response(command: str, input_str: str) -> dict:
    """
    Универсальный парсер: определяет, hex это или ASCII, и декодирует
    в dict в зависимости от команды.
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


# ==========================
# CRC ДЛЯ VOLTRONIC ASCII
# ==========================


def crc16(data: bytes) -> bytes:
    """CRC16, как в протоколе Voltronic (QPIGS/QPI/QMOD)."""
    crc = 0
    for b in data:
        x = (crc >> 8) ^ b
        x ^= x >> 4
        crc = ((crc << 8) ^ (x << 12) ^ (x << 5) ^ x) & 0xFFFF
    return struct.pack(">H", crc)


# ==========================
# КЛАССЫ ПРОТОКОЛОВ VOLTRONIC
# ==========================


class ElfinTCPProtocol(asyncio.Protocol):
    """Классический Voltronic-по-TCP (Elfin) протокол: ASCII-команды с CRC."""

    def __init__(self, command: str, on_response):
        self.transport = None
        self.command = command.upper()
        self.command_bytes = command.encode("ascii")
        self.on_response = on_response
        self.buffer = bytearray()

    def connection_made(self, transport):
        self.transport = transport
        packet = self.command_bytes + crc16(self.command_bytes) + b"\r"
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


class SerialCommandProtocol(asyncio.Protocol):
    """Классический Voltronic по UART/USB: ASCII-команды с CRC."""

    def __init__(self, command: str, on_response):
        self.transport = None
        self.command = command.upper()
        self.command_bytes = command.encode("ascii")
        self.on_response = on_response
        self.buffer = bytearray()

    def connection_made(self, transport):
        self.transport = transport
        packet = self.command_bytes + crc16(self.command_bytes) + b"\r"
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
# MODBUS RTU-over-TCP (SMG-II)
# ==========================

UNIT_ID = 1  # для SMG-II обычно 1


def i16(v: int) -> int:
    """Преобразование 16-битного регистра в signed int."""
    return v - 65536 if v >= 32768 else v


def modbus_crc16(data: bytes) -> int:
    """Классический CRC16 Modbus (poly 0xA001, init 0xFFFF)."""
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
    """
    Async Modbus RTU-over-TCP:
      • открываем TCP-сокет
      • шлём RTU-запрос 0x03
      • читаем ответ
    НИГДЕ не используется time.sleep и pyserial.
    """
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout
    )

    try:
        # Собираем запрос: [id][func=3][addr_hi][addr_lo][cnt_hi][cnt_lo][crc_lo][crc_hi]
        req = bytearray()
        req.append(unit_id & 0xFF)
        req.append(3)  # Read Holding Registers
        req.append((start >> 8) & 0xFF)
        req.append(start & 0xFF)
        req.append((count >> 8) & 0xFF)
        req.append(count & 0xFF)
        crc = modbus_crc16(bytes(req))
        req.append(crc & 0xFF)        # CRC low
        req.append((crc >> 8) & 0xFF)  # CRC high

        writer.write(req)
        await writer.drain()

        # Читаем первые 2 байта: id, func
        header2 = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
        if len(header2) < 2:
            raise Exception("Short Modbus header")

        uid, func = header2[0], header2[1]
        if uid != unit_id:
            raise Exception(f"Unexpected unit id: {uid}")

        # Ошибка Modbus: func | 0x80 и один байт кода ошибки
        if func & 0x80:
            exc_and_crc = await asyncio.wait_for(reader.readexactly(3), timeout=timeout)
            exc_code = exc_and_crc[0]
            raise Exception(f"Modbus exception {exc_code}")

        # Нормальный ответ: [id][func][byte_count][data...][crc_lo][crc_hi]
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
    """
    Запись одного Holding-регистра Modbus RTU-over-TCP.
    Сначала пробуем func = 0x06 (Write Single Register),
    если инвертор молчит/рвёт соединение — пробуем func = 0x10 (Write Multiple, qty=1).

    ВО ВСЕХ СЛУЧАЯХ:
    - не кидаем исключения наружу, возвращаем {'status': 'OK'} или {'error': '...'}
    """

    async def _send_once(func_code: int) -> dict:
        reader: asyncio.StreamReader
        writer: asyncio.StreamWriter

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
                # Write Single Register
                req.append((value >> 8) & 0xFF)
                req.append(value & 0xFF)
            elif func_code == 0x10:
                # Write Multiple Registers (qty = 1)
                req.append(0x00)  # qty_hi
                req.append(0x01)  # qty_lo
                req.append(0x02)  # byte_count
                req.append((value >> 8) & 0xFF)
                req.append(value & 0xFF)
            else:
                raise ValueError(f"Unsupported func_code {func_code}")

            crc = modbus_crc16(bytes(req))
            req.append(crc & 0xFF)         # CRC lo
            req.append((crc >> 8) & 0xFF)  # CRC hi

            writer.write(req)
            await writer.drain()

            # Ответ RTU:
            #   [id][func][addr_hi][addr_lo][val_hi/qty_hi][val_lo/qty_lo][crc_lo][crc_hi]
            # т.е. всего 8 байт
            resp = await asyncio.wait_for(reader.readexactly(8), timeout=timeout)

            if len(resp) != 8:
                raise Exception(f"Short Modbus write response: {len(resp)} bytes")

            # CRC проверяем
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

            # Адрес можно дополнительно сверить, но это уже не критично
            return {"status": "OK", "func": func_code}

        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    # Сначала пробуем 0x06
    try:
        return await _send_once(0x06)
    except Exception as e1:
        # Если Elfin/инвертор молчит на 0x06 — пробуем 0x10
        try:
            return await _send_once(0x10)
        except Exception as e2:
            return {
                "error": f"modbus write failed (0x06: {e1}, 0x10: {e2})"
            }

async def read_modbus_snapshot_async(host: str, port: int) -> tuple[dict, dict]:
    """
    Считывает два блока:
      • 201–231: сенсоры
      • 300–337: конфигурация
    Через чистый asyncio Modbus RTU-over-TCP.
    """
    # --- блок 201–231 (сенсоры) ---
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

    # --- блок 300–337 (конфиг) ---
    block_300 = await _read_modbus_block(host, port, 300, 38, UNIT_ID, 30) # TODO: change

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
    """Преобразует данные MODBUS (sensors) в структуру, максимально похожую на decode_qpigs()."""
    bus_voltage = 400  # у SMG-II нет отдельного bus voltage

    # у тебя тут знак уже подогнан под SMG, я оставляю как было
    battery_charging_current = max(0, int(s["battery_current"]))   # заряд +
    battery_discharge_current = max(0, int(-s["battery_current"]))  # разряд +

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
        "battery_capacity": "100",  # SOC можно позже взять из отдельного регистра
        "inverter_heat_sink_temperature": f"{s['temp_inverter']:.1f}", ### or temp_dcdc
        "inverter_dcdc_module_temperature": f"{s['temp_dcdc']:.1f}", ### or temp_dcdc
        "pv_input_current": f"{pv_current_int:.1f}",
        "pv_input_voltage": f"{pv_voltage_value:.1f}",
        "scc_battery_voltage": f"{s['battery_voltage']:.2f}",
        "battery_discharge_current": f"{battery_discharge_current:05d}",
        "device_status_bits_b7_b0": "00010000",
        "battery_voltage_offset": "00",
        "eeprom_version": "00",
        "pv_charging_power": f"{int(s['pv_charge_power']):05d}",
        "device_status_bits_b10_b8": "010",
        "grid_ac_in_power": f"{abs(s['mains_power']):05d}",
    }


def modbus_to_qpiri(c: dict) -> dict:
    """Преобразует данные MODBUS (config) в структуру, максимально похожую на decode_qpiri()."""
    ac_range_name = (
        ACInputVoltageRange.UPS.name
        if c["input_voltage_range"] == 1
        else ACInputVoltageRange.Appliance.name
    )

    OUTPUT_PRIORITY = {
        0: OutputSourcePriority.UtilityFirst.name,
        1: OutputSourcePriority.SolarFirst.name,
        2: OutputSourcePriority.SBU.name,
    }
    output_priority = OUTPUT_PRIORITY.get(
        c["output_priority"], OutputSourcePriority.UtilityFirst.name
    )

    CHARGER_PRIORITY = {
        0: ChargerSourcePriority.UtilityFirst.name,
        1: ChargerSourcePriority.SolarFirst.name,
        2: ChargerSourcePriority.SolarAndUtility.name,
        3: ChargerSourcePriority.OnlySolar.name,
    }
    charger_priority = CHARGER_PRIORITY.get(
        c["battery_charging_priority"], ChargerSourcePriority.UtilityFirst.name
    )

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
# ГЛАВНАЯ ФУНКЦИЯ ЧТЕНИЯ
# ==========================


async def get_direct_data(device: str, command_str: str, timeout: float = 30.0) -> dict:
    """
    Универсальный доступ к инвертору.

    • device = "modbus://host:port"  → SMG-II по Modbus RTU-over-TCP, данные мимикрируют под QPIGS/QPIRI/QMOD.
    • device = "tcp://host:port"    → классический Voltronic через Elfin (ASCII + CRC).
    • device = "/dev/ttyUSB0"       → классический Voltronic по UART (ASCII + CRC).

    Возвращает dict, совместимый с decode_qpigs / decode_qpiri / decode_qmod.
    """

    command = command_str.upper()

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

        if command == "QPIGS":
            return modbus_to_qpigs(sensors)
        if command == "QPIRI":
            return modbus_to_qpiri(config)
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

        # на всякий случай — сырые структуры
        return {"sensors": sensors, "config": config}

    # ---- Старый путь: Elfin TCP / Serial, всё как было ----
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    transport: asyncio.Transport | None = None

    def on_response(data, err):
        if not fut.done():
            if err:
                fut.set_result(None)
            else:
                fut.set_result(data)

    try:
        if device.startswith("tcp://"):
            _, addr = device.split("tcp://", 1)
            host, port_str = addr.split(":")
            port = int(port_str)
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
        else:
            return {}
    finally:
        if transport:
            transport.close()


# ==========================
# УПРАВЛЯЮЩИЕ КОМАНДЫ Voltronic / Modbus
# ==========================


async def set_direct_data(device: str, command_str: str, timeout: float = 30.0) -> dict:
    """
    Отправляет управляющую команду (PBATC, POP, PCP, ...) на классический Voltronic через TCP.
    Для Modbus/SMG-II управление делается через регистры в set_* методах.
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
            data = await asyncio.wait_for(reader.read(128), timeout=timeout)
        except asyncio.TimeoutError:
            return {"error": "timeout waiting for ACK/NAK"}

        writer.close()
        await writer.wait_closed()

        resp = data.decode(errors="ignore").strip()

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


# === Enums для текстовых/выборных настроек (классический Voltronic) ===


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


# ==== ОБНОВЛЁННЫЕ set_* С ПОДДЕРЖКОЙ modbus:// ====


async def set_battery_type(device: str, battery_type: BatteryTypeSetting) -> dict:
    """
    У Voltronic — PB Txx команда.
    Для SMG-II по Modbus сейчас безопасно вернуть ошибку, т.к. регистр типа батареи не известен.
    """
    if device.startswith("modbus://"):
        return {"error": "set_battery_type is not implemented for modbus devices"}

    # классический Voltronic по TCP
    return await set_direct_data(device, battery_type.value)


async def set_output_source_priority(device: str, mode: OutputSourcePrioritySetting) -> dict:
    """
    Voltronic: POPxx
    SMG-II (modbus://): регистр 301 (output_priority)
        0 = UtilityFirst
        1 = SolarFirst
        2 = SBU
    """
    if device.startswith("modbus://"):
        try:
            _, addr = device.split("modbus://", 1)
            host, port_str = addr.split(":")
            port = int(port_str)
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

        return await _write_modbus_single_register(host, port, 301, value)

    # классический Voltronic
    return await set_direct_data(device, mode.value)


async def set_charge_source_priority(device: str, mode: ChargeSourcePrioritySetting) -> dict:
    """
    Voltronic: PCPxx
    SMG-II (modbus://): регистр 331 (battery_charging_priority)
        0 = UtilityFirst
        1 = SolarFirst
        2 = SolarAndUtility
        (3 = OnlySolar — не покрывается текущим Enum)
    """
    if device.startswith("modbus://"):
        try:
            _, addr = device.split("modbus://", 1)
            host, port_str = addr.split(":")
            port = int(port_str)
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

        return await _write_modbus_single_register(host, port, 331, value)

    # классический Voltronic
    return await set_direct_data(device, mode.value)


async def set_battery_bulk_voltage(device: str, voltage: float) -> dict:
    """
    Voltronic: PBAVxx.xx
    SMG-II (modbus://): регистр 324 (max_charge_voltage), масштаб ×10
    """
    if device.startswith("modbus://"):
        try:
            _, addr = device.split("modbus://", 1)
            host, port_str = addr.split(":")
            port = int(port_str)
        except Exception:
            return {"error": "invalid modbus device string"}

        reg_value = int(round(voltage * 10.0))
        reg_value = max(0, min(0xFFFF, reg_value))
        return await _write_modbus_single_register(host, port, 324, reg_value)

    cmd = f"PBAV{voltage:.2f}"
    return await set_direct_data(device, cmd)


async def set_battery_float_voltage(device: str, voltage: float) -> dict:
    """
    Voltronic: PBFVxx.xx
    SMG-II (modbus://): регистр 325 (float_charge_voltage), масштаб ×10.
    """
    if device.startswith("modbus://"):
        try:
            _, addr = device.split("modbus://", 1)
            host, port_str = addr.split(":")
            port = int(port_str)
        except Exception:
            return {"error": "invalid modbus device string"}

        reg_value = int(round(voltage * 10.0))
        reg_value = max(0, min(0xFFFF, reg_value))
        return await _write_modbus_single_register(host, port, 325, reg_value)

    cmd = f"PBFV{voltage:.2f}"
    return await set_direct_data(device, cmd)


async def set_rated_battery_voltage(device: str, voltage: int) -> dict:
    """
    Voltronic: PBRVxx
    Для SMG-II пока нет явного регистра "rated_battery_voltage" в карте, поэтому не трогаем.
    """
    if device.startswith("modbus://"):
        return {"error": "set_rated_battery_voltage is not implemented for modbus devices"}

    cmd = f"PBRV{voltage}"
    return await set_direct_data(device, cmd)


async def set_max_combined_charge_current(device: str, amps: int) -> dict:
    """
    Voltronic: MCHGCxxx
    SMG-II (modbus://): регистра явного "combined" нет, используем 332 (max_charging_current), ×10.
    """
    if device.startswith("modbus://"):
        try:
            _, addr = device.split("modbus://", 1)
            host, port_str = addr.split(":")
            port = int(port_str)
        except Exception:
            return {"error": "invalid modbus device string"}

        reg_value = int(round(amps * 10.0))
        reg_value = max(0, min(0xFFFF, reg_value))
        return await _write_modbus_single_register(host, port, 332, reg_value)

    cmd = f"MCHGC{amps:03d}"
    return await set_direct_data(device, cmd)


async def set_battery_charge_current(device: str, amps: int) -> dict:
    """
    Voltronic: PBATCxxx
    SMG-II (modbus://): используем тот же 332 (max_charging_current), ×10.
    """
    if device.startswith("modbus://"):
        try:
            _, addr = device.split("modbus://", 1)
            host, port_str = addr.split(":")
            port = int(port_str)
        except Exception:
            return {"error": "invalid modbus device string"}

        reg_value = int(round(amps * 10.0))
        reg_value = max(0, min(0xFFFF, reg_value))
        return await _write_modbus_single_register(host, port, 332, reg_value)

    cmd = f"PBATC{amps:03d}"
    return await set_direct_data(device, cmd)


async def set_max_utility_charge_current(device: str, amps: int) -> dict:
    """
    Установка максимального тока заряда от сети (AC charging current).

    • Для классического Voltronic (tcp:// или /dev/ttyUSB0) — команда MUCHGCxxx.
    • Для SMG-II по Modbus (modbus://host:port) — запись в регистр 333 (в десятых ампера).
    """
    if device.startswith("modbus://"):
        try:
            _, addr = device.split("modbus://", 1)
            host, port_str = addr.split(":")
            port = int(port_str)
        except Exception:
            return {"error": f"invalid modbus device: {device}"}

        # Регистр 333 хранит значение в 0.1 A → умножаем на 10
        reg_value = int(amps * 10)
        return await _write_modbus_single_register(host, port, 333, reg_value)
    else:
        # Старый Voltronic-путь
        cmd = f"MUCHGC{amps:03d}"
        return await set_direct_data(device, cmd)


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
    """Очищает строку, оставляя только 0/1, и возвращает ровно count бит."""
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
