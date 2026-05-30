from __future__ import annotations

import logging

from ...const import PROTOCOL_PI18
from ..decoders.pi18 import decode_pi18_response, pi18_to_snapshot
from ..decoders.voltronic import decode_direct_response, voltronic_to_snapshot
from ..model import DeviceSnapshot
from ..protocols.eybond_dongle import send_eybond_set_command, send_eybond_voltronic
from .base import BaseAdapter

_LOGGER = logging.getLogger(__name__)

class EyBondAdapter(BaseAdapter):
    """Adapter for EyBond dongle, supporting PI30 and PI18."""

    def __init__(self, uri: str, timeout: float = 30.0, strict_crc: bool = False):
        super().__init__(uri, timeout, strict_crc)
        self.is_pi18 = uri.startswith("eybond-pi18://")
        self.protocol = PROTOCOL_PI18 if self.is_pi18 else None

    async def get_snapshot(self) -> DeviceSnapshot:
        """Fetch the command set over the dongle and assemble the model,
        using the PI18 or PI30 projection per the URI scheme."""
        if self.is_pi18:
            sections = {
                "qpigs": await self.get_data("QPIGS"),
                "qpiri": await self.get_data("QPIRI"),
                "qmod": await self.get_data("QMOD"),
                "qfws": await self.get_data("QFWS"),
            }
        else:
            sections = {
                "qpigs": await self.get_data("QPIGS"),
                "qpiri": await self.get_data("QPIRI"),
                "qmod": await self.get_data("QMOD"),
                "qpiws": await self.get_data("QPIWS"),
                "qpigs2": await self.get_data("QPIGS2"),
            }
        return self.snapshot_from_sections(sections)

    def snapshot_from_sections(self, sections: dict) -> DeviceSnapshot:
        if self.is_pi18:
            return pi18_to_snapshot(sections)
        return voltronic_to_snapshot(sections)

    async def get_data(self, command: str) -> dict:
        response = await send_eybond_voltronic(
            self.uri, command, self.timeout, protocol=self.protocol
        )
        if not response:
            return {}

        try:
            if self.is_pi18:
                return decode_pi18_response(command, response) or {}

            # For PI30, decode to ASCII first
            body, _, _ = response.partition(b"\r")
            ascii_resp = body.decode("ascii", errors="ignore")
            return decode_direct_response(command, ascii_resp) or {}
        except Exception as err:
            _LOGGER.debug("EyBondAdapter decode failed: %s", err)
            return {}

    async def set_data(self, command: str) -> dict:
        return await send_eybond_set_command(
            self.uri, command, self.timeout, protocol=self.protocol
        )
