"""In-memory ring buffer of raw transport frames.

Captures the last few responses received from each inverter command so
they're available for retrieval via the HA "Download Diagnostics" button.
Critical for debugging field-shift / parser / CRC issues where the only
authoritative evidence is the exact bytes the inverter sent.

Implementation choices:

* Module-level dict (rather than ``hass.data``) so transports can call
  ``record()`` without threading ``hass`` through every protocol class.
  Single-tenant integration, so no isolation concerns.
* Buffer per command (QPIGS / QPIRI / GS / …) keeps related frames
  together; ``maxlen=20`` covers ~3 minutes at the default 10-second
  poll interval — enough to catch a transient anomaly while staying
  well under any reasonable memory budget.
* Both hex and printable-ASCII representations are recorded — hex for
  byte-exact reproduction, ASCII for at-a-glance human reading.
* Optional fields are added only when the caller supplies them, so the
  base entry shape stays stable for existing callers:

  - ``outcome`` — why a request produced no usable reply (``"timeout"``,
    ``"no_session"``, …). Such entries carry empty bytes; their absence
    means a reply was received.
  - ``transport`` / ``pn`` / ``devaddr`` — routing tags (the EyBond path
    sets them) so device diagnostics can pick out one dongle's frames
    from a listener shared by several. Never a host or URI: frames are
    not redacted.
"""
from __future__ import annotations

from collections import deque
from datetime import UTC, datetime

_MAX_FRAMES_PER_COMMAND = 20
_FRAMES: dict[str, deque[dict]] = {}


def _safe_ascii(b: bytes) -> str:
    """Render ``b`` as printable ASCII with non-printable bytes shown as
    ``\\xNN`` escapes. Keeps the diagnostic readable even when the frame
    contains CRC bytes / control chars / random corruption."""
    return "".join(
        chr(c) if 0x20 <= c < 0x7F else f"\\x{c:02x}" for c in b
    )


def record(
    command: str,
    raw_bytes: bytes,
    crc_valid: bool,
    *,
    outcome: str | None = None,
    transport: str | None = None,
    pn: str | None = None,
    devaddr: int | None = None,
) -> None:
    """Append one frame snapshot to the per-command ring buffer.

    The keyword-only fields are optional (see module docstring) and are
    omitted from the entry when ``None``.
    """
    buf = _FRAMES.get(command)
    if buf is None:
        buf = deque(maxlen=_MAX_FRAMES_PER_COMMAND)
        _FRAMES[command] = buf
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "byte_count": len(raw_bytes),
        "crc_valid": crc_valid,
        "raw_hex": raw_bytes.hex(" "),
        "raw_ascii": _safe_ascii(raw_bytes),
    }
    for key, value in (
        ("outcome", outcome),
        ("transport", transport),
        ("pn", pn),
        ("devaddr", devaddr),
    ):
        if value is not None:
            entry[key] = value
    buf.append(entry)


def snapshot() -> dict[str, list[dict]]:
    """Return a JSON-serialisable snapshot of all buffers for diagnostics."""
    return {cmd: list(buf) for cmd, buf in _FRAMES.items()}


def snapshot_for(
    transport: str | None,
    pn: str | None = None,
    devaddr: int | None = None,
) -> dict[str, list[dict]]:
    """Snapshot narrowed to one device's frames (device-level diagnostics).

    ``transport=None`` selects untagged entries (the direct TCP / serial
    transports, which talk to exactly one device each). For a tagged
    transport (``"eybond"``) entries must match ``devaddr`` and — when
    ``pn`` is given — ``pn``; ``pn=None`` is the legacy PN-less dongle,
    which matches any PN. Commands with no matching entry are omitted.
    """
    out: dict[str, list[dict]] = {}
    for cmd, buf in _FRAMES.items():
        if transport is None:
            hits = [e for e in buf if "transport" not in e]
        else:
            hits = [
                e
                for e in buf
                if e.get("transport") == transport
                and (devaddr is None or e.get("devaddr") == devaddr)
                and (pn is None or e.get("pn") == pn)
            ]
        if hits:
            out[cmd] = hits
    return out


def clear() -> None:
    """Drop all buffers — called on integration unload to free memory."""
    _FRAMES.clear()
