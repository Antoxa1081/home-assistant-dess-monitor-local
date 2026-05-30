"""Tests for the EyBond discovery registry (api/protocols/eybond_discovery.py).

Phase 2: per-hub registry of dongles keyed by PN with lifecycle tracking
(last_seen / status) and per-device configuration (enabled / protocol /
devaddr / name). Pure module — no hass, deterministic clock injected."""
from custom_components.dess_monitor_local.api.protocols.eybond_discovery import (
    DongleRecord,
    DongleStatus,
    EybondRegistry,
)


class _Clock:
    """Deterministic ISO-timestamp source: 't0', 't1', 't2', ..."""

    def __init__(self):
        self.n = 0

    def __call__(self) -> str:
        v = f"t{self.n}"
        self.n += 1
        return v


class TestRecordSeen:
    def test_first_sight_creates_connected_record(self):
        reg = EybondRegistry(now=_Clock())
        rec = reg.record_seen("PN001", "10.0.0.1:1111")
        assert rec.pn == "PN001"
        assert rec.status is DongleStatus.CONNECTED
        assert rec.peer == "10.0.0.1:1111"
        assert rec.first_seen == "t0"
        assert rec.last_seen == "t0"
        # Discovered dongles start unconfigured: disabled, no protocol.
        assert rec.enabled is False
        assert rec.protocol is None
        assert len(reg) == 1
        assert "PN001" in reg

    def test_subsequent_sight_refreshes_last_seen_keeps_first(self):
        reg = EybondRegistry(now=_Clock())
        reg.record_seen("PN001", "10.0.0.1:1111")
        rec = reg.record_seen("PN001", "10.0.0.1:2222")
        assert rec.first_seen == "t0"
        assert rec.last_seen == "t1"
        assert rec.peer == "10.0.0.1:2222"
        assert len(reg) == 1

    def test_record_seen_preserves_user_config(self):
        reg = EybondRegistry(now=_Clock())
        reg.set_enabled("PN001", True)
        reg.set_protocol("PN001", "voltronic")
        reg.record_seen("PN001", "10.0.0.1:1111")
        rec = reg.get("PN001")
        assert rec.enabled is True
        assert rec.protocol == "voltronic"
        assert rec.status is DongleStatus.CONNECTED


class TestDisconnect:
    def test_mark_disconnected(self):
        clock = _Clock()
        reg = EybondRegistry(now=clock)
        reg.record_seen("PN001", "10.0.0.1:1111")  # t0
        rec = reg.mark_disconnected("PN001")  # t1
        assert rec.status is DongleStatus.DISCONNECTED
        assert rec.last_seen == "t1"
        assert rec.first_seen == "t0"

    def test_mark_disconnected_unknown_is_noop(self):
        reg = EybondRegistry(now=_Clock())
        assert reg.mark_disconnected("ghost") is None


class TestConfiguration:
    def test_setters_create_record_when_absent(self):
        reg = EybondRegistry(now=_Clock())
        reg.set_enabled("PN001", True)
        rec = reg.get("PN001")
        assert rec is not None
        # Pre-configured before discovery: no timestamps, disconnected.
        assert rec.status is DongleStatus.DISCONNECTED
        assert rec.first_seen == ""
        assert rec.enabled is True

    def test_set_protocol_none_means_unconfigured(self):
        reg = EybondRegistry(now=_Clock())
        reg.set_protocol("PN001", "pi18")
        assert reg.get("PN001").protocol == "pi18"
        reg.set_protocol("PN001", None)
        assert reg.get("PN001").protocol is None

    def test_set_devaddr_and_name(self):
        reg = EybondRegistry(now=_Clock())
        reg.set_devaddr("PN001", 3)
        reg.set_name("PN001", "Garage inverter")
        rec = reg.get("PN001")
        assert rec.devaddr == 3
        assert rec.name == "Garage inverter"

    def test_remove(self):
        reg = EybondRegistry(now=_Clock())
        reg.record_seen("PN001")
        assert reg.remove("PN001") is not None
        assert "PN001" not in reg
        assert reg.remove("PN001") is None


class TestQueries:
    def _populate(self) -> EybondRegistry:
        reg = EybondRegistry(now=_Clock())
        reg.record_seen("PN_A", "10.0.0.1:1")      # connected
        reg.record_seen("PN_B", "10.0.0.2:2")      # connected
        reg.mark_disconnected("PN_B")              # now disconnected
        reg.set_enabled("PN_A", True)
        return reg

    def test_enabled_pns(self):
        reg = self._populate()
        assert reg.enabled_pns() == ["PN_A"]

    def test_connected_pns(self):
        reg = self._populate()
        assert reg.connected_pns() == ["PN_A"]

    def test_all_returns_every_record(self):
        reg = self._populate()
        assert {r.pn for r in reg.all()} == {"PN_A", "PN_B"}


class TestSerialization:
    def test_round_trip(self):
        clock = _Clock()
        reg = EybondRegistry(now=clock)
        reg.record_seen("PN_A", "10.0.0.1:1")
        reg.set_enabled("PN_A", True)
        reg.set_protocol("PN_A", "voltronic")
        reg.set_devaddr("PN_A", 2)
        reg.mark_disconnected("PN_A")

        data = reg.to_dict()
        # Status serialized as a plain string (JSON-friendly).
        assert data["PN_A"]["status"] == "disconnected"

        reg2 = EybondRegistry(now=_Clock())
        reg2.load(data)
        rec = reg2.get("PN_A")
        assert rec.enabled is True
        assert rec.protocol == "voltronic"
        assert rec.devaddr == 2
        assert rec.status is DongleStatus.DISCONNECTED

    def test_load_replaces_contents(self):
        reg = EybondRegistry(now=_Clock())
        reg.record_seen("OLD")
        reg.load({"NEW": {"pn": "NEW"}})
        assert "OLD" not in reg
        assert "NEW" in reg

    def test_load_none_clears(self):
        reg = EybondRegistry(now=_Clock())
        reg.record_seen("OLD")
        reg.load(None)
        assert len(reg) == 0

    def test_from_dict_tolerates_bad_status_and_unknown_keys(self):
        rec = DongleRecord.from_dict(
            {"pn": "X", "status": "bogus", "surprise": 1, "enabled": True}
        )
        assert rec.pn == "X"
        assert rec.status is DongleStatus.DISCONNECTED
        assert rec.enabled is True
