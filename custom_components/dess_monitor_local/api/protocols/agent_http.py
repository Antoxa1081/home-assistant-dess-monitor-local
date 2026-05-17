"""solar-system-agent HTTP snapshot transport.

URI: ``agent://<host>:<port>/<providerDeviceId>``

Targets the local HTTP API exposed by solar-system-agent::

    GET http://<host>:<port>/devices/<id>/latest
    POST http://<host>:<port>/devices/<id>/settings

The agent already decodes the raw Voltronic/Modbus response server-side
and caches it in memory; we just pull the pre-shaped flat dict and split
it into the QPIGS / QPIRI / QMOD buckets the rest of the integration
expects, so downstream sensors see identical inputs regardless of the
source transport.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import aiohttp

_LOGGER = logging.getLogger(__name__)


# Max acceptable reading age. The agent polls on a cloud-configured
# interval (typically 5–30 s); anything older than this means the agent
# itself is stuck or its upstream hardware is unreachable, and we'd
# rather surface "unavailable" in HA than show a stale value as live.
AGENT_STALE_THRESHOLD_MS = 5 * 60 * 1000


def parse_agent_uri(device: str) -> tuple[str, int, str]:
    """Decompose ``agent://host:port/providerDeviceId`` into its parts.

    Raises ``ValueError`` on malformed input — callers translate that
    into an empty dict so the coordinator handles it uniformly with
    other "cannot talk to device" outcomes.
    """
    parsed = urlparse(device)
    if parsed.scheme != "agent":
        raise ValueError(f"not an agent URI: {device}")
    if not parsed.hostname or not parsed.port:
        raise ValueError(f"agent URI needs host:port: {device}")
    provider_device_id = parsed.path.lstrip("/")
    if not provider_device_id:
        raise ValueError(f"agent URI missing providerDeviceId: {device}")
    return parsed.hostname, parsed.port, provider_device_id


async def fetch_agent_snapshot(
    host: str, port: int, provider_device_id: str, timeout: float
) -> dict | None:
    """GET the snapshot JSON from the agent. Returns ``None`` on any error.

    The agent API is read-only, local-network, no-auth by design. We
    don't retry here — the coordinator polls on its own schedule, and
    a transient failure surfaces as empty-dict for one tick.
    """
    url = f"http://{host}:{port}/devices/{provider_device_id}/latest"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as session:
            async with session.get(url) as resp:
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    _LOGGER.debug(
                        "agent returned HTTP %s for %s", resp.status, url
                    )
                    return None
                return await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        _LOGGER.debug("agent unreachable at %s: %s", url, err)
        return None


# Canonical names of QPIRI (config / rated settings) fields. The agent
# emits these alongside live QPIGS readings in a single flat dict — we
# use this set to route them to the qpiri bucket when no explicit
# ``qpiri.`` prefix is present. Keep in sync with the QPIRI_SENSOR_MAPPING
# keys in sensors/direct_sensor.py.
_QPIRI_FIELD_NAMES = frozenset({
    "rated_grid_voltage",
    "rated_input_current",
    "rated_ac_output_voltage",
    "rated_output_frequency",
    "rated_output_current",
    "rated_output_apparent_power",
    "rated_output_active_power",
    "rated_battery_voltage",
    "low_battery_to_ac_bypass_voltage",
    "shut_down_battery_voltage",
    "bulk_charging_voltage",
    "float_charging_voltage",
    "battery_type",
    "max_utility_charging_current",
    "max_charging_current",
    "ac_input_voltage_range",
    "output_source_priority",
    "charger_source_priority",
    "parallel_max_number",
    "parallel_mode",
    "high_battery_voltage_to_battery_mode",
    "solar_work_condition_in_parallel",
    "solar_max_charging_power_auto_adjust",
    "rated_battery_capacity",
})


def split_raw_by_command(
    raw: dict[str, str], command: str
) -> dict[str, str]:
    """Return the subset of ``raw`` that belongs to the requested command.

    The agent emits each command's payload either with a ``<cmd>.`` prefix
    (canonical) or as a flat dict (older builds / postgen pipelines). Both
    are handled — prefix takes precedence, falling back to name-based
    routing for QPIRI so the Number / Select control entities keep working
    against agents that don't prefix.
    """
    if command == "QPIRI":
        prefixed = {
            k[len("qpiri."):]: v
            for k, v in raw.items()
            if k.startswith("qpiri.")
        }
        if prefixed:
            return prefixed
        # Fallback: pick out the well-known QPIRI field names from the
        # flat dict. Anything not in the set falls through to QPIGS.
        return {
            k: v for k, v in raw.items()
            if k in _QPIRI_FIELD_NAMES
        }

    if command == "QPIGS":
        # Exclude prefixed keys *and* the QPIRI name-set, so QPIGS-only
        # sensors don't accidentally pick up config readings.
        qpigs = {
            k: v
            for k, v in raw.items()
            if not k.startswith(("qpiri.", "qmod.", "qpigs2."))
            and k not in _QPIRI_FIELD_NAMES
        }
        # Agent ships SMG-II-style signed ``battery_current`` (+ charging,
        # − discharging). The rest of the integration is Voltronic-shaped
        # and expects the two split fields, so derive them when the
        # agent didn't already provide them. Same with ``battery_power``
        # → infer it back if voltage is known and split currents are
        # absent. Idempotent: if split fields already exist, leave them.
        if "battery_current" in qpigs and (
            "battery_charging_current" not in qpigs
            or "battery_discharge_current" not in qpigs
        ):
            try:
                i_signed = float(qpigs["battery_current"])
            except (TypeError, ValueError):
                i_signed = 0.0
            qpigs.setdefault(
                "battery_charging_current",
                f"{max(0.0, i_signed):.2f}",
            )
            qpigs.setdefault(
                "battery_discharge_current",
                f"{max(0.0, -i_signed):.2f}",
            )

        # Synthesise PI30-style device_status_bits from the agent's
        # higher-level fields. SMG-II / Modbus inverters don't expose
        # those bit-strings at the protocol level, so binary_sensors
        # built on QPIGS bits would otherwise stay ``unknown``. We
        # derive them from operating_mode, current direction and PV
        # presence — enough to drive the Inverter On / Grid Lost /
        # Charging / AC-Charge / PV-Charge flags accurately.
        if "device_status_bits_b7_b0" not in qpigs:
            qpigs["device_status_bits_b7_b0"] = _synth_b7_b0(qpigs, raw)
        if "device_status_bits_b10_b8" not in qpigs:
            qpigs["device_status_bits_b10_b8"] = _synth_b10_b8(qpigs, raw)
        return qpigs

    for token, prefix in (
        ("QMOD", "qmod."),
        ("QPIGS2", "qpigs2."),
    ):
        if command == token:
            return {
                k[len(prefix):]: v
                for k, v in raw.items()
                if k.startswith(prefix)
            }
    return {}


def _safe_float(d: dict, key: str) -> float:
    try:
        return float(d.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _synth_b7_b0(qpigs: dict, raw: dict) -> str:
    """Reconstruct PI30 ``device_status_bits_b7_b0`` from agent fields.

    Bit positions (MSB→LSB): fault, reserved, bus_over, line_fail,
    battery_low, battery_high, overload, inverter_on. We can't observe
    every fault from the agent feed — only the ones derivable from
    operating_mode, grid voltage and overload status are populated.
    Unknown bits stay 0; the integration's plausibility-bounds and
    fault-summary sensors handle the rest via QPIWS/QFWS.
    """
    operating_mode = (raw.get("operating_mode") or raw.get("qmod.operating_mode") or "").lower()
    grid_voltage = _safe_float(raw, "grid_voltage")
    load_percent = _safe_float(raw, "load_percent")
    # Inverter is "on" whenever it's not in PowerOn boot / Shutdown phase.
    # Agent's operating_mode "Mains" / "OffGrid" / "Battery" all imply
    # the inverter is actively converting.
    inverter_on = operating_mode in ("mains", "offgrid", "battery", "line", "bypass")
    # Grid considered lost when AC input voltage is below ~50 V — same
    # heuristic the discharge-synthesis code uses elsewhere.
    line_fail = grid_voltage < 50.0
    # Overload: load_percent > 100 (some firmware reports 110+ during
    # surge); fault states usually also push it here.
    overload = load_percent > 100.0
    fault = operating_mode == "fault"
    bits = [
        "1" if fault else "0",         # b7
        "0",                           # b6 reserved
        "0",                           # b5 bus_over — not derivable
        "1" if line_fail else "0",     # b4
        "0",                           # b3 battery_low — comes from QPIWS
        "0",                           # b2 battery_high — comes from QPIWS
        "1" if overload else "0",      # b1
        "1" if inverter_on else "0",   # b0
    ]
    return "".join(bits)


def _synth_b10_b8(qpigs: dict, raw: dict) -> str:
    """Reconstruct PI30 ``device_status_bits_b10_b8`` from agent fields.

    Bit positions (MSB→LSB): charging_to_battery, ac_charging_active,
    scc_charging_active.
    """
    try:
        i_signed = float(qpigs.get("battery_current", 0) or 0)
    except (TypeError, ValueError):
        i_signed = 0.0
    charging_to_battery = i_signed > 0.1
    # AC-side charging only when grid is up AND battery is charging.
    grid_voltage = _safe_float(raw, "grid_voltage")
    ac_charging = charging_to_battery and grid_voltage > 50.0
    # SCC (PV) charging when PV is producing significantly.
    pv_power = _safe_float(raw, "pv_charging_power") or _safe_float(raw, "pv_input_power")
    scc_charging = pv_power > 5.0
    bits = [
        "1" if charging_to_battery else "0",  # b10
        "1" if ac_charging else "0",          # b9
        "1" if scc_charging else "0",         # b8
    ]
    return "".join(bits)


async def post_agent_setting(
    device: str, setting_key: str, value, timeout: float = 30.0
) -> dict:
    """POST a semantic setting (key/value) to the agent.

    The agent translates ``(setting_key, value)`` into the proper wire
    format for the device's transport (Voltronic POP/PCP/PBAVxx.xx,
    Modbus register write, etc). Returns a dict of the agent's JSON
    response::

        {"ok": True, "rawResponse": "ACK", "durationMs": 420}
        {"ok": False, "error": "...", "code": "validation" | "device_nak" | ...}
    """
    try:
        host, port, provider_device_id = parse_agent_uri(device)
    except ValueError as err:
        return {"ok": False, "error": f"invalid agent URI: {err}"}

    url = f"http://{host}:{port}/devices/{provider_device_id}/settings"
    payload = {"key": setting_key, "value": value}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json(content_type=None)
                if not isinstance(data, dict):
                    return {
                        "ok": False,
                        "error": f"agent returned non-dict body (HTTP {resp.status})",
                    }
                return data
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        return {"ok": False, "error": f"agent unreachable: {err}"}
