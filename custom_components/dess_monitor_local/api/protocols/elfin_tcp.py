"""Voltronic-over-TCP (Elfin gateway) transport.

URI: ``tcp://<host>:<port>``

Sends classic Voltronic ASCII commands (QPIGS, QPIRI, ...) framed with a
CRC-16 and trailing CR. The Elfin gateway is a transparent
TCP↔RS232 bridge — wire format is identical to the serial path.
"""
from __future__ import annotations

import asyncio
import logging

from ..crc import crc16_voltronic, validate_voltronic_response

_LOGGER = logging.getLogger(__name__)


class ElfinTCPProtocol(asyncio.Protocol):
    """Single-shot Voltronic ASCII request/response over TCP."""

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
        if b"\r" in self.buffer or b"\n" in self.buffer:
            raw_bytes = bytes(self.buffer.split(b"\r", 1)[0])
            ok, _ = validate_voltronic_response(raw_bytes)
            if not ok:
                # Single-frame CRC mismatches are routine on noisy RS232 lines;
                # the coordinator's retry + freeze logic absorbs them. Only the
                # "3 failures in a row" signal (logged from the coordinator)
                # warrants WARNING-level attention.
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


def parse_tcp_uri(device: str) -> tuple[str, int]:
    _, addr = device.split("tcp://", 1)
    host, port_str = addr.split(":")
    return host, int(port_str)


async def send_voltronic_set_command(
    host: str, port: int, command: str, timeout: float = 30.0
) -> dict:
    """Send a Voltronic *set* command (PBATC, POP, PCP, ...) over TCP and
    parse the ACK/NAK/raw response."""
    try:
        reader, writer = await asyncio.open_connection(host, port)
        cmd = command.strip().encode("ascii")
        packet = cmd + crc16_voltronic(cmd) + b"\r"

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
        if "NAK" in resp:
            return {"status": "NAK"}
        if not resp:
            return {"error": "empty response"}
        return {"raw": resp}
    except Exception as e:
        return {"error": str(e)}
