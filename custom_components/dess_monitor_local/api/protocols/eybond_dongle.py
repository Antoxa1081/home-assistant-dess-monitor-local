"""EyBond / SmartESS WiFi dongle transport.

URI: ``eybond://<bind_host>:<bind_port>/<rs485_devaddr>[?broadcast=<ip>]``

Unlike the other transports, the dongle initiates the TCP connection to
us. We:

  1. Bind a TCP listener on the configured port (default 8899).
  2. Broadcast ``set>server=<MY_IP>:<port>;`` on UDP/58899 every 5s while
     no dongle is connected — that command tells the dongle to switch
     upstream from the SmartESS cloud to us.
  3. After the dongle's first heartbeat, stop announcing and keep the
     session alive with FC=1 heartbeats.
  4. On each read, wrap the Voltronic ASCII command (``QPIGS\\r``,
     ``QPIRI\\r``, ...) inside an EyBond FC=4 (Forward2Device) frame,
     await the response keyed by TID, unwrap, and return the inner
     Voltronic ASCII payload.

The wire format INSIDE FC=4 is byte-for-byte the same as ``tcp://`` and
serial transports — the dispatcher feeds the unwrapped response straight
into ``decode_direct_response``.

A single TCP listener serves all ``eybond://`` devices in this HA
instance; ``devaddr`` in the URI selects which inverter on the RS485 bus
(1-based). Different bind ports get independent listener instances.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from ..crc import crc16_voltronic

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# EyBond ModBus transport — 8-byte big-endian header + payload.
#   [tid:2][devcode:2][wire_len:2][devaddr:1][fcode:1]
#   wire_len = total_frame_len - 6
# ---------------------------------------------------------------------------
HEADER_SIZE = 8
WIRE_LEN_OFFSET = 6
FC_HEARTBEAT = 1
FC_FORWARD2DEVICE = 4

# EyBond device-family code; 0x0994 = Voltronic P17. Observed to work for
# PI30 (Anern EVO 4200) as well — the dongle forwards payload verbatim
# regardless of devcode.
DEFAULT_DEVCODE = 0x0994

UDP_PORT = 58899
DEFAULT_BROADCAST = "255.255.255.255"
DEFAULT_BIND_PORT = 8899

ANNOUNCE_INTERVAL = 5.0
HEARTBEAT_INTERVAL = 60.0
DEFAULT_TIMEOUT = 5.0
SESSION_WAIT_TIMEOUT = 30.0

# After a bind failure, suppress further bind attempts for this many
# seconds to keep the log from drowning in retries on every coordinator
# tick.  The user has to fix the port conflict externally anyway.
BIND_FAILURE_BACKOFF = 30.0


# ---------------------------------------------------------------------------
# Framing helpers
# ---------------------------------------------------------------------------
@dataclass
class _EyHeader:
    tid: int
    devcode: int
    wire_len: int
    devaddr: int
    fcode: int

    @property
    def total_len(self) -> int:
        return self.wire_len + WIRE_LEN_OFFSET

    @property
    def payload_len(self) -> int:
        return self.total_len - HEADER_SIZE


def _encode_header(
    tid: int, devcode: int, total_len: int, devaddr: int, fcode: int
) -> bytes:
    return struct.pack(
        ">HHHBB", tid, devcode, total_len - WIRE_LEN_OFFSET, devaddr, fcode
    )


def _decode_header(data: bytes) -> _EyHeader:
    tid, devcode, wire_len, devaddr, fcode = struct.unpack(
        ">HHHBB", data[:HEADER_SIZE]
    )
    return _EyHeader(tid, devcode, wire_len, devaddr, fcode)


def _build_heartbeat(tid: int, interval: int) -> bytes:
    """FC=1 server→dongle heartbeat. Payload: UTC date + interval(2)."""
    now = datetime.now(timezone.utc)
    payload = bytes([
        (now.year - 2000) & 0xFF, now.month, now.day,
        now.hour, now.minute, now.second,
    ]) + struct.pack(">H", interval)
    total_len = HEADER_SIZE + len(payload)
    return _encode_header(tid, 0, total_len, 1, FC_HEARTBEAT) + payload


def _build_forward2device(
    tid: int, payload: bytes, devaddr: int, devcode: int = DEFAULT_DEVCODE
) -> bytes:
    total_len = HEADER_SIZE + len(payload)
    return _encode_header(tid, devcode, total_len, devaddr, FC_FORWARD2DEVICE) + payload


def _build_voltronic_frame(command: str) -> bytes:
    """Same ASCII framing the Elfin TCP / serial paths use."""
    body = command.encode("ascii")
    return body + crc16_voltronic(body) + b"\r"


# ---------------------------------------------------------------------------
# URI parsing
# ---------------------------------------------------------------------------
def parse_eybond_uri(device: str) -> tuple[str, int, int, str, str | None]:
    """Return ``(bind_host, bind_port, devaddr, broadcast, announce_ip)``.

    URI: ``eybond://<bind_host>:<bind_port>/<devaddr>?broadcast=<ip>&announce=<ip>``

    ``announce`` is the IP to embed in the ``set>server=<ip>:<port>;``
    UDP payload so the dongle knows where to TCP-connect. It defaults to
    auto-detect via :func:`_detect_local_ip`; explicit override is needed
    in Docker bridge networking where auto-detect returns the container's
    internal address.
    """
    parsed = urlparse(device)
    bind_host = parsed.hostname or "0.0.0.0"
    bind_port = parsed.port or DEFAULT_BIND_PORT
    devaddr_str = (parsed.path or "/1").lstrip("/")
    try:
        devaddr = int(devaddr_str) if devaddr_str else 1
    except ValueError:
        devaddr = 1
    query = parse_qs(parsed.query or "")
    broadcast = (query.get("broadcast") or [DEFAULT_BROADCAST])[0]
    announce_raw = (query.get("announce") or [""])[0].strip()
    announce_ip = announce_raw or None
    return bind_host, bind_port, devaddr, broadcast, announce_ip


def _detect_local_ip() -> str:
    """Best-effort local IP discovery for the announce payload."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Session — one connected dongle
# ---------------------------------------------------------------------------
@dataclass
class _Session:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    peer: str = ""
    pn: str = ""
    _tid: int = 0
    pending: dict[int, asyncio.Future] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def next_tid(self) -> int:
        self._tid = (self._tid + 1) & 0xFFFF
        return self._tid


# ---------------------------------------------------------------------------
# Manager — one TCP listener + one UDP announcer per (bind_host, bind_port)
# ---------------------------------------------------------------------------
class EybondManager:
    def __init__(
        self,
        bind_host: str,
        bind_port: int,
        broadcast: str,
        announce_ip: str | None = None,
    ) -> None:
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.broadcast = broadcast
        # Explicit override for the IP advertised in the UDP payload.
        # ``None`` falls back to :func:`_detect_local_ip`.
        self.announce_ip = announce_ip
        self._server: asyncio.AbstractServer | None = None
        self._announce_task: asyncio.Task | None = None
        self._hb_task: asyncio.Task | None = None
        self._session: _Session | None = None
        self._session_ready = asyncio.Event()
        self._start_lock = asyncio.Lock()
        # Sticky bind-failure state — keeps the log clean instead of
        # retrying every coordinator tick when port 8899 is held by
        # someone else.
        self._bind_failed_until: float = 0.0

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def ensure_started(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._start_lock:
            if self._server is None:
                # Suppress retries while a recent bind failure is still
                # within backoff window.
                now = loop.time()
                if self._bind_failed_until > now:
                    remaining = self._bind_failed_until - now
                    _LOGGER.debug(
                        "EyBond: bind backoff in effect on %s:%d (retry in %.1fs)",
                        self.bind_host, self.bind_port, remaining,
                    )
                    raise OSError(
                        f"EyBond bind backoff active for {remaining:.1f}s more"
                    )
                _LOGGER.info(
                    "EyBond: starting TCP listener on %s:%d ...",
                    self.bind_host, self.bind_port,
                )
                try:
                    self._server = await asyncio.start_server(
                        self._handle_session,
                        self.bind_host,
                        self.bind_port,
                        reuse_address=True,
                    )
                except OSError:
                    self._bind_failed_until = loop.time() + BIND_FAILURE_BACKOFF
                    raise
                sockets = self._server.sockets or ()
                sock_addrs = ", ".join(
                    f"{s.getsockname()[0]}:{s.getsockname()[1]}" for s in sockets
                ) or "<no sockets>"
                _LOGGER.info(
                    "EyBond: TCP listener READY, bound sockets=[%s] (broadcast target %s)",
                    sock_addrs, self.broadcast,
                )
            else:
                _LOGGER.debug(
                    "EyBond: TCP listener already running on %s:%d",
                    self.bind_host, self.bind_port,
                )
            if self._session is None and (
                self._announce_task is None or self._announce_task.done()
            ):
                self._announce_task = asyncio.create_task(self._announce_loop())

    async def shutdown(self) -> None:
        _LOGGER.info(
            "EyBond: manager shutdown begin (bind=%s:%d, session=%s)",
            self.bind_host, self.bind_port,
            self._session.peer if self._session else "none",
        )
        for task in (self._announce_task, self._hb_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._announce_task = None
        self._hb_task = None
        if self._session:
            n_pending = len(self._session.pending)
            for fut in self._session.pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("manager shutting down"))
            try:
                self._session.writer.close()
            except Exception:
                pass
            if n_pending:
                _LOGGER.debug(
                    "EyBond: cancelled %d in-flight request(s) on shutdown",
                    n_pending,
                )
            self._session = None
            self._session_ready.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            _LOGGER.info(
                "EyBond: TCP listener on %s:%d closed",
                self.bind_host, self.bind_port,
            )

    async def _announce_loop(self) -> None:
        if self.announce_ip:
            announce_ip = self.announce_ip
            _LOGGER.debug(
                "EyBond: using configured announce IP %s", announce_ip
            )
        else:
            announce_ip = self.bind_host
            if announce_ip in ("0.0.0.0", "", None):
                announce_ip = _detect_local_ip()
                _LOGGER.debug(
                    "EyBond: auto-detected announce IP %s — set "
                    "eybond_announce_ip explicitly if this is wrong (e.g. "
                    "Docker bridge networking returns container IP, not host)",
                    announce_ip,
                )
        payload = f"set>server={announce_ip}:{self.bind_port};".encode("ascii")
        _LOGGER.info(
            "EyBond: UDP announcer START -> %s:%d every %.1fs, payload=%r",
            self.broadcast, UDP_PORT, ANNOUNCE_INTERVAL, payload.decode(),
        )
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        try:
            while True:
                try:
                    sock.sendto(payload, (self.broadcast, UDP_PORT))
                    _LOGGER.debug(
                        "EyBond: UDP -> %s:%d %r",
                        self.broadcast, UDP_PORT, payload.decode(),
                    )
                except OSError as err:
                    _LOGGER.warning(
                        "EyBond: UDP send failed (target %s:%d): %s",
                        self.broadcast, UDP_PORT, err,
                    )
                await asyncio.sleep(ANNOUNCE_INTERVAL)
        except asyncio.CancelledError:
            _LOGGER.info("EyBond: UDP announcer STOP")
            raise
        finally:
            sock.close()

    async def _stop_announcer(self) -> None:
        task = self._announce_task
        self._announce_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _handle_session(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        peer_str = f"{peer[0]}:{peer[1]}" if peer else "unknown"
        _LOGGER.info("EyBond: dongle CONNECTED from %s", peer_str)

        # New connection wins: drop any existing session.
        if self._session is not None:
            _LOGGER.warning(
                "EyBond: replacing existing session from %s with new %s",
                self._session.peer, peer_str,
            )
            old = self._session
            try:
                old.writer.close()
            except Exception:
                pass
            for fut in old.pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("replaced by new connection"))
            self._session = None
            self._session_ready.clear()

        sess = _Session(reader=reader, writer=writer, peer=peer_str)
        self._session = sess
        self._session_ready.set()

        await self._stop_announcer()
        self._hb_task = asyncio.create_task(self._heartbeat_loop(sess))

        try:
            while True:
                head = await reader.readexactly(HEADER_SIZE)
                h = _decode_header(head)
                payload = b""
                if h.payload_len > 0:
                    payload = await reader.readexactly(h.payload_len)

                _LOGGER.debug(
                    "EyBond RX [%s] tid=%d fc=%d devaddr=%d len=%d payload=%s",
                    peer_str, h.tid, h.fcode, h.devaddr, h.payload_len, payload.hex(),
                )

                if h.fcode == FC_HEARTBEAT:
                    pn = payload[:14].decode("ascii", errors="replace").strip("\x00")
                    if pn and not sess.pn:
                        sess.pn = pn
                        _LOGGER.info(
                            "EyBond: dongle identified, PN=%s peer=%s", pn, peer_str
                        )
                    else:
                        _LOGGER.debug(
                            "EyBond: heartbeat ack from %s (PN=%s)", peer_str, sess.pn
                        )
                elif h.fcode == FC_FORWARD2DEVICE:
                    fut = sess.pending.pop(h.tid, None)
                    if fut and not fut.done():
                        fut.set_result(payload)
                    else:
                        _LOGGER.warning(
                            "EyBond: unsolicited FC=4 tid=%d devaddr=%d (%d bytes) "
                            "payload=%s",
                            h.tid, h.devaddr, len(payload), payload.hex(),
                        )
                else:
                    _LOGGER.debug(
                        "EyBond: unhandled FC=%d tid=%d payload=%s",
                        h.fcode, h.tid, payload.hex(),
                    )

        except asyncio.IncompleteReadError:
            _LOGGER.info("EyBond: dongle %s DISCONNECTED (clean close)", peer_str)
        except asyncio.CancelledError:
            _LOGGER.info("EyBond: session %s cancelled", peer_str)
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("EyBond: session %s error: %s", peer_str, err)
        finally:
            if self._hb_task and not self._hb_task.done():
                self._hb_task.cancel()
                try:
                    await self._hb_task
                except asyncio.CancelledError:
                    pass
            self._hb_task = None
            for fut in sess.pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("dongle disconnected"))
            try:
                writer.close()
            except Exception:
                pass
            if self._session is sess:
                self._session = None
                self._session_ready.clear()
            # Restart announcer so the dongle (or a new one) can re-attach.
            if self._server is not None and (
                self._announce_task is None or self._announce_task.done()
            ):
                self._announce_task = asyncio.create_task(self._announce_loop())

    async def _heartbeat_loop(self, sess: _Session) -> None:
        try:
            while True:
                tid = sess.next_tid()
                frame = _build_heartbeat(tid, int(HEARTBEAT_INTERVAL))
                try:
                    sess.writer.write(frame)
                    await sess.writer.drain()
                    _LOGGER.debug(
                        "EyBond TX [%s] HB tid=%d interval=%ds frame=%s",
                        sess.peer, tid, int(HEARTBEAT_INTERVAL), frame.hex(),
                    )
                except (ConnectionError, OSError) as err:
                    _LOGGER.warning(
                        "EyBond: heartbeat write to %s failed: %s", sess.peer, err
                    )
                    return
                await asyncio.sleep(HEARTBEAT_INTERVAL)
        except asyncio.CancelledError:
            pass

    async def send_voltronic(
        self, devaddr: int, command: str, timeout: float
    ) -> str | None:
        """Send a Voltronic ASCII command via FC=4, return the response as
        a string without the trailing CR (matches Elfin TCP shape), or
        ``None`` on transport-level failure."""
        await self.ensure_started()

        if self._session is None:
            wait = min(timeout, SESSION_WAIT_TIMEOUT)
            _LOGGER.info(
                "EyBond: no dongle connected yet, waiting up to %.1fs for %s "
                "(devaddr=%d) — UDP announcer is broadcasting",
                wait, command, devaddr,
            )
            try:
                await asyncio.wait_for(self._session_ready.wait(), timeout=wait)
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "EyBond: no dongle within %.1fs, dropping %s devaddr=%d",
                    wait, command, devaddr,
                )
                return None

        sess = self._session
        if sess is None:
            _LOGGER.warning(
                "EyBond: session vanished before sending %s devaddr=%d",
                command, devaddr,
            )
            return None

        v_frame = _build_voltronic_frame(command)
        async with sess.send_lock:
            tid = sess.next_tid()
            frame = _build_forward2device(tid, v_frame, devaddr=devaddr)
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[bytes] = loop.create_future()
            sess.pending[tid] = fut
            try:
                sess.writer.write(frame)
                await sess.writer.drain()
                _LOGGER.debug(
                    "EyBond TX [%s] %s tid=%d devaddr=%d v_frame=%s wrapped=%s",
                    sess.peer, command, tid, devaddr,
                    v_frame.hex(), frame.hex(),
                )
            except (ConnectionError, OSError) as err:
                sess.pending.pop(tid, None)
                _LOGGER.warning(
                    "EyBond: write %s devaddr=%d to %s failed: %s",
                    command, devaddr, sess.peer, err,
                )
                return None
            try:
                raw = await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                sess.pending.pop(tid, None)
                _LOGGER.warning(
                    "EyBond: %s devaddr=%d tid=%d TIMEOUT after %.1fs",
                    command, devaddr, tid, timeout,
                )
                return None
            except ConnectionError as err:
                _LOGGER.info(
                    "EyBond: %s devaddr=%d aborted (session lost): %s",
                    command, devaddr, err,
                )
                return None

        # FC=4 payload is the inner Voltronic frame ('(' + body + CRC + CR).
        # Match the Elfin path: keep everything up to the first CR, decode.
        body, _, _ = raw.partition(b"\r")
        response = body.decode("ascii", errors="ignore")
        _LOGGER.debug(
            "EyBond RX-payload %s devaddr=%d (%d bytes raw) ascii=%r",
            command, devaddr, len(raw), response,
        )
        if "NAK" in response:
            _LOGGER.info(
                "EyBond: %s devaddr=%d → NAK from inverter", command, devaddr
            )
        return response


# ---------------------------------------------------------------------------
# Module-level registry: one manager per (bind_host, bind_port)
# ---------------------------------------------------------------------------
_managers: dict[tuple[str, int], EybondManager] = {}
_registry_lock = asyncio.Lock()


async def _get_manager(
    bind_host: str, bind_port: int, broadcast: str, announce_ip: str | None
) -> EybondManager:
    async with _registry_lock:
        key = (bind_host, bind_port)
        mgr = _managers.get(key)
        if mgr is None:
            _LOGGER.info(
                "EyBond: creating new manager for bind=%s:%d broadcast=%s announce=%s",
                bind_host, bind_port, broadcast, announce_ip or "<auto>",
            )
            mgr = EybondManager(bind_host, bind_port, broadcast, announce_ip)
            _managers[key] = mgr
        else:
            if mgr.broadcast != broadcast:
                _LOGGER.info(
                    "EyBond: broadcast target changed %s -> %s",
                    mgr.broadcast, broadcast,
                )
            mgr.broadcast = broadcast
            if mgr.announce_ip != announce_ip:
                _LOGGER.info(
                    "EyBond: announce IP changed %s -> %s (restart announcer)",
                    mgr.announce_ip or "<auto>", announce_ip or "<auto>",
                )
                mgr.announce_ip = announce_ip
                # Restart announcer so the new IP takes effect immediately.
                await mgr._stop_announcer()
                if mgr._session is None and mgr._server is not None:
                    mgr._announce_task = asyncio.create_task(mgr._announce_loop())
    await mgr.ensure_started()
    return mgr


async def send_eybond_voltronic(
    device: str, command: str, timeout: float = DEFAULT_TIMEOUT
) -> str | None:
    """Parse the URI, get/create the manager, send the command."""
    bind_host, bind_port, devaddr, broadcast, announce_ip = parse_eybond_uri(device)
    _LOGGER.debug(
        "EyBond: dispatch %s for device=%s "
        "(bind=%s:%d devaddr=%d broadcast=%s announce=%s)",
        command, device, bind_host, bind_port, devaddr, broadcast,
        announce_ip or "<auto>",
    )
    try:
        mgr = await _get_manager(bind_host, bind_port, broadcast, announce_ip)
    except OSError as err:
        msg = str(err)
        # Once-per-backoff-window error; subsequent attempts surface as
        # debug only (see ensure_started's backoff branch) so the log
        # doesn't drown.
        if "backoff active" in msg:
            _LOGGER.debug("EyBond: %s — skipping %s", msg, command)
        else:
            _LOGGER.error(
                "EyBond: TCP bind FAILED on %s:%d (%s). Likely causes:\n"
                "  - another process is bound to this port "
                "(check: ss -ltnp '( sport = :%d )' or netstat)\n"
                "  - a leftover test script (test-anern-local.py / "
                "test-smg2-local.py) is still running\n"
                "  - a second HA instance with eybond:// is bound to "
                "the same IP\n"
                "  - HA crashed and the previous listener is in TIME_WAIT "
                "(wait %ds for the kernel to release it)\n"
                "Suppressing further bind attempts for %ds.",
                bind_host, bind_port, err, bind_port,
                int(BIND_FAILURE_BACKOFF), int(BIND_FAILURE_BACKOFF),
            )
        return None
    return await mgr.send_voltronic(devaddr, command, timeout)


async def send_eybond_set_command(
    device: str, command: str, timeout: float = 30.0
) -> dict:
    """Send a Voltronic set command (POPxx, PCPxx, ...) and classify the
    ACK/NAK response. Mirrors :func:`elfin_tcp.send_voltronic_set_command`."""
    response = await send_eybond_voltronic(device, command, timeout)
    if response is None:
        return {"error": "no response"}
    if "ACK" in response:
        return {"status": "ACK"}
    if "NAK" in response:
        return {"status": "NAK"}
    if not response:
        return {"error": "empty response"}
    return {"raw": response}


async def shutdown_all_eybond_managers() -> None:
    """Drain all managers — call on integration unload."""
    async with _registry_lock:
        managers = list(_managers.values())
        _managers.clear()
    if not managers:
        _LOGGER.debug("EyBond: shutdown — no managers to stop")
        return
    _LOGGER.info("EyBond: shutting down %d manager(s)", len(managers))
    for mgr in managers:
        try:
            _LOGGER.debug(
                "EyBond: stopping manager %s:%d", mgr.bind_host, mgr.bind_port
            )
            await mgr.shutdown()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("EyBond: manager shutdown error")
    _LOGGER.info("EyBond: all managers stopped")
