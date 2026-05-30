"""Tests for the canonical WarningKey union and flag canonicalisation
(api/model.py). Pure — no Home Assistant.

Group 3 of the domain-model refactor makes WarningKey the union of every
warning any adapter can report, and teaches ``from_flags`` to canonicalise
the PI18 native spellings that differ from the PI30/agent canon."""
from custom_components.dess_monitor_local.api.model import WarningKey


class TestWarningKeyUnion:
    def test_pi30_bare_names_map(self):
        keys = WarningKey.from_flags({"overload": True, "fan_locked": True})
        assert keys == {WarningKey.OVERLOAD, WarningKey.FAN_LOCKED}

    def test_inactive_flags_ignored(self):
        assert WarningKey.from_flags({"overload": False, "bus_over": 0}) == set()

    def test_agent_warn_prefixed_names_map(self):
        # Agent extras already use the canonical spelling under warn_.
        keys = WarningKey.from_flags(
            {"warn_pv_over_temperature": True, "warn_parallel_host_lost": True}
        )
        assert keys == {
            WarningKey.PV_OVER_TEMPERATURE,
            WarningKey.PARALLEL_HOST_LOST,
        }

    def test_unknown_keys_ignored(self):
        assert WarningKey.from_flags({"warn_made_up_flag": True}) == set()

    def test_reserved_and_meta_keys_ignored(self):
        # The decode dicts also carry non-flag entries; they must not map.
        flags = {"_reserved_0": True, "fault_code": 5, "has_fault": True}
        assert WarningKey.from_flags(flags) == set()


class TestPi18Aliases:
    """PI18 QFWS native spellings canonicalise onto the union members."""

    def test_spelling_variants(self):
        flags = {
            "warn_fan_lock": True,            # -> fan_locked
            "warn_eeprom_fail": True,         # -> eeprom_fault
            "warn_output_short": True,        # -> opv_short
            "warn_battery_low": True,         # -> battery_low_alarm
            "warn_battery_under": True,       # -> battery_under_shutdown
        }
        assert WarningKey.from_flags(flags) == {
            WarningKey.FAN_LOCKED,
            WarningKey.EEPROM_FAULT,
            WarningKey.OPV_SHORT,
            WarningKey.BATTERY_LOW_ALARM,
            WarningKey.BATTERY_UNDER_SHUTDOWN,
        }

    def test_split_warnings_merge_to_one_canonical(self):
        # PI18 splits per-MPPT / per-SCC; the canon keeps one each.
        assert WarningKey.from_flags(
            {"warn_mppt1_overload": True, "warn_mppt2_overload": True}
        ) == {WarningKey.MPPT_OVERLOAD_FAULT}
        assert WarningKey.from_flags(
            {"warn_battery_too_low_scc1": True, "warn_battery_too_low_scc2": True}
        ) == {WarningKey.BATTERY_TOO_LOW_TO_CHARGE}
        assert WarningKey.from_flags(
            {"warn_pv1_voltage_high": True, "warn_pv2_voltage_high": True}
        ) == {WarningKey.PV_VOLTAGE_HIGH}

    def test_full_pi18_qfws_set_all_recognised(self):
        # Every PI18 QFWS warning must map to some canonical member — none
        # may be silently dropped (the bug this group fixes).
        from custom_components.dess_monitor_local.api.decoders.pi18 import (
            _FWS_WARNING_FIELDS,
        )
        flags = {name: True for name in _FWS_WARNING_FIELDS}
        keys = WarningKey.from_flags(flags)
        # 16 PI18 warnings collapse to 13 canonical members (3 merges).
        assert len(keys) == 13
        assert WarningKey.FAN_LOCKED in keys
        assert WarningKey.LINE_FAIL in keys


class TestSeverityTableCoverage:
    def test_every_severity_base_name_is_a_warning_key(self):
        # The display/severity table and the enum must stay in lock-step so
        # the fault-summary severity walk can look every entry up by value.
        from custom_components.dess_monitor_local.sensors.direct_sensor import (
            _WARNING_SEVERITY_ORDER,
        )
        values = {k.value for k in WarningKey}
        missing = [
            base for base, _ in _WARNING_SEVERITY_ORDER if base not in values
        ]
        assert missing == []
