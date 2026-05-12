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


# ---------------------------------------------------------------------------
# Frame-level validators (response-side)
# ---------------------------------------------------------------------------


def validate_voltronic_response(raw: bytes) -> tuple[bool, bytes]:
    """Validate a Voltronic PI30 response frame's CRC.

    The wire format is ``(<payload><CRC_HI><CRC_LO>\\r``. The CRC scope
    differs between firmware variants found in the wild (Voltronic Axpert,
    EASUN, ANERN, MUST, etc.):

    * canonical:  XMODEM-16 over ``<payload>``
    * with start: XMODEM-16 over ``(<payload>``
    * with CR:    XMODEM-16 over ``<payload>\\r`` (some clones)

    Each firmware may also apply the Voltronic 0x28/0x0D/0x0A → +1
    byte-bump (to keep CRC bytes from colliding with frame delimiters)
    or skip it entirely. All combinations are accepted; the false-positive
    rate stays at CRC-16 strength (≈1 in 10⁴) even with six candidates.

    Args:
        raw: response bytes *without* the trailing ``\\r``. Leading
            non-frame bytes (NULs, whitespace from gateways) are tolerated;
            the frame is located by searching for ``(``.

    Returns:
        ``(ok, payload)`` where ``payload`` is the response body with the
        leading ``(`` and trailing 2-byte CRC stripped. On too-short input
        returns ``(False, raw)``.
    """
    # Locate frame start; tolerate leading junk that some gateways inject
    # (NUL bytes, stray whitespace, leftovers from previous connections).
    idx = raw.find(b"(")
    body = raw[idx + 1:] if idx >= 0 else raw.lstrip(b" \t\r\n\x00")
    if len(body) < 3:
        return False, body

    payload = body[:-2]
    received = bytes(body[-2:])

    # Try every CRC scope variant. If any matches under either bumped or
    # raw form, the frame is accepted.
    scopes = (
        payload,
        b"(" + payload,
        payload + b"\r",
    )
    for scope in scopes:
        raw_crc = crc16_xmodem(scope)
        unbumped = bytes([(raw_crc >> 8) & 0xFF, raw_crc & 0xFF])
        bumped = crc16_voltronic(scope)
        if received == unbumped or received == bumped:
            return True, payload

    return False, payload


def validate_pi18_response(raw: bytes) -> tuple[bool, bytes]:
    """Validate a PI18 ``^Dnnn<body><CRC_HI><CRC_LO>\\r`` response frame.

    CRC is XMODEM-16 over ``^Dnnn<body>`` (everything except the 2 CRC
    bytes). PI18 firmware does *not* apply the Voltronic byte-bump, so
    only the raw form is accepted.

    Args:
        raw: response bytes *without* the trailing ``\\r``.

    Returns:
        ``(ok, payload)`` where ``payload`` is the frame minus the 2 CRC
        bytes (the ``^Dnnn`` header is left attached for the decoder). On
        too-short input returns ``(False, raw)``.
    """
    if len(raw) < 3:
        return False, raw
    payload = raw[:-2]
    received = bytes(raw[-2:])
    expected = crc16_xmodem_bytes(payload)
    return received == expected, payload
