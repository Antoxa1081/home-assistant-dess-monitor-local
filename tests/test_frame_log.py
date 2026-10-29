"""Tests for the diagnostic frame ring buffer (frame_log.py)."""
from custom_components.dess_monitor_local import frame_log


class TestFrameLog:
    def setup_method(self):
        frame_log.clear()

    def teardown_method(self):
        frame_log.clear()

    def test_record_and_snapshot(self):
        frame_log.record("QPIGS", b"(239.3 50.0\x12\x34", crc_valid=True)
        snap = frame_log.snapshot()
        assert "QPIGS" in snap
        entry = snap["QPIGS"][0]
        assert entry["crc_valid"] is True
        assert entry["byte_count"] == len(b"(239.3 50.0\x12\x34")
        assert "raw_hex" in entry and "raw_ascii" in entry

    def test_ascii_escapes_nonprintable(self):
        frame_log.record("QPIGS", b"AB\x00\xff", crc_valid=False)
        entry = frame_log.snapshot()["QPIGS"][0]
        assert entry["raw_ascii"] == "AB\\x00\\xff"

    def test_ring_buffer_caps(self):
        for i in range(40):
            frame_log.record("QPIGS", bytes([i]), crc_valid=True)
        entries = frame_log.snapshot()["QPIGS"]
        # Bounded to _MAX_FRAMES_PER_COMMAND; oldest dropped.
        assert len(entries) == frame_log._MAX_FRAMES_PER_COMMAND
        assert len(entries) <= 40

    def test_per_command_separation(self):
        frame_log.record("QPIGS", b"a", crc_valid=True)
        frame_log.record("QPIRI", b"b", crc_valid=True)
        snap = frame_log.snapshot()
        assert set(snap) == {"QPIGS", "QPIRI"}

    def test_clear(self):
        frame_log.record("QPIGS", b"a", crc_valid=True)
        frame_log.clear()
        assert frame_log.snapshot() == {}


class TestOptionalFieldsAndFilter:
    def setup_method(self):
        frame_log.clear()

    def teardown_method(self):
        frame_log.clear()

    def test_optional_fields_omitted_by_default(self):
        frame_log.record("QPIGS", b"a", crc_valid=True)
        entry = frame_log.snapshot()["QPIGS"][0]
        assert set(entry) == {"timestamp", "byte_count", "crc_valid", "raw_hex", "raw_ascii"}

    def test_outcome_and_tags_recorded(self):
        frame_log.record(
            "QPIGS", b"", False, outcome="timeout", transport="eybond", pn="PN1", devaddr=1,
        )
        entry = frame_log.snapshot()["QPIGS"][0]
        assert entry["outcome"] == "timeout"
        assert entry["byte_count"] == 0 and entry["raw_hex"] == ""
        assert (entry["transport"], entry["pn"], entry["devaddr"]) == ("eybond", "PN1", 1)

    def test_snapshot_for_eybond_filters_by_pn_and_devaddr(self):
        frame_log.record("QPIGS", b"a", True, transport="eybond", pn="PN1", devaddr=1)
        frame_log.record("QPIGS", b"b", True, transport="eybond", pn="PN2", devaddr=1)
        frame_log.record("QPIGS", b"c", True, transport="eybond", pn="PN1", devaddr=2)
        frame_log.record("QPIRI", b"d", True)  # untagged direct transport
        snap = frame_log.snapshot_for("eybond", "PN1", 1)
        assert list(snap) == ["QPIGS"]
        assert [e["raw_ascii"] for e in snap["QPIGS"]] == ["a"]
        # Legacy PN-less dongle: any PN on that devaddr.
        legacy = frame_log.snapshot_for("eybond", None, 1)
        assert [e["raw_ascii"] for e in legacy["QPIGS"]] == ["a", "b"]

    def test_snapshot_for_untagged(self):
        frame_log.record("QPIGS", b"a", True, transport="eybond", pn="PN1", devaddr=1)
        frame_log.record("QPIRI", b"d", True)
        assert list(frame_log.snapshot_for(None)) == ["QPIRI"]
