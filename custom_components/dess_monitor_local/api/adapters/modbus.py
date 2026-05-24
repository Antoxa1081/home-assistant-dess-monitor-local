from __future__ import annotations
import logging
from .base import BaseAdapter
from ..decoders.enums import (
    ChargeSourcePrioritySetting,
    OutputSourcePrioritySetting,
)
from ..protocols.modbus_rtu import (
    parse_modbus_uri,
    read_smg2_snapshot,
    smg2_to_qpigs,
    smg2_to_qpiri,
    write_modbus_single_register,
)

_LOGGER = logging.getLogger(__name__)

class ModbusAdapter(BaseAdapter):
    """Adapter for SMG-II via Modbus RTU-over-TCP."""

    async def get_data(self, command: str) -> dict:
        try:
            host, port = parse_modbus_uri(self.uri)
            sensors, config, faults = await read_smg2_snapshot(host, port)
        except Exception as err:
            _LOGGER.debug("ModbusAdapter read failed: %s", err)
            return {}

        if command == "QPIGS":
            return smg2_to_qpigs(sensors)
        if command == "QPIRI":
            return smg2_to_qpiri(config)
        # ... other command emulations from dispatcher.py ...
        return {"sensors": sensors, "config": config, "faults": faults}

    async def set_data(self, command: str) -> dict:
        return {"error": "raw set_data is not supported for Modbus; use semantic setters"}

    async def set_output_source_priority(self, mode: OutputSourcePrioritySetting) -> dict:
        try:
            host, port = parse_modbus_uri(self.uri)
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

    async def set_charge_source_priority(self, mode: ChargeSourcePrioritySetting) -> dict:
        try:
            host, port = parse_modbus_uri(self.uri)
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

    async def set_battery_bulk_voltage(self, voltage: float) -> dict:
        host, port = parse_modbus_uri(self.uri)
        reg_value = max(0, min(0xFFFF, int(round(voltage * 10.0))))
        return await write_modbus_single_register(host, port, 324, reg_value)

    async def set_battery_float_voltage(self, voltage: float) -> dict:
        host, port = parse_modbus_uri(self.uri)
        reg_value = max(0, min(0xFFFF, int(round(voltage * 10.0))))
        return await write_modbus_single_register(host, port, 325, reg_value)

    async def set_max_combined_charge_current(self, amps: int) -> dict:
        host, port = parse_modbus_uri(self.uri)
        reg_value = max(0, min(0xFFFF, int(round(amps * 10.0))))
        return await write_modbus_single_register(host, port, 332, reg_value)

    async def set_battery_charge_current(self, amps: int) -> dict:
        return await self.set_max_combined_charge_current(amps)

    async def set_max_utility_charge_current(self, amps: int, float_format: bool = False) -> dict:
        host, port = parse_modbus_uri(self.uri)
        return await write_modbus_single_register(host, port, 333, int(amps * 10))
