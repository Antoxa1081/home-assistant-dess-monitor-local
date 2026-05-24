from __future__ import annotations
import asyncio
import logging
import serial_asyncio_fast as serial_asyncio

from .base import BaseAdapter
from ..decoders.voltronic import decode_direct_response
from ..protocols.elfin_tcp import ElfinTCPProtocol, parse_tcp_uri
from ..protocols.serial_uart import SERIAL_BAUDRATE, SerialCommandProtocol

_LOGGER = logging.getLogger(__name__)

class VoltronicAdapter(BaseAdapter):
    """Adapter for Voltronic PI30 protocol over TCP or Serial."""

    async def get_data(self, command: str) -> dict:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        transport: asyncio.Transport | None = None

        def on_response(data, err):
            if not fut.done():
                fut.set_result(None if err else data)

        try:
            if self.uri.startswith("tcp://"):
                host, port = parse_tcp_uri(self.uri)
                transport, _ = await loop.create_connection(
                    lambda: ElfinTCPProtocol(command, on_response, strict_crc=self.strict_crc),
                    host,
                    port,
                )
            else:
                # Direct serial (e.g. /dev/ttyUSB0)
                transport, _ = await serial_asyncio.create_serial_connection(
                    loop,
                    lambda: SerialCommandProtocol(command, on_response, strict_crc=self.strict_crc),
                    self.uri,
                    baudrate=SERIAL_BAUDRATE,
                    bytesize=8,
                    parity="N",
                    stopbits=1,
                )
        except Exception as err:
            _LOGGER.debug("VoltronicAdapter connection failed: %s", err)
            return {}

        try:
            try:
                result = await asyncio.wait_for(fut, timeout=self.timeout)
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

    async def set_data(self, command: str) -> dict:
        # For Voltronic, set_data is often just get_data and checking for ACK/NAK.
        # But we have send_voltronic_set_command in elfin_tcp.py.
        if self.uri.startswith("tcp://"):
            from ..protocols.elfin_tcp import send_voltronic_set_command
            host, port = parse_tcp_uri(self.uri)
            return await send_voltronic_set_command(host, port, command, self.timeout)
        
        # Fallback to get_data for serial or others if not specialized
        resp = await self.get_data(command)
        # decode_direct_response already handles ACK/NAK for some commands but returns a dict.
        # This part might need refinement to match legacy set_direct_data.
        return resp
