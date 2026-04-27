"""Voltronic-over-serial (RS232 / USB-UART) transport.

Used when ``device`` is a bare serial path like ``/dev/ttyUSB0`` or
``COM3``. Wire format matches the Elfin TCP path.
"""
from __future__ import annotations

import asyncio

from ..crc import crc16_voltronic


SERIAL_BAUDRATE = 2400


class SerialCommandProtocol(asyncio.Protocol):
    """Single-shot Voltronic ASCII request/response over RS232/USB."""

    def __init__(self, command: str, on_response):
        self.transport: asyncio.Transport | None = None
        self.command = command.upper()
        self.command_bytes = command.encode("ascii")
        self.on_response = on_response
        self.buffer = bytearray()

    def connection_made(self, transport):
        self.transport = transport
        packet = self.command_bytes + crc16_voltronic(self.command_bytes) + b"\r"
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
