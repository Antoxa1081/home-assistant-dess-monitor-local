"""CRC routines used by the inverter protocols.

Voltronic Axpert (PI30) and InfiniSolar PI18 frames use CRC-16/XMODEM
(poly 0x1021, init 0x0000). SMG-II Modbus RTU uses the standard
Modbus CRC-16 (poly 0xA001, init 0xFFFF, reflected).
"""
from __future__ import annotations


def crc16_xmodem(data: bytes) -> int:
    """XMODEM CRC-16 (poly 0x1021, init 0x0000)."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc


def crc16_xmodem_bytes(data: bytes) -> bytes:
    crc = crc16_xmodem(data)
    return bytes([(crc >> 8) & 0xFF, crc & 0xFF])


def crc16_voltronic(data: bytes) -> bytes:
    """Voltronic ASCII-frame CRC (QPIGS/QPIRI/QMOD).

    Returned in big-endian wire order. Voltronic firmware reserves
    0x28 ('('), 0x0D ('\\r') and 0x0A ('\\n') as frame control bytes —
    if either CRC byte falls on one of these, it is incremented by 1
    so the gateway/UART parser doesn't truncate the packet. Without
    this adjustment, commands like POP02 (raw CRC 0xE20A) are silently
    dropped by Elfin gateways.
    """
    crc = 0
    for b in data:
        x = (crc >> 8) ^ b
        x ^= x >> 4
        crc = ((crc << 8) ^ (x << 12) ^ (x << 5) ^ x) & 0xFFFF
    high = (crc >> 8) & 0xFF
    low = crc & 0xFF
    if high in (0x28, 0x0D, 0x0A):
        high += 1
    if low in (0x28, 0x0D, 0x0A):
        low += 1
    return bytes([high, low])


def crc16_modbus(data: bytes) -> int:
    """Standard Modbus CRC-16 (poly 0xA001, init 0xFFFF, reflected)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF
