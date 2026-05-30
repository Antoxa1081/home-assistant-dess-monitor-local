from __future__ import annotations

import logging
from typing import Any

from ..decoders.enums import (
    ChargeSourcePrioritySetting,
    OperatingMode,
    OutputSourcePrioritySetting,
)
from ..decoders.voltronic import voltronic_to_snapshot
from ..model import DeviceSnapshot, WarningKey
from ..protocols.agent_http import (
    AGENT_STALE_THRESHOLD_MS,
    fetch_agent_snapshot,
    parse_agent_uri,
    post_agent_setting,
    split_raw_by_command,
)
from .base import BaseAdapter

_LOGGER = logging.getLogger(__name__)

# Translation from the agent's free-text operating_mode strings to canonical PI30 codes.
_AGENT_MODE_TO_PI30: dict = {
    "Mains":     OperatingMode.Line,
    "Line":      OperatingMode.Line,
    "Bypass":    OperatingMode.Line,
    "OffGrid":   OperatingMode.Battery,
    "Battery":   OperatingMode.Battery,
    "PowerOn":   OperatingMode.PowerOn,
    "Standby":   OperatingMode.Standby,
    "Fault":     OperatingMode.Fault,
    "Shutdown":  OperatingMode.ShutdownApproaching,
}

_OUTPUT_PRIORITY_TO_AGENT: dict = {
    OutputSourcePrioritySetting.UTILITY_FIRST: "UtilityFirst",
    OutputSourcePrioritySetting.SBU_PRIORITY: "SBU",
    OutputSourcePrioritySetting.SOLAR_FIRST: "SolarFirst",
}

_CHARGER_PRIORITY_TO_AGENT: dict = {
    ChargeSourcePrioritySetting.UTILITY_FIRST: "UtilityFirst",
    ChargeSourcePrioritySetting.SOLAR_FIRST: "SolarFirst",
    ChargeSourcePrioritySetting.SOLAR_AND_UTILITY: "SolarAndUtility",
}

def agent_to_snapshot(sections: dict) -> DeviceSnapshot:
    """Map the agent's Voltronic-shaped sections onto the domain model.

    The agent is faithful (no fabrication), so the shared mapping applies;
    its faults live in the ``qfws`` section (``warn_*`` flags + numeric codes),
    which is merged in on top of the base projection.
    """
    snap = voltronic_to_snapshot(sections)
    qfws = sections.get("qfws") or {}
    if qfws:
        snap.faults.warnings |= WarningKey.from_flags(qfws)
        snap.faults.fault_code = qfws.get("fault_code")
        snap.faults.warning_code = qfws.get("warning_code")
        snap.faults.fault_description = qfws.get("fault_description")
    snap.raw = dict(sections)
    return snap


class AgentAdapter(BaseAdapter):
    """Adapter for solar-system-agent HTTP API."""

    async def get_snapshot(self) -> DeviceSnapshot:
        """Fetch the agent snapshot sections and assemble the domain model."""
        sections = {
            "qpigs": await self.get_data("QPIGS"),
            "qpiri": await self.get_data("QPIRI"),
            "qmod": await self.get_data("QMOD"),
            "qpiws": await self.get_data("QPIWS"),
            "qpigs2": await self.get_data("QPIGS2"),
            "qfws": await self.get_data("QFWS"),
        }
        return agent_to_snapshot(sections)

    async def get_data(self, command: str) -> dict:
        try:
            host, port, provider_device_id = parse_agent_uri(self.uri)
        except ValueError as err:
            _LOGGER.warning("invalid agent URI %s: %s", self.uri, err)
            return {}

        payload = await fetch_agent_snapshot(host, port, provider_device_id, self.timeout)
        if not payload:
            return {}

        age_ms = payload.get("ageMs")
        if isinstance(age_ms, (int, float)) and age_ms > AGENT_STALE_THRESHOLD_MS:
            _LOGGER.debug("agent snapshot for %s is stale (%sms) — dropping", provider_device_id, age_ms)
            return {}

        raw = payload.get("raw") or {}
        if not isinstance(raw, dict):
            return {}

        if command == "QMOD":
            sub = split_raw_by_command(raw, "QMOD")
            mode_name = sub.get("operating_mode")
            if mode_name is None:
                return {}
            mapped = _AGENT_MODE_TO_PI30.get(mode_name)
            if mapped is not None:
                return {"operating_mode": mapped}
            try:
                return {"operating_mode": OperatingMode[mode_name]}
            except KeyError:
                return {}

        return split_raw_by_command(raw, command)

    async def set_data(self, command: str) -> dict:
        return {"error": "raw set_data is not supported for Agent; use semantic setters or set_setting"}

    async def set_setting(self, key: str, value: Any) -> dict:
        return await post_agent_setting(self.uri, key, value, self.timeout)

    async def set_output_source_priority(self, mode: OutputSourcePrioritySetting) -> dict:
        agent_value = _OUTPUT_PRIORITY_TO_AGENT.get(mode)
        if agent_value is None:
            return {"ok": False, "error": f"no agent mapping for {mode}"}
        return await self.set_setting("output_source_priority", agent_value)

    async def set_charge_source_priority(self, mode: ChargeSourcePrioritySetting) -> dict:
        agent_value = _CHARGER_PRIORITY_TO_AGENT.get(mode)
        if agent_value is None:
            return {"ok": False, "error": f"no agent mapping for {mode}"}
        return await self.set_setting("charger_source_priority", agent_value)

    async def set_battery_bulk_voltage(self, voltage: float) -> dict:
        return await self.set_setting("bulk_charging_voltage", float(voltage))

    async def set_battery_float_voltage(self, voltage: float) -> dict:
        return await self.set_setting("float_charging_voltage", float(voltage))

    async def set_max_combined_charge_current(self, amps: int) -> dict:
        return await self.set_setting("max_charging_current", int(amps))

    async def set_battery_charge_current(self, amps: int) -> dict:
        return await self.set_setting("max_charging_current", int(amps))

    async def set_max_utility_charge_current(self, amps: int, float_format: bool = False) -> dict:
        return await self.set_setting("max_utility_charging_current", int(amps))
