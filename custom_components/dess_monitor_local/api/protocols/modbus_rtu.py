"""Modbus RTU-over-TCP transport (SMG-II inverter controller).

URI: ``modbus://<host>:<port>``

Implements raw Modbus RTU framing on top of an asyncio TCP socket — no
``time.sleep`` and no ``pyserial``. Two helpers translate the SMG-II
register block into the QPIGS / QPIRI shapes the rest of the
integration consumes.
"""
from __future__ import annotations

import asyncio

from ..crc import crc16_modbus
from ..decoders.enums import (
    ACInputVoltageRange,
    ChargerSourcePriority,
    OutputSourcePriority,
)


UNIT_ID = 1


def parse_modbus_uri(device: str) -> tuple[str, int]:
    _, addr = device.split("modbus://", 1)
    host, port_str = addr.split(":")
    return host, int(port_str)


def _i16(v: int) -> int:
    """Convert an unsigned 16-bit register to signed."""
    return v - 65536 if v >= 32768 else v


async def read_modbus_block(
    host: str,
    port: int,
    start: int,
    count: int,
    unit_id: int = UNIT_ID,
    timeout: float = 30.0,
) -> list[int]:
    """Read a contiguous block of Modbus holding registers (func 0x03)."""
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
        crc = crc16_modbus(bytes(req))
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
            raise Exception(f"Modbus exception {exc_and_crc[0]}")

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
        calc_crc = crc16_modbus(header2 + bc_bytes + data)
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


async def write_modbus_single_register(
    host: str,
    port: int,
    address: int,
    value: int,
    unit_id: int = UNIT_ID,
    timeout: float = 30.0,
) -> dict:
    """Write a single holding register; falls back to multi-write if the
    inverter rejects the single-write opcode (some Elfin/SMG combos do).
    Returns ``{"status": "OK", ...}`` or ``{"error": "..."}``.
    """

    async def _send(func_code: int) -> dict:
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

            crc = crc16_modbus(bytes(req))
            req.append(crc & 0xFF)
            req.append((crc >> 8) & 0xFF)

            writer.write(req)
            await writer.drain()

            resp = await asyncio.wait_for(reader.readexactly(8), timeout=timeout)
            if len(resp) != 8:
                raise Exception(f"Short Modbus write response: {len(resp)} bytes")

            body = resp[:-2]
            crc_lo, crc_hi = resp[-2], resp[-1]
            recv_crc = crc_lo | (crc_hi << 8)
            calc_crc = crc16_modbus(body)
            if recv_crc != calc_crc:
                raise Exception("Modbus write CRC mismatch")

            uid, fback = body[0], body[1]
            if uid != unit_id:
                raise Exception(f"Unexpected unit id in write response: {uid}")
            if fback & 0x80:
                raise Exception(f"Modbus exception in write: {body[2]}")

            return {"status": "OK", "func": func_code}
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    try:
        return await _send(0x06)
    except Exception as e1:
        try:
            return await _send(0x10)
        except Exception as e2:
            return {
                "error": f"modbus write failed (0x06: {e1}, 0x10: {e2})"
            }


_OPERATION_MODES = {
    0: "Power On",
    1: "Standby",
    2: "Mains",
    3: "Off-Grid",
    4: "Bypass",
    5: "Charging",
    6: "Fault",
}


async def read_smg2_snapshot(host: str, port: int) -> tuple[dict, dict]:
    """Read SMG-II's two register blocks: 201–231 (sensors) and 300–337 (config)."""
    block_200 = await read_modbus_block(host, port, 201, 31)

    def R200(addr: int) -> int:
        return block_200[addr - 201]

    sensors = {
        "operation_mode": _OPERATION_MODES.get(R200(201)),
        "mains_voltage": R200(202) / 10.0,
        "mains_frequency": R200(203) / 100.0,
        "mains_power": _i16(R200(204)),
        "inverter_voltage": R200(205) / 10.0,
        "inverter_current": _i16(R200(206)) / 10.0,
        "inverter_frequency": R200(207) / 100.0,
        "inverter_power": _i16(R200(208)),
        "inverter_charge_power": _i16(R200(209)),
        "output_voltage": R200(210) / 10.0,
        "output_current": _i16(R200(211)) / 10.0,
        "output_frequency": R200(212) / 100.0,
        "output_active_power": _i16(R200(213)),
        "battery_voltage": R200(215) / 10.0,
        "battery_current": _i16(R200(216)) / 10.0,
        "battery_power": _i16(R200(217)),
        "pv_voltage": R200(219) / 10.0,
        "pv_current": R200(220) / 10.0,
        "pv_power": _i16(R200(223)),
        "pv_charge_power": _i16(R200(224)),
        "load_percent": R200(225),
        "temp_dcdc": R200(226),
        "temp_inverter": R200(227),
    }

    block_300 = await read_modbus_block(host, port, 300, 38, UNIT_ID, 30)

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


def smg2_to_qpigs(s: dict) -> dict:
    """Project SMG-II sensor block onto the QPIGS-shaped dict."""
    bus_voltage = 400  # SMG-II has no dedicated bus voltage register

    battery_charging_current = max(0, int(s["battery_current"]))
    battery_discharge_current = max(0, int(-s["battery_current"]))

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
        "pv_input_current": f"{s['pv_current']:.1f}",
        "pv_input_voltage": f"{s['pv_voltage']:.1f}",
        "scc_battery_voltage": f"{s['battery_voltage']:.2f}",
        "battery_discharge_current": f"{battery_discharge_current:05d}",
        "device_status_bits_b7_b0": "00010000",
        "battery_voltage_offset": "00",
        "eeprom_version": "00",
        "pv_charging_power": f"{int(s['pv_power']):05d}",
        "device_status_bits_b10_b8": "010",
        "grid_ac_in_power": f"{abs(s['mains_power']):05d}",
    }


_OUTPUT_PRIORITY_MAP = {
    0: OutputSourcePriority.UtilityFirst.name,
    1: OutputSourcePriority.SolarFirst.name,
    2: OutputSourcePriority.SBU.name,
}

_CHARGER_PRIORITY_MAP = {
    0: ChargerSourcePriority.UtilityFirst.name,
    1: ChargerSourcePriority.SolarFirst.name,
    2: ChargerSourcePriority.SolarAndUtility.name,
    3: ChargerSourcePriority.OnlySolar.name,
}


def smg2_to_qpiri(c: dict) -> dict:
    """Project SMG-II config block onto the QPIRI-shaped dict."""
    ac_range_name = (
        ACInputVoltageRange.UPS.name
        if c["input_voltage_range"] == 1
        else ACInputVoltageRange.Appliance.name
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
        "output_source_priority": _OUTPUT_PRIORITY_MAP.get(
            c["output_priority"], OutputSourcePriority.UtilityFirst.name
        ),
        "charger_source_priority": _CHARGER_PRIORITY_MAP.get(
            c["battery_charging_priority"], ChargerSourcePriority.UtilityFirst.name
        ),
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
