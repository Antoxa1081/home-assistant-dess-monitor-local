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


def split_raw_by_command(
    raw: dict[str, str], command: str
) -> dict[str, str]:
    """Return the subset of ``raw`` that belongs to the requested command.

    QPIGS keys are stored unprefixed; QPIRI keys carry a ``qpiri.``
    prefix; QMOD keys carry ``qmod.``; QPIGS2 keys carry ``qpigs2.``.
    """
    if command == "QPIGS":
        return {
            k: v
            for k, v in raw.items()
            if not k.startswith(("qpiri.", "qmod.", "qpigs2."))
        }
    for token, prefix in (
        ("QPIRI", "qpiri."),
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
