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
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

_MAX_FRAMES_PER_COMMAND = 20
_FRAMES: dict[str, deque[dict]] = {}


def _safe_ascii(b: bytes) -> str:
    """Render ``b`` as printable ASCII with non-printable bytes shown as
    ``\\xNN`` escapes. Keeps the diagnostic readable even when the frame
    contains CRC bytes / control chars / random corruption."""
    return "".join(
        chr(c) if 0x20 <= c < 0x7F else f"\\x{c:02x}" for c in b
    )


def record(command: str, raw_bytes: bytes, crc_valid: bool) -> None:
    """Append one frame snapshot to the per-command ring buffer."""
    buf = _FRAMES.get(command)
    if buf is None:
        buf = deque(maxlen=_MAX_FRAMES_PER_COMMAND)
        _FRAMES[command] = buf
    buf.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "byte_count": len(raw_bytes),
        "crc_valid": crc_valid,
        "raw_hex": raw_bytes.hex(" "),
        "raw_ascii": _safe_ascii(raw_bytes),
    })


def snapshot() -> dict[str, list[dict]]:
    """Return a JSON-serialisable snapshot of all buffers for diagnostics."""
    return {cmd: list(buf) for cmd, buf in _FRAMES.items()}


def clear() -> None:
    """Drop all buffers — called on integration unload to free memory."""
    _FRAMES.clear()
