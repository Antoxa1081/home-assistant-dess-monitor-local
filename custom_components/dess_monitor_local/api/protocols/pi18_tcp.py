"""PI18 / InfiniSolar-V transport over TCP (and serial).

URI:
    ``pi18://<host>:<port>``         — TCP (Elfin gateway)
    ``pi18-serial:///dev/ttyUSB0``   — direct serial (rare, bench setups)

Both variants speak PI18's ``^P<nnn>...<CRC><CR>`` framing. The codec
that builds requests and parses responses lives in
:mod:`..decoders.pi18`; this module only handles I/O.
"""
from __future__ import annotations

import asyncio
import logging

import serial_asyncio_fast as serial_asyncio

from ..crc import validate_pi18_response
from ..decoders.pi18 import build_request_frame, decode_pi18_response

_LOGGER = logging.getLogger(__name__)


PI18_SERIAL_BAUDRATE = 2400


def parse_pi18_tcp_uri(device: str) -> tuple[str, int]:
    _, addr = device.split("pi18://", 1)
    host, port_str = addr.split(":")
    return host, int(port_str)


def parse_pi18_serial_uri(device: str) -> str:
    _, path = device.split("pi18-serial://", 1)
    return path


class _Pi18FrameCollector(asyncio.Protocol):
    """Reads bytes until the first ``\\r``, then surrenders."""

    def __init__(self, frame: bytes, on_response, strict_crc: bool = False, command: str = ""):
        self.transport: asyncio.Transport | None = None
        self.frame = frame
        self.on_response = on_response
        self.strict_crc = strict_crc
        self.command = command
        self.buffer = bytearray()

    def connection_made(self, transport):
        self.transport = transport
        self.transport.write(self.frame)

    def data_received(self, data: bytes):
        self.buffer.extend(data)
        if b"\r" in self.buffer:
            body = bytes(self.buffer.split(b"\r", 1)[0])
            ok, _ = validate_pi18_response(body)
            if not ok:
                _LOGGER.warning(
                    "CRC mismatch for PI18 %s response (%d bytes): %r",
                    self.command or "?",
                    len(body),
                    body[:120],
                )
                if self.strict_crc:
                    self.on_response(None, ValueError("CRC mismatch"))
                    if self.transport:
                        self.transport.close()
                    return
            self.on_response(body + b"\r", None)
            if self.transport:
                self.transport.close()

    def connection_lost(self, exc):
        if exc:
            self.on_response(None, exc)


async def query_pi18(
    device: str, command: str, timeout: float = 30.0, strict_crc: bool = False
) -> dict:
    """Issue ``command`` to a PI18 device and return a decoded dict.

    Selects TCP vs serial transport based on the URI scheme. Returns an
    empty dict on transport failure — same convention as the Voltronic
    path so the coordinator handles it uniformly.
    """
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    transport: asyncio.Transport | None = None

    def on_response(data, err):
        if not fut.done():
            fut.set_result(None if err else data)

    frame = build_request_frame(command)

    try:
        if device.startswith("pi18://"):
            host, port = parse_pi18_tcp_uri(device)
            transport, _ = await loop.create_connection(
                lambda: _Pi18FrameCollector(frame, on_response, strict_crc, command),
                host,
                port,
            )
        elif device.startswith("pi18-serial://"):
            path = parse_pi18_serial_uri(device)
            transport, _ = await serial_asyncio.create_serial_connection(
                loop,
                lambda: _Pi18FrameCollector(frame, on_response, strict_crc, command),
                path,
                baudrate=PI18_SERIAL_BAUDRATE,
                bytesize=8,
                parity="N",
                stopbits=1,
            )
        else:
            return {}
    except Exception:
        return {}

    try:
        try:
            raw = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raw = None
        if not raw:
            return {}
        try:
            return decode_pi18_response(command, raw) or {}
        except Exception:
            return {}
    finally:
        if transport:
            transport.close()
