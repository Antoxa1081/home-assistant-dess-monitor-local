"""In-process diagnostic event hub for the debug panel.

A ring buffer of recent debug events plus a set of live subscriber queues.
Hot-path producers (the EyBond transport, the coordinator) publish ONLY while
at least one debug panel is subscribed — they guard call sites with
``if diag_hub.active(): diag_hub.publish(...)`` so there's zero cost (not even
building the event dict) in normal operation.

Pure module — no Home Assistant imports — so it's unit-testable in isolation.
Single-tenant integration, so a module-level singleton is fine (mirrors
``frame_log``).

Event shape is ``{"t": <kind>, "ts": <epoch_s>, ...}`` where ``t`` is one of
``frame`` / ``session`` / ``cycle`` / ``dongles``. ``ts`` is filled in by
``publish`` if the producer didn't set it.
"""
from __future__ import annotations

import time
from collections import deque

_RING_MAX = 1000

# Module-level singletons.
_ring: deque[dict] = deque(maxlen=_RING_MAX)
_subscribers: set = set()  # set[asyncio.Queue[dict]]


def active() -> bool:
    """True when at least one panel is subscribed.

    Producers test this before building an event so the debug instrumentation
    costs nothing unless the panel is actually open.
    """
    return bool(_subscribers)


def publish(event: dict) -> None:
    """Record a debug event and fan it out to every live subscriber.

    No-op when nobody is listening. A subscriber whose queue is full (a slow
    panel) drops this event rather than blocking the producer — the panel
    re-syncs from the next ``state`` snapshot.
    """
    if not _subscribers:
        return
    event.setdefault("ts", round(time.time(), 3))
    _ring.append(event)
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except Exception:  # noqa: BLE001 — QueueFull / closed: drop for this sub
            pass


def subscribe(queue) -> None:
    """Register a subscriber queue (created by the WebSocket handler)."""
    _subscribers.add(queue)


def unsubscribe(queue) -> None:
    _subscribers.discard(queue)


def recent(limit: int | None = None) -> list[dict]:
    """Snapshot of the most recent buffered events for a just-connected panel."""
    items = list(_ring)
    return items[-limit:] if limit else items


def clear() -> None:
    """Drop the ring and all subscribers (integration unload)."""
    _ring.clear()
    _subscribers.clear()
