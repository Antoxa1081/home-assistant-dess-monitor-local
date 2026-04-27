"""Voltronic-over-TCP (Elfin gateway) transport.

URI: ``tcp://<host>:<port>``

Sends classic Voltronic ASCII commands (QPIGS, QPIRI, ...) framed with a
CRC-16 and trailing CR. The Elfin gateway is a transparent
TCP↔RS232 bridge — wire format is identical to the serial path.
"""
from __future__ import annotations

import asyncio

from ..crc import crc16_voltronic


class ElfinTCPProtocol(asyncio.Protocol):
    """Single-shot Voltronic ASCII request/response over TCP."""

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
