"""Tests for the EyBond Wi-Fi dongle transport's pure helpers
(api/protocols/eybond_dongle.py): binary header framing, frame builders,
URI parsing and broadcast resolution. This is the newest transport, so
the framing correctness matters most."""
import struct

from custom_components.dess_monitor_local.api.protocols import eybond_dongle as ey


class TestHeaderRoundTrip:
    def test_encode_decode_identity(self):
        raw = ey._encode_header(
            tid=0x1234, devcode=0x0994, total_len=20, devaddr=1, fcode=4
        )
        h = ey._decode_header(raw)
        assert h.tid == 0x1234
        assert h.devcode == 0x0994
        assert h.devaddr == 1
        assert h.fcode == 4
        # wire_len encodes total_len - 6.
        assert h.wire_len == 20 - ey.WIRE_LEN_OFFSET
        assert h.total_len == 20

    def test_header_is_8_bytes(self):
        raw = ey._encode_header(1, 0, ey.HEADER_SIZE, 1, 1)
        assert len(raw) == ey.HEADER_SIZE

    def test_wire_len_field_big_endian(self):
        raw = ey._encode_header(0, 0, 6 + 0x0102, 0, 0)
        # Bytes 4-5 carry wire_len = total_len-6 = 0x0102, big-endian.
        assert raw[4:6] == bytes([0x01, 0x02])

    def test_payload_len_property(self):
        h = ey._decode_header(ey._encode_header(1, 0, ey.HEADER_SIZE + 12, 1, 4))
        assert h.payload_len == 12


class TestBuildHeartbeat:
    def test_structure(self):
        frame = ey._build_heartbeat(tid=7, interval=60)
        h = ey._decode_header(frame)
        assert h.fcode == ey.FC_HEARTBEAT
        assert h.tid == 7
        assert h.devaddr == 1
        # 8-byte header + 6 date bytes + 2 interval bytes.
        assert len(frame) == ey.HEADER_SIZE + 8
        assert h.total_len == len(frame)

    def test_interval_trailing_big_endian(self):
        frame = ey._build_heartbeat(tid=0, interval=300)
        assert frame[-2:] == struct.pack(">H", 300)


class TestBuildForward2Device:
    def test_wraps_payload(self):
        payload = b"QPIGS\xb7\xa9\r"
        frame = ey._build_forward2device(tid=42, payload=payload, devaddr=3)
        h = ey._decode_header(frame)
        assert h.fcode == ey.FC_FORWARD2DEVICE
        assert h.tid == 42
        assert h.devaddr == 3
        assert h.devcode == ey.DEFAULT_DEVCODE
        # Payload is appended verbatim after the header.
        assert frame[ey.HEADER_SIZE:] == payload
        assert h.payload_len == len(payload)

    def test_custom_devcode(self):
        frame = ey._build_forward2device(
            tid=1, payload=b"x", devaddr=1, devcode=0x1234
        )
        assert ey._decode_header(frame).devcode == 0x1234


class TestParseEybondUri:
    def test_full(self):
        host, port, devaddr, broadcast, announce = ey.parse_eybond_uri(
            "eybond://0.0.0.0:8899/2?broadcast=192.168.1.255&announce=192.168.1.10"
        )
        assert host == "0.0.0.0"
        assert port == 8899
        assert devaddr == 2
        assert broadcast == "192.168.1.255"
        assert announce == "192.168.1.10"

    def test_defaults(self):
        host, port, devaddr, broadcast, announce = ey.parse_eybond_uri(
            "eybond://0.0.0.0"
        )
        assert port == ey.DEFAULT_BIND_PORT
        assert devaddr == 1
        assert broadcast == ey.DEFAULT_BROADCAST
        assert announce is None

    def test_bad_devaddr_falls_back_to_1(self):
        _, _, devaddr, _, _ = ey.parse_eybond_uri("eybond://0.0.0.0:8899/abc")
        assert devaddr == 1

    def test_blank_announce_is_none(self):
        *_, announce = ey.parse_eybond_uri("eybond://0.0.0.0:8899/1?announce=")
        assert announce is None


class TestResolveBroadcast:
    def test_invalid_ip_returns_default(self):
        assert ey._resolve_broadcast_for_announce_ip("not-an-ip") == ey.DEFAULT_BROADCAST

    def test_loopback_returns_default(self):
        assert ey._resolve_broadcast_for_announce_ip("127.0.0.1") == ey.DEFAULT_BROADCAST
