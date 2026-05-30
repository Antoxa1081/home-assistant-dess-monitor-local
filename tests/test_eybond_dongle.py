"""Tests for the EyBond Wi-Fi dongle transport's pure helpers
(api/protocols/eybond_dongle.py): binary header framing, frame builders,
URI parsing and broadcast resolution. This is the newest transport, so
the framing correctness matters most.

Also exercises the multi-session EybondManager (one listener, many dongles
routed by PN) using in-memory fake StreamReader/StreamWriter so no real
socket is bound."""
import asyncio
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


# ---------------------------------------------------------------------------
# Multi-session manager — in-memory fakes (no real socket)
# ---------------------------------------------------------------------------
class _FakeWriter:
    """Captures each ``write()`` as a discrete frame (EyBond writes whole
    frames in one call) and records close()."""

    def __init__(self, peer=("10.0.0.9", 9999)):
        self.frames: list[bytes] = []
        self.closed = False
        self._peer = peer

    def get_extra_info(self, key):
        return self._peer if key == "peername" else None

    def write(self, data: bytes) -> None:
        self.frames.append(bytes(data))

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _FakeReader:
    """Feedable stream reader. ``feed()`` pushes bytes; ``feed_eof()`` makes
    pending/future ``readexactly`` raise IncompleteReadError (clean close)."""

    def __init__(self):
        self._buf = bytearray()
        self._eof = False
        self._evt = asyncio.Event()

    def feed(self, data: bytes) -> None:
        self._buf.extend(data)
        self._evt.set()

    def feed_eof(self) -> None:
        self._eof = True
        self._evt.set()

    async def readexactly(self, n: int) -> bytes:
        while len(self._buf) < n:
            if self._eof:
                partial = bytes(self._buf)
                self._buf.clear()
                raise asyncio.IncompleteReadError(partial, n)
            self._evt.clear()
            await self._evt.wait()
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


def _dongle_heartbeat(pn: str) -> bytes:
    """An FC=1 heartbeat as a dongle would send it: PN in the payload."""
    payload = pn.encode("ascii")
    total = ey.HEADER_SIZE + len(payload)
    return ey._encode_header(
        1, ey.DEFAULT_DEVCODE, total, 1, ey.FC_HEARTBEAT
    ) + payload


def _find_fc4(frames: list[bytes]):
    """Return (header, frame) of the first FC=4 frame in the list."""
    for f in frames:
        if len(f) >= ey.HEADER_SIZE:
            h = ey._decode_header(f)
            if h.fcode == ey.FC_FORWARD2DEVICE:
                return h, f
    return None, None


async def _until(pred, tries: int = 400) -> None:
    for _ in range(tries):
        if pred():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met within timeout")


def _new_manager() -> ey.EybondManager:
    """Manager wired to skip the real TCP server + UDP announcer."""
    mgr = ey.EybondManager("0.0.0.0", 18899, ey.DEFAULT_BROADCAST)
    mgr._server = object()  # sentinel: ensure_started() skips bind
    mgr._announce_task = asyncio.create_task(asyncio.sleep(3600))
    return mgr


async def _drain(mgr: ey.EybondManager, *tasks) -> None:
    """Cancel outstanding tasks so asyncio.run() exits cleanly."""
    if mgr._announce_task and not mgr._announce_task.done():
        mgr._announce_task.cancel()
    for t in tasks:
        if t and not t.done():
            t.cancel()
    for t in (mgr._announce_task, *tasks):
        if t:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


class TestMultiSession:
    def test_pn_learned_from_heartbeat(self):
        async def scenario():
            mgr = _new_manager()
            r, w = _FakeReader(), _FakeWriter(("10.0.0.1", 1111))
            task = asyncio.create_task(mgr._handle_session(r, w))
            r.feed(_dongle_heartbeat("PN0000000001"))
            await _until(lambda: mgr.identified_pns == ["PN0000000001"])
            assert mgr.connected
            assert len(mgr._sessions) == 1
            assert mgr._sessions_by_pn["PN0000000001"].peer == "10.0.0.1:1111"
            # Clean disconnect removes the session and clears readiness.
            r.feed_eof()
            await _until(lambda: not mgr.connected)
            assert mgr.identified_pns == []
            assert not mgr._any_ready.is_set()
            await _drain(mgr, task)

        asyncio.run(scenario())

    def test_two_dongles_coexist_no_eviction(self):
        async def scenario():
            mgr = _new_manager()
            rA, wA = _FakeReader(), _FakeWriter(("10.0.0.1", 1111))
            rB, wB = _FakeReader(), _FakeWriter(("10.0.0.2", 2222))
            tA = asyncio.create_task(mgr._handle_session(rA, wA))
            tB = asyncio.create_task(mgr._handle_session(rB, wB))
            rA.feed(_dongle_heartbeat("PN_AAAAAAA01"))
            rB.feed(_dongle_heartbeat("PN_BBBBBBB02"))
            await _until(
                lambda: mgr.identified_pns == ["PN_AAAAAAA01", "PN_BBBBBBB02"]
            )
            # Both alive; the second connection did NOT evict the first.
            assert len(mgr._sessions) == 2
            assert not wA.closed and not wB.closed
            await _drain(mgr, tA, tB)

        asyncio.run(scenario())

    def test_routing_targets_correct_pn(self):
        async def scenario():
            mgr = _new_manager()
            rA, wA = _FakeReader(), _FakeWriter(("10.0.0.1", 1111))
            rB, wB = _FakeReader(), _FakeWriter(("10.0.0.2", 2222))
            tA = asyncio.create_task(mgr._handle_session(rA, wA))
            tB = asyncio.create_task(mgr._handle_session(rB, wB))
            rA.feed(_dongle_heartbeat("PN_AAAAAAA01"))
            rB.feed(_dongle_heartbeat("PN_BBBBBBB02"))
            await _until(lambda: len(mgr.identified_pns) == 2)

            wA.frames.clear()
            wB.frames.clear()
            send = asyncio.create_task(
                mgr.send_frame(1, b"QPIGS\r", timeout=5.0, pn="PN_BBBBBBB02")
            )
            # The forwarded frame must land on B's writer, not A's.
            await _until(lambda: _find_fc4(wB.frames)[1] is not None)
            assert _find_fc4(wA.frames)[1] is None, "request leaked to wrong dongle"

            h, _ = _find_fc4(wB.frames)
            rB.feed(ey._build_forward2device(h.tid, b"(test-response", devaddr=1))
            result = await send
            assert result == b"(test-response"
            await _drain(mgr, tA, tB, send)

        asyncio.run(scenario())

    def test_disconnect_isolation(self):
        async def scenario():
            mgr = _new_manager()
            rA, wA = _FakeReader(), _FakeWriter(("10.0.0.1", 1111))
            rB, wB = _FakeReader(), _FakeWriter(("10.0.0.2", 2222))
            tA = asyncio.create_task(mgr._handle_session(rA, wA))
            tB = asyncio.create_task(mgr._handle_session(rB, wB))
            rA.feed(_dongle_heartbeat("PN_AAAAAAA01"))
            rB.feed(_dongle_heartbeat("PN_BBBBBBB02"))
            await _until(lambda: len(mgr.identified_pns) == 2)
            # Drop B only; A must survive.
            rB.feed_eof()
            await _until(lambda: mgr.identified_pns == ["PN_AAAAAAA01"])
            assert len(mgr._sessions) == 1
            assert mgr._sessions_by_pn["PN_AAAAAAA01"].peer == "10.0.0.1:1111"
            assert mgr._any_ready.is_set()
            await _drain(mgr, tA, tB)

        asyncio.run(scenario())

    def test_same_pn_reconnect_evicts_stale(self):
        async def scenario():
            mgr = _new_manager()
            r1, w1 = _FakeReader(), _FakeWriter(("10.0.0.1", 1111))
            t1 = asyncio.create_task(mgr._handle_session(r1, w1))
            r1.feed(_dongle_heartbeat("PN_DUP000001"))
            await _until(lambda: mgr.identified_pns == ["PN_DUP000001"])

            # Same PN reconnects on a new socket before the old one EOFs.
            r2, w2 = _FakeReader(), _FakeWriter(("10.0.0.1", 5555))
            t2 = asyncio.create_task(mgr._handle_session(r2, w2))
            r2.feed(_dongle_heartbeat("PN_DUP000001"))
            await _until(
                lambda: mgr._sessions_by_pn.get("PN_DUP000001")
                and mgr._sessions_by_pn["PN_DUP000001"].peer == "10.0.0.1:5555"
            )
            # Stale session was closed and only the new one remains mapped.
            assert w1.closed is True
            assert len(mgr._sessions) == 1
            r1.feed_eof()
            await _drain(mgr, t1, t2)

        asyncio.run(scenario())

    def test_legacy_pn_none_routes_to_single_session(self):
        async def scenario():
            mgr = _new_manager()
            r, w = _FakeReader(), _FakeWriter(("10.0.0.1", 1111))
            task = asyncio.create_task(mgr._handle_session(r, w))
            r.feed(_dongle_heartbeat("PN_LEGACY001"))
            await _until(lambda: mgr.connected)

            w.frames.clear()
            send = asyncio.create_task(
                mgr.send_frame(2, b"QMOD\r", timeout=5.0)  # pn=None (legacy)
            )
            await _until(lambda: _find_fc4(w.frames)[1] is not None)
            h, _ = _find_fc4(w.frames)
            assert h.devaddr == 2
            r.feed(ey._build_forward2device(h.tid, b"(L", devaddr=2))
            assert await send == b"(L"
            await _drain(mgr, task, send)

        asyncio.run(scenario())

    def test_request_times_out_when_no_dongle(self):
        async def scenario():
            mgr = _new_manager()
            # No session connected → wait then drop (timeout clamped small).
            result = await mgr.send_frame(
                1, b"QPIGS\r", timeout=0.05, pn="PN_MISSING01"
            )
            assert result is None
            await _drain(mgr)

        asyncio.run(scenario())


class TestDiscoveryIntegration:
    """The manager feeds its discovery registry from session lifecycle."""

    def test_identify_records_connected(self):
        async def scenario():
            mgr = _new_manager()
            r, w = _FakeReader(), _FakeWriter(("10.0.0.1", 1111))
            task = asyncio.create_task(mgr._handle_session(r, w))
            r.feed(_dongle_heartbeat("PN0000000001"))
            await _until(lambda: mgr.registry.connected_pns() == ["PN0000000001"])
            rec = mgr.registry.get("PN0000000001")
            assert rec.peer == "10.0.0.1:1111"
            assert rec.first_seen and rec.last_seen
            await _drain(mgr, task)

        asyncio.run(scenario())

    def test_disconnect_marks_record_disconnected(self):
        async def scenario():
            mgr = _new_manager()
            r, w = _FakeReader(), _FakeWriter(("10.0.0.1", 1111))
            task = asyncio.create_task(mgr._handle_session(r, w))
            r.feed(_dongle_heartbeat("PN0000000001"))
            await _until(lambda: mgr.registry.connected_pns() == ["PN0000000001"])
            r.feed_eof()
            await _until(lambda: not mgr.connected)
            # Record persists across disconnect, now marked disconnected.
            assert mgr.registry.connected_pns() == []
            assert mgr.registry.get("PN0000000001") is not None
            await _drain(mgr, task)

        asyncio.run(scenario())

    def test_same_pn_reconnect_stays_connected(self):
        async def scenario():
            mgr = _new_manager()
            r1, w1 = _FakeReader(), _FakeWriter(("10.0.0.1", 1111))
            t1 = asyncio.create_task(mgr._handle_session(r1, w1))
            r1.feed(_dongle_heartbeat("PN_DUP000001"))
            await _until(lambda: mgr.registry.connected_pns() == ["PN_DUP000001"])

            r2, w2 = _FakeReader(), _FakeWriter(("10.0.0.1", 5555))
            t2 = asyncio.create_task(mgr._handle_session(r2, w2))
            r2.feed(_dongle_heartbeat("PN_DUP000001"))
            await _until(
                lambda: mgr._sessions_by_pn.get("PN_DUP000001")
                and mgr._sessions_by_pn["PN_DUP000001"].peer == "10.0.0.1:5555"
            )
            # The stale session's teardown must NOT flip the record to
            # disconnected — the PN is still live via the new session.
            r1.feed_eof()
            await _until(lambda: w1.closed)
            await asyncio.sleep(0.05)
            assert mgr.registry.connected_pns() == ["PN_DUP000001"]
            assert mgr.registry.get("PN_DUP000001").peer == "10.0.0.1:5555"
            await _drain(mgr, t1, t2)

        asyncio.run(scenario())
