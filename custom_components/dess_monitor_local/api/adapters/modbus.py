from __future__ import annotations

import logging

from ..decoders.enums import (
    ChargeSourcePrioritySetting,
    OutputSourcePrioritySetting,
)
from ..protocols.eybond_dongle import parse_eybond_uri, send_eybond_bytes
from ..protocols.modbus_rtu import (
    UNIT_ID,
    build_read_holding_frame,
    build_write_single_frame,
    parse_modbus_uri,
    parse_read_holding_response,
    parse_write_response,
    read_modbus_block,
    read_smg2_snapshot_via,
    smg2_to_qpigs,
    smg2_to_qpiri,
    write_modbus_single_register,
)
from .base import BaseAdapter

_LOGGER = logging.getLogger(__name__)

_EYBOND_MODBUS_SCHEME = "eybond-modbus://"


class _TcpModbusTransport:
    """Modbus RTU over a direct TCP socket (``modbus://host:port``)."""

    def __init__(self, uri: str) -> None:
        self.host, self.port = parse_modbus_uri(uri)
        self.unit_id = UNIT_ID

    async def read_block(self, start: int, count: int) -> list[int]:
        return await read_modbus_block(self.host, self.port, start, count, self.unit_id)

    async def write_register(self, address: int, value: int) -> dict:
        return await write_modbus_single_register(
            self.host, self.port, address, value, self.unit_id
        )


class _EybondModbusTransport:
    """Modbus RTU forwarded through an EyBond dongle's FC=4 channel.

    URI: ``eybond-modbus://<bind_host>:<bind_port>/<devaddr>?pn=<PN>``. The
    RS485 ``devaddr`` doubles as the Modbus unit id; the PN routes to the
    right dongle on a shared listener.
    """

    def __init__(self, uri: str, timeout: float = 30.0) -> None:
        self.uri = uri
        self.timeout = timeout
        _, _, devaddr, _, _ = parse_eybond_uri(uri)
        self.unit_id = devaddr

    async def read_block(self, start: int, count: int) -> list[int]:
        frame = build_read_holding_frame(start, count, self.unit_id)
        resp = await send_eybond_bytes(
            self.uri, frame, self.timeout, context=f"modbus rd {start}+{count}"
        )
        if not resp:
            raise ConnectionError("no eybond-modbus response")
        return parse_read_holding_response(resp, count, self.unit_id)

    async def write_register(self, address: int, value: int) -> dict:
        # Try single-write (0x06), then multi-write (0x10) like the TCP path.
        last = {"error": "eybond-modbus write failed"}
        for func_code in (0x06, 0x10):
            frame = build_write_single_frame(address, value, self.unit_id, func_code)
            resp = await send_eybond_bytes(
                self.uri, frame, self.timeout, context=f"modbus wr {address}"
            )
            if not resp:
                last = {"error": "no eybond-modbus write response"}
                continue
            result = parse_write_response(resp, self.unit_id)
            if "error" not in result:
                return result
            last = result
        return last


class ModbusAdapter(BaseAdapter):
    """SMG-II via Modbus RTU — over TCP (``modbus://``) or an EyBond dongle
    (``eybond-modbus://``). The register map and projections are identical;
    only the transport differs."""

    def _transport(self):
        if self.uri.startswith(_EYBOND_MODBUS_SCHEME):
            return _EybondModbusTransport(self.uri, self.timeout)
        return _TcpModbusTransport(self.uri)

    async def get_data(self, command: str) -> dict:
        try:
            transport = self._transport()
            sensors, config, faults = await read_smg2_snapshot_via(
                transport.read_block
            )
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
        mapping = {
            OutputSourcePrioritySetting.UTILITY_FIRST: 0,
            OutputSourcePrioritySetting.SOLAR_FIRST: 1,
            OutputSourcePrioritySetting.SBU_PRIORITY: 2,
        }
        value = mapping.get(mode)
        if value is None:
            return {"error": f"mode {mode} is not mappable to SMG output_priority"}
        return await self._transport().write_register(301, value)

    async def set_charge_source_priority(self, mode: ChargeSourcePrioritySetting) -> dict:
        mapping = {
            ChargeSourcePrioritySetting.UTILITY_FIRST: 0,
            ChargeSourcePrioritySetting.SOLAR_FIRST: 1,
            ChargeSourcePrioritySetting.SOLAR_AND_UTILITY: 2,
        }
        value = mapping.get(mode)
        if value is None:
            return {"error": f"mode {mode} is not mappable to SMG battery_charging_priority"}
        return await self._transport().write_register(331, value)

    async def set_battery_bulk_voltage(self, voltage: float) -> dict:
        reg_value = max(0, min(0xFFFF, int(round(voltage * 10.0))))
        return await self._transport().write_register(324, reg_value)

    async def set_battery_float_voltage(self, voltage: float) -> dict:
        reg_value = max(0, min(0xFFFF, int(round(voltage * 10.0))))
        return await self._transport().write_register(325, reg_value)

    async def set_max_combined_charge_current(self, amps: int) -> dict:
        reg_value = max(0, min(0xFFFF, int(round(amps * 10.0))))
        return await self._transport().write_register(332, reg_value)

    async def set_battery_charge_current(self, amps: int) -> dict:
        return await self.set_max_combined_charge_current(amps)

    async def set_max_utility_charge_current(self, amps: int, float_format: bool = False) -> dict:
        return await self._transport().write_register(333, int(amps * 10))
