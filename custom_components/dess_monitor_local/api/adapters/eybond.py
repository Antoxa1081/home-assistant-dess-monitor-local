from __future__ import annotations

import logging

from ...const import PROTOCOL_PI18
from ..decoders.pi18 import decode_pi18_response
from ..decoders.voltronic import decode_direct_response
from ..protocols.eybond_dongle import send_eybond_set_command, send_eybond_voltronic
from .base import BaseAdapter

_LOGGER = logging.getLogger(__name__)

class EyBondAdapter(BaseAdapter):
    """Adapter for EyBond dongle, supporting PI30 and PI18."""

    def __init__(self, uri: str, timeout: float = 30.0, strict_crc: bool = False):
        super().__init__(uri, timeout, strict_crc)
        self.is_pi18 = uri.startswith("eybond-pi18://")
        self.protocol = PROTOCOL_PI18 if self.is_pi18 else None

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
