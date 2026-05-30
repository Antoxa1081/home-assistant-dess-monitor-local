"""EyBond / SmartESS WiFi dongle transport.

URI: ``eybond://<bind_host>:<bind_port>/<rs485_devaddr>[?broadcast=<ip>]``

Unlike the other transports, the dongle initiates the TCP connection to
us. We:

  1. Bind a TCP listener on the configured port (default 8899).
  2. Broadcast ``set>server=<MY_IP>:<port>;`` on UDP/58899 every 5s
     continuously — that command tells dongles to switch upstream from the
     SmartESS cloud to us, and keeping it running lets additional dongles
     attach at any time.
  3. Accept multiple dongle connections on one listener. Each session is
     kept alive with FC=1 heartbeats and identified by the ``PN`` carried
     in the dongle's own heartbeat.
  4. On each read, wrap the Voltronic ASCII command (``QPIGS\\r``,
     ``QPIRI\\r``, ...) inside an EyBond FC=4 (Forward2Device) frame,
     await the response keyed by TID, unwrap, and return the inner
     Voltronic ASCII payload.

The wire format INSIDE FC=4 is byte-for-byte the same as ``tcp://`` and
serial transports — the dispatcher feeds the unwrapped response straight
into ``decode_direct_response``.

A single TCP listener serves all ``eybond://`` devices in this HA
instance; ``devaddr`` in the URI selects which inverter on the RS485 bus
(1-based). Multiple dongles can share one listener — requests route by
``PN`` (see :meth:`EybondManager.send_frame`); ``pn=None`` keeps the legacy
single-dongle behaviour. Different bind ports get independent listeners.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
import struct
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from ...const import PROTOCOL_PI18
from ..crc import build_pi30_frame
from ..decoders.pi18 import build_request_frame

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
    now = datetime.now(UTC)
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


def _resolve_broadcast_for_announce_ip(announce_ip: str) -> str:
    """Best-effort broadcast resolution by matching announce_ip to local subnets."""
    try:
        target = ipaddress.IPv4Address(announce_ip.strip())
    except ValueError:
        return DEFAULT_BROADCAST

    if target.is_loopback:
        return DEFAULT_BROADCAST

    # Windows: parse ipconfig
    if os.name == "nt":
        try:
            # shell=True handles Windows command lookup; cp866/utf-8 covers most locales
            raw = subprocess.check_output("ipconfig", shell=True).decode("cp866", errors="ignore")
            current_ip = None
            for line in raw.splitlines():
                if "IPv4" in line:
                    m = re.search(r":\s*([\d\.]+)", line)
                    if m:
                        current_ip = m.group(1)
                elif "Subnet Mask" in line or "Маска подсети" in line:
                    m = re.search(r":\s*([\d\.]+)", line)
                    if m and current_ip:
                        try:
                            net = ipaddress.IPv4Network(f"{current_ip}/{m.group(1)}", strict=False)
                            if target in net:
                                return str(net.broadcast_address)
                        except ValueError:
                            pass
                    current_ip = None
        except Exception:
            pass
    # Linux: parse ip addr
    else:
        try:
            raw = subprocess.check_output(["ip", "-4", "addr", "show"]).decode("utf-8", errors="ignore")
            for m in re.finditer(r"inet\s+([\d\.]+)/(\d+)", raw):
                try:
                    net = ipaddress.IPv4Network(f"{m.group(1)}/{m.group(2)}", strict=False)
                    if target in net:
                        return str(net.broadcast_address)
                except ValueError:
                    pass
        except Exception:
            pass

    # Heuristic fallback: if no matching subnet found (common in Docker without host networking),
    # assume a standard /24 network and use .255. This is much more likely to work
    # than 255.255.255.255 which often gets trapped inside the container.
    try:
        parts = str(target).split(".")
        if len(parts) == 4:
            heuristic = ".".join(parts[:3]) + ".255"
            _LOGGER.debug(
                "EyBond: could not find subnet for %s, using heuristic broadcast %s",
                announce_ip, heuristic
            )
            return heuristic
    except Exception:
        pass

    return DEFAULT_BROADCAST


# ---------------------------------------------------------------------------
# Session — one connected dongle
# ---------------------------------------------------------------------------
@dataclass(eq=False)  # identity hash/eq so sessions are usable as set elements
class _Session:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    peer: str = ""
    pn: str = ""
    _tid: int = 0
    pending: dict[int, asyncio.Future] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    hb_task: asyncio.Task | None = None

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
        # Multi-session state: a single TCP listener accepts many dongles
        # on one port. Sessions start in ``_sessions`` (unidentified) and
        # are promoted into ``_sessions_by_pn`` once their heartbeat
        # reveals a PN. ``_ready_by_pn`` gates per-PN waiters; ``_any_ready``
        # gates legacy PN-less requests (single-dongle compatibility).
        self._sessions: set[_Session] = set()
        self._sessions_by_pn: dict[str, _Session] = {}
        self._ready_by_pn: dict[str, asyncio.Event] = {}
        self._any_ready = asyncio.Event()
        self._start_lock = asyncio.Lock()
        # Sticky bind-failure state — keeps the log clean instead of
        # retrying every coordinator tick when port 8899 is held by
        # someone else.
        self._bind_failed_until: float = 0.0

    @property
    def connected(self) -> bool:
        return bool(self._sessions)

    @property
    def identified_pns(self) -> list[str]:
        """PNs of currently connected, identified dongles."""
        return sorted(self._sessions_by_pn)

    def _ready_event_for(self, pn: str) -> asyncio.Event:
        ev = self._ready_by_pn.get(pn)
        if ev is None:
            ev = asyncio.Event()
            self._ready_by_pn[pn] = ev
        return ev

    def _any_session(self) -> _Session | None:
        """Deterministically pick one session for a legacy PN-less request."""
        if not self._sessions:
            return None
        if len(self._sessions) > 1:
            _LOGGER.warning(
                "EyBond: PN-less request with %d sessions connected on %s:%d; "
                "routing to first identified — specify a PN to target a "
                "specific dongle",
                len(self._sessions), self.bind_host, self.bind_port,
            )
        identified = sorted(
            (s for s in self._sessions if s.pn), key=lambda s: s.pn
        )
        if identified:
            return identified[0]
        return next(iter(self._sessions))

    def _drop_session(self, sess: _Session, reason: str) -> None:
        """Idempotently remove a session from all maps and fail its pending."""
        self._sessions.discard(sess)
        if sess.pn and self._sessions_by_pn.get(sess.pn) is sess:
            del self._sessions_by_pn[sess.pn]
            ev = self._ready_by_pn.get(sess.pn)
            if ev is not None:
                ev.clear()
        for fut in list(sess.pending.values()):
            if not fut.done():
                fut.set_exception(ConnectionError(reason))
        sess.pending.clear()
        if not self._sessions:
            self._any_ready.clear()

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
            # The announcer runs continuously so additional dongles can
            # discover us and attach at any time (multi-session model).
            if self._announce_task is None or self._announce_task.done():
                self._announce_task = asyncio.create_task(self._announce_loop())

    async def shutdown(self) -> None:
        _LOGGER.info(
            "EyBond: manager shutdown begin (bind=%s:%d, sessions=%d %s)",
            self.bind_host, self.bind_port, len(self._sessions),
            self.identified_pns or "[]",
        )
        if self._announce_task and not self._announce_task.done():
            self._announce_task.cancel()
            try:
                await self._announce_task
            except asyncio.CancelledError:
                pass
        self._announce_task = None
        for sess in list(self._sessions):
            if sess.hb_task and not sess.hb_task.done():
                sess.hb_task.cancel()
                try:
                    await sess.hb_task
                except asyncio.CancelledError:
                    pass
            n_pending = len(sess.pending)
            self._drop_session(sess, "manager shutting down")
            try:
                sess.writer.close()
            except Exception:
                pass
            if n_pending:
                _LOGGER.debug(
                    "EyBond: cancelled %d in-flight request(s) on %s shutdown",
                    n_pending, sess.peer,
                )
        self._sessions.clear()
        self._sessions_by_pn.clear()
        self._ready_by_pn.clear()
        self._any_ready.clear()
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

        if self.broadcast == "255.255.255.255" or self.broadcast == DEFAULT_BROADCAST:
            resolved = _resolve_broadcast_for_announce_ip(announce_ip)
            if resolved and resolved != "255.255.255.255":
                _LOGGER.info(
                    "EyBond: broadcast resolved: announce=%s broadcast=%s",
                    announce_ip, resolved,
                )
                self.broadcast = resolved

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

        # Multi-session: a new connection NEVER evicts an existing one.
        # It joins as unidentified until its heartbeat reveals a PN.
        sess = _Session(reader=reader, writer=writer, peer=peer_str)
        self._sessions.add(sess)
        self._any_ready.set()

        sess.hb_task = asyncio.create_task(self._heartbeat_loop(sess))

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
                        # Same physical dongle reconnecting? Evict the stale
                        # session bound to this PN before claiming it.
                        old = self._sessions_by_pn.get(pn)
                        if old is not None and old is not sess:
                            _LOGGER.warning(
                                "EyBond: PN=%s reconnected from %s, replacing "
                                "stale session %s",
                                pn, peer_str, old.peer,
                            )
                            try:
                                old.writer.close()
                            except Exception:
                                pass
                            self._drop_session(
                                old, "replaced by reconnect with same PN"
                            )
                        self._sessions_by_pn[pn] = sess
                        self._ready_event_for(pn).set()
                        _LOGGER.info(
                            "EyBond: dongle identified, PN=%s peer=%s "
                            "(now %d session(s): %s)",
                            pn, peer_str, len(self._sessions), self.identified_pns,
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
            if sess.hb_task and not sess.hb_task.done():
                sess.hb_task.cancel()
                try:
                    await sess.hb_task
                except asyncio.CancelledError:
                    pass
            sess.hb_task = None
            # Disconnecting one dongle must not affect the others. Drop only
            # this session; the announcer keeps running for re-attach.
            self._drop_session(sess, "dongle disconnected")
            try:
                writer.close()
            except Exception:
                pass
            _LOGGER.debug(
                "EyBond: session %s removed (%d session(s) remain: %s)",
                peer_str, len(self._sessions), self.identified_pns,
            )

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

    async def _wait_for_session(
        self, pn: str | None, timeout: float, context: str, devaddr: int
    ) -> _Session | None:
        """Resolve the target session, waiting for a dongle if needed.

        ``pn=None`` is the legacy single-dongle path: route to whichever
        session is connected. A non-empty ``pn`` targets that specific
        dongle and waits on its per-PN ready event.
        """
        if pn:
            sess = self._sessions_by_pn.get(pn)
            if sess is not None:
                return sess
            ev = self._ready_event_for(pn)
        else:
            sess = self._any_session()
            if sess is not None:
                return sess
            ev = self._any_ready

        wait = min(timeout, SESSION_WAIT_TIMEOUT)
        _LOGGER.info(
            "EyBond: no dongle connected yet, waiting up to %.1fs for %s "
            "(pn=%s devaddr=%d) — UDP announcer is broadcasting",
            wait, context or "frame", pn or "<any>", devaddr,
        )
        try:
            await asyncio.wait_for(ev.wait(), timeout=wait)
        except TimeoutError:
            _LOGGER.warning(
                "EyBond: no dongle (pn=%s) within %.1fs, dropping %s devaddr=%d",
                pn or "<any>", wait, context or "frame", devaddr,
            )
            return None
        return self._sessions_by_pn.get(pn) if pn else self._any_session()

    async def send_frame(
        self,
        devaddr: int,
        v_frame: bytes,
        timeout: float,
        context: str = "",
        pn: str | None = None,
    ) -> bytes | None:
        """Send a raw frame via FC=4, return the response as bytes.

        ``pn`` selects which connected dongle to target; ``None`` keeps the
        legacy single-dongle behaviour (route to the only/first session).
        """
        await self.ensure_started()

        sess = await self._wait_for_session(pn, timeout, context, devaddr)
        if sess is None:
            return None

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
                    sess.peer, context or "frame", tid, devaddr,
                    v_frame.hex(), frame.hex(),
                )
            except (ConnectionError, OSError) as err:
                sess.pending.pop(tid, None)
                _LOGGER.warning(
                    "EyBond: write %s devaddr=%d to %s failed: %s",
                    context or "frame", devaddr, sess.peer, err,
                )
                return None
            try:
                raw = await asyncio.wait_for(fut, timeout=timeout)
            except TimeoutError:
                sess.pending.pop(tid, None)
                _LOGGER.warning(
                    "EyBond: %s devaddr=%d tid=%d TIMEOUT after %.1fs",
                    context or "frame", devaddr, tid, timeout,
                )
                return None
            except ConnectionError as err:
                _LOGGER.info(
                    "EyBond: %s devaddr=%d aborted (session lost): %s",
                    context or "frame", devaddr, err,
                )
                return None

        _LOGGER.debug(
            "EyBond RX-payload %s devaddr=%d (%d bytes raw)",
            context or "frame", devaddr, len(raw),
        )
        return raw


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
            # Update parameters if they changed in the URI.
            # If broadcast is DEFAULT_BROADCAST (255.255.255.255), we don't overwrite
            # an already resolved specific broadcast address.
            if broadcast != DEFAULT_BROADCAST and mgr.broadcast != broadcast:
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
                # If broadcast was also default, reset it to force re-resolution for the new IP
                if broadcast == DEFAULT_BROADCAST:
                    mgr.broadcast = DEFAULT_BROADCAST

                # Restart announcer so the new IP takes effect immediately.
                # The announcer runs continuously in the multi-session model.
                await mgr._stop_announcer()
                if mgr._server is not None:
                    mgr._announce_task = asyncio.create_task(mgr._announce_loop())
    await mgr.ensure_started()
    return mgr


async def send_eybond_bytes(
    device: str,
    v_frame: bytes,
    timeout: float = DEFAULT_TIMEOUT,
    context: str = "",
    pn: str | None = None,
) -> bytes | None:
    """Parse the URI, get/create the manager, send the raw frame.

    ``pn`` optionally targets a specific dongle on a shared listener; when
    ``None`` the legacy single-dongle routing is used.
    """
    bind_host, bind_port, devaddr, broadcast, announce_ip = parse_eybond_uri(device)

    _LOGGER.debug(
        "EyBond: dispatch frame %s for device=%s "
        "(bind=%s:%d devaddr=%d broadcast=%s announce=%s)",
        context or v_frame.hex(), device, bind_host, bind_port, devaddr, broadcast,
        announce_ip or "<auto>",
    )
    try:
        mgr = await _get_manager(bind_host, bind_port, broadcast, announce_ip)
    except OSError as err:
        msg = str(err)
        if "backoff active" in msg:
            _LOGGER.debug("EyBond: %s — skipping %s", msg, context or "frame")
        else:
            _LOGGER.error(
                "EyBond: TCP bind FAILED on %s:%d (%s). Suppressing further bind attempts for %ds.",
                bind_host, bind_port, err, int(BIND_FAILURE_BACKOFF),
            )
        return None
    return await mgr.send_frame(devaddr, v_frame, timeout, context=context, pn=pn)


async def send_eybond_voltronic(
    device: str,
    command: str,
    timeout: float = DEFAULT_TIMEOUT,
    protocol: str | None = None,
    pn: str | None = None,
) -> bytes | None:
    """Backward-compatible wrapper for Voltronic/PI18 commands."""
    if protocol == PROTOCOL_PI18 or device.startswith("eybond-pi18://"):
        v_frame = build_request_frame(command)
    else:
        v_frame = build_pi30_frame(command)
    return await send_eybond_bytes(device, v_frame, timeout, context=command, pn=pn)


async def send_eybond_set_command(
    device: str,
    command: str,
    timeout: float = 30.0,
    protocol: str | None = None,
    pn: str | None = None,
) -> dict:
    """Send a set command and classify the ACK/NAK response."""
    response = await send_eybond_voltronic(
        device, command, timeout, protocol=protocol, pn=pn
    )
    if response is None:
        return {"error": "no response"}
    if b"ACK" in response or response.startswith(b"^1"):
        return {"status": "ACK"}
    if b"NAK" in response or response.startswith(b"^0"):
        return {"status": "NAK"}
    if not response:
        return {"error": "empty response"}
    return {"raw": response.decode("ascii", errors="ignore")}


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
