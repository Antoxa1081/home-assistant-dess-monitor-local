"""Tests for the CRC routines and frame validators (api/crc.py).

These cover the heart of the long CRC-mismatch saga: the multi-scope
Voltronic response validator that learned to accept the firmware
variants found in the wild (canonical / leading-"(" / trailing-CR, each
with or without the 0x28/0x0D/0x0A byte-bump).
"""
from custom_components.dess_monitor_local.api.crc import (
    build_pi30_frame,
    crc16_modbus,
    crc16_voltronic,
    crc16_xmodem,
    crc16_xmodem_bytes,
    validate_pi18_response,
    validate_voltronic_response,
)
from custom_components.dess_monitor_local.api.decoders.pi18 import build_request_frame


class TestXmodem:
    def test_empty(self):
        assert crc16_xmodem(b"") == 0

    def test_known_vector(self):
        # "123456789" is the canonical XMODEM check value.
        assert crc16_xmodem(b"123456789") == 0x31C3

    def test_bytes_is_big_endian(self):
        crc = crc16_xmodem(b"QPIGS")
        assert crc16_xmodem_bytes(b"QPIGS") == bytes([(crc >> 8) & 0xFF, crc & 0xFF])

    def test_in_range(self):
        assert 0 <= crc16_xmodem(b"arbitrary payload \x00\xff") <= 0xFFFF


class TestModbus:
    def test_known_vector(self):
        # Classic Modbus RTU example 01 04 02 FF FF: CRC bytes go on the
        # wire low-first as B8 80, i.e. the integer value 0x80B8.
        assert crc16_modbus(bytes([0x01, 0x04, 0x02, 0xFF, 0xFF])) == 0x80B8

    def test_empty_is_init(self):
        assert crc16_modbus(b"") == 0xFFFF


class TestVoltronicCrc:
    def test_byte_bump_avoids_frame_control_chars(self):
        # Whatever the input, neither returned byte may be a reserved
        # frame-control char — that's the whole point of the bump.
        for cmd in (b"QPIGS", b"QPIRI", b"POP02", b"QMOD", b"PCP00"):
            hi, lo = crc16_voltronic(cmd)
            assert hi not in (0x28, 0x0D, 0x0A)
            assert lo not in (0x28, 0x0D, 0x0A)

    def test_returns_two_bytes(self):
        assert len(crc16_voltronic(b"QPIGS")) == 2


class TestBuildPi30Frame:
    def test_structure(self):
        frame = build_pi30_frame("QPIGS")
        assert frame.startswith(b"QPIGS")
        assert frame.endswith(b"\r")
        # body + 2 CRC + CR
        assert len(frame) == len(b"QPIGS") + 2 + 1


def _make_voltronic_frame(payload: bytes, scope: str = "canonical", bump: bool = True) -> bytes:
    """Build a response frame the way a given firmware variant would,
    so we can prove the validator accepts it. Returns bytes WITHOUT the
    trailing CR (matching what the transport hands to the validator)."""
    if scope == "canonical":
        crc_input = payload
    elif scope == "with_paren":
        crc_input = b"(" + payload
    elif scope == "with_cr":
        crc_input = payload + b"\r"
    else:
        raise ValueError(scope)

    if bump:
        crc = crc16_voltronic(crc_input)
    else:
        raw = crc16_xmodem(crc_input)
        crc = bytes([(raw >> 8) & 0xFF, raw & 0xFF])
    return b"(" + payload + crc


class TestValidateVoltronicResponse:
    PAYLOAD = b"239.3 50.0 230.6 49.9 2144 2136 053"

    def test_canonical_bumped(self):
        frame = _make_voltronic_frame(self.PAYLOAD, "canonical", bump=True)
        ok, payload = validate_voltronic_response(frame)
        assert ok is True
        assert payload == self.PAYLOAD

    def test_canonical_unbumped(self):
        frame = _make_voltronic_frame(self.PAYLOAD, "canonical", bump=False)
        ok, payload = validate_voltronic_response(frame)
        assert ok is True

    def test_scope_with_paren(self):
        frame = _make_voltronic_frame(self.PAYLOAD, "with_paren", bump=True)
        ok, _ = validate_voltronic_response(frame)
        assert ok is True

    def test_scope_with_cr(self):
        # The ANERN/clone variant that includes \r in the CRC scope.
        frame = _make_voltronic_frame(self.PAYLOAD, "with_cr", bump=False)
        ok, _ = validate_voltronic_response(frame)
        assert ok is True

    def test_leading_junk_tolerated(self):
        frame = b"\x00\x00 " + _make_voltronic_frame(self.PAYLOAD, "canonical")
        ok, payload = validate_voltronic_response(frame)
        assert ok is True
        assert payload == self.PAYLOAD

    def test_corrupted_crc_rejected(self):
        frame = b"(" + self.PAYLOAD + b"\x12\x34"
        ok, _ = validate_voltronic_response(frame)
        assert ok is False

    def test_corrupted_payload_rejected(self):
        frame = _make_voltronic_frame(self.PAYLOAD, "canonical")
        # Flip a byte in the payload region, keep the original CRC.
        mutated = bytearray(frame)
        mutated[3] ^= 0xFF
        ok, _ = validate_voltronic_response(bytes(mutated))
        assert ok is False

    def test_too_short(self):
        ok, _ = validate_voltronic_response(b"(")
        assert ok is False


class TestValidatePi18Response:
    def _frame(self, body: bytes) -> bytes:
        return body + crc16_xmodem_bytes(body)

    def test_valid(self):
        body = b"^D0252350,500,2300"
        ok, payload = validate_pi18_response(self._frame(body))
        assert ok is True
        assert payload == body

    def test_no_byte_bump_for_pi18(self):
        # PI18 must NOT accept a bumped CRC — only the raw XMODEM bytes.
        body = b"^D025test"
        raw_crc = crc16_xmodem_bytes(body)
        bumped = crc16_voltronic(body)
        if bumped != raw_crc:
            ok, _ = validate_pi18_response(body + bumped)
            assert ok is False

    def test_corrupted_rejected(self):
        ok, _ = validate_pi18_response(b"^D025test\x00\x00")
        assert ok is False

    def test_too_short(self):
        ok, _ = validate_pi18_response(b"x")
        assert ok is False


class TestRoundTrip:
    """Integration: a frame our own builder signs must pass our own
    validator — proves the send-side CRC and the receive-side check agree."""

    def test_pi18_request_validates(self):
        # build_request_frame -> head + XMODEM CRC + CR. Drop the CR
        # (transports hand the validator the frame without it).
        frame = build_request_frame("QPIGS")
        ok, payload = validate_pi18_response(frame[:-1])
        assert ok is True
        assert payload == frame[:-3]  # head, minus the 2 CRC bytes

    def test_voltronic_response_validates(self):
        # Build a response the canonical-bumped firmware way and validate.
        payload = b"239.3 50.0 230.6 00010110"
        frame = b"(" + payload + crc16_voltronic(payload)
        ok, got = validate_voltronic_response(frame)
        assert ok is True
        assert got == payload

    def test_build_pi30_frame_crc_matches_helper(self):
        # build_pi30_frame must embed exactly crc16_voltronic(body).
        frame = build_pi30_frame("POP02")
        body = b"POP02"
        assert frame == body + crc16_voltronic(body) + b"\r"
