"""Pure, Home-Assistant-free resilience policy for the coordinator.

Extracted from ``DirectCoordinator`` so the retry/freeze/unavailable
decision — the logic behind the "dashboard strobe" fix — can be unit
tested without an event loop or HA.

Behaviour (per ``(device, command)``):
  * a successful read resets the consecutive-failure counter;
  * each fully-failed read (after the transport-level retry) increments it;
  * while the counter is below ``max_consecutive`` the last known good
    value is reused ("freeze"), so a single bad poll doesn't flap the
    entity to unavailable;
  * once it reaches ``max_consecutive`` the section is dropped (``{}``),
    letting HA mark the entities unavailable.

The async retry loop itself stays in the coordinator; this module owns
only the counting and the freeze-vs-drop verdict.
"""
from __future__ import annotations

from enum import Enum

DEFAULT_MAX_CONSECUTIVE_FAILURES = 3


class FailureOutcome(Enum):
    FREEZE = "freeze"          # reuse last known good section
    NO_DATA = "no_data"        # below threshold but nothing to freeze
    UNAVAILABLE = "unavailable"  # threshold hit -> drop, go unavailable


class FailureTracker:
    """Per-(device, command) consecutive-failure counter + verdict."""

    def __init__(self, max_consecutive: int = DEFAULT_MAX_CONSECUTIVE_FAILURES):
        self.max_consecutive = max_consecutive
        self._counts: dict[str, dict[str, int]] = {}

    def count(self, device: str, command: str) -> int:
        return self._counts.get(device, {}).get(command, 0)

    def on_success(self, device: str, command: str) -> None:
        self._counts.setdefault(device, {})[command] = 0

    def on_failure(self, device: str, command: str) -> int:
        """Increment and return the new consecutive-failure count."""
        per_device = self._counts.setdefault(device, {})
        per_device[command] = per_device.get(command, 0) + 1
        return per_device[command]

    def resolve(self, count: int, last_known: dict | None) -> tuple[dict, FailureOutcome]:
        """Decide what data to surface after a failed read.

        Args:
            count: the consecutive-failure count (from :meth:`on_failure`).
            last_known: the previous good section for this command, if any.

        Returns:
            ``(data, outcome)`` — the dict to publish plus the verdict
            enum (useful for logging at the call site).
        """
        if count < self.max_consecutive:
            if last_known:
                return last_known, FailureOutcome.FREEZE
            return {}, FailureOutcome.NO_DATA
        return {}, FailureOutcome.UNAVAILABLE
