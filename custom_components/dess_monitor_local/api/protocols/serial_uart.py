"""Voltronic-over-serial (RS232 / USB-UART) transport.

Used when ``device`` is a bare serial path like ``/dev/ttyUSB0`` or
``COM3``. Wire format matches the Elfin TCP path.
"""
from __future__ import annotations

import asyncio
import logging

from ..crc import crc16_voltronic, validate_voltronic_response

_LOGGER = logging.getLogger(__name__)


SERIAL_BAUDRATE = 2400


class SerialCommandProtocol(asyncio.Protocol):
    """Single-shot Voltronic ASCII request/response over RS232/USB."""

    def __init__(self, command: str, on_response, strict_crc: bool = False):
        self.transport: asyncio.Transport | None = None
        self.command = command.upper()
        self.command_bytes = command.encode("ascii")
        self.on_response = on_response
        self.strict_crc = strict_crc
        self.buffer = bytearray()

    def connection_made(self, transport):
        self.transport = transport
        packet = self.command_bytes + crc16_voltronic(self.command_bytes) + b"\r"
        self.transport.write(packet)

    def data_received(self, data: bytes):
        self.buffer.extend(data)
        if b"\r" in self.buffer:
            raw_bytes = bytes(self.buffer.split(b"\r", 1)[0])
            ok, _ = validate_voltronic_response(raw_bytes)
            if not ok:
                # See elfin_tcp.py: single CRC mismatches are absorbed by the
                # coordinator's retry/freeze; only the consecutive-failure
                # warning at the coordinator level is escalated.
                _LOGGER.debug(
                    "CRC mismatch for %s response (%d bytes): %r",
                    self.command,
                    len(raw_bytes),
                    raw_bytes[:120],
                )
                if self.strict_crc:
                    self.on_response(None, ValueError("CRC mismatch"))
                    if self.transport:
                        self.transport.close()
                    return
            try:
                response = raw_bytes.strip().decode(errors="ignore")
                self.on_response(response, None)
            except Exception as e:
                self.on_response(None, e)
            if self.transport:
                self.transport.close()

    def connection_lost(self, exc):
        if exc:
            self.on_response(None, exc)
