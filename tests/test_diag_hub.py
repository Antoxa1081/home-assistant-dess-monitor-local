"""Tests for the debug-panel diagnostic event hub (diag_hub.py). Pure — no HA."""
import asyncio

from custom_components.dess_monitor_local import diag_hub


def _fresh():
    diag_hub.clear()


def test_inactive_with_no_subscribers_and_publish_is_noop():
    _fresh()
    assert diag_hub.active() is False
    diag_hub.publish({"t": "frame", "hex": "00"})
    # Nothing buffered while inactive (the active gate).
    assert diag_hub.recent() == []


def test_publish_fans_out_and_buffers_when_active():
    _fresh()
    q = asyncio.Queue()
    diag_hub.subscribe(q)
    assert diag_hub.active() is True

    diag_hub.publish({"t": "session", "ev": "connect", "pn": "PN1"})
    # Delivered to the live subscriber...
    ev = q.get_nowait()
    assert ev["t"] == "session" and ev["pn"] == "PN1"
    assert "ts" in ev  # publish stamps it
    # ...and kept in the ring for a later-connecting panel.
    assert diag_hub.recent()[-1]["pn"] == "PN1"

    diag_hub.unsubscribe(q)
    assert diag_hub.active() is False


def test_producer_supplied_ts_is_kept():
    _fresh()
    q = asyncio.Queue()
    diag_hub.subscribe(q)
    diag_hub.publish({"t": "cycle", "ts": 111.0, "dur_s": 3.0})
    assert q.get_nowait()["ts"] == 111.0


def test_full_subscriber_queue_drops_without_raising():
    _fresh()
    q = asyncio.Queue(maxsize=1)
    diag_hub.subscribe(q)
    diag_hub.publish({"t": "frame", "n": 1})  # fills the queue
    diag_hub.publish({"t": "frame", "n": 2})  # would overflow → dropped, no raise
    assert q.get_nowait()["n"] == 1
    assert q.empty()  # the second was dropped for this slow subscriber
    # But the ring still has both (panel re-syncs from a state snapshot).
    assert [e["n"] for e in diag_hub.recent()] == [1, 2]


def test_recent_limit_and_clear():
    _fresh()
    q = asyncio.Queue()
    diag_hub.subscribe(q)
    for i in range(5):
        diag_hub.publish({"t": "frame", "n": i})
    assert [e["n"] for e in diag_hub.recent(limit=2)] == [3, 4]
    diag_hub.clear()
    assert diag_hub.recent() == [] and diag_hub.active() is False
