import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

# hass.data key holding the per-endpoint queue registry (dict[str, CommandQueue]).
QUEUES_KEY = "dess_monitor_local_queues"


class CommandQueue:
    """Асинхронная очередь команд к инвертору (Elfin / RS232)."""

    def __init__(self, min_delay: float = 0.3):
        self._queue = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._worker_task = None
        self.min_delay = min_delay

    async def start(self):
        if not self._worker_task:
            self._worker_task = asyncio.create_task(self._worker())

    async def stop(self):
        if self._worker_task:
            self._worker_task.cancel()
            # Await the cancellation so HA's event loop sees the task
            # finish gracefully — otherwise we get "Task was destroyed
            # but it is pending!" warnings on integration reload / HA
            # shutdown when the worker is mid-``await fn()`` and the
            # future under it gets cancelled.
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    async def enqueue(self, fn: Callable[[], Awaitable[Any]], desc: str = "") -> Any:
        """Добавить команду в очередь."""
        fut = asyncio.get_running_loop().create_future()
        await self._queue.put((fn, fut, desc))
        return await fut

    async def _worker(self):
        while True:
            fn, fut, desc = await self._queue.get()
            try:
                async with self._lock:
                    # if desc:
                    #     print(f"[QUEUE] → {desc}")
                    result = await fn()
                    if not fut.done():
                        fut.set_result(result)
            except Exception as e:
                if not fut.done():
                    fut.set_exception(e)
            finally:
                await asyncio.sleep(self.min_delay)
                self._queue.task_done()


def endpoint_key(device: str) -> str:
    """Stable key identifying the *physical endpoint* a command talks to.

    Commands to the same endpoint must not interleave (half-duplex RS485 bus /
    single socket); commands to different endpoints are independent and run on
    separate queues so they poll in parallel. A single global queue used to
    serialize every device against every other — this splits them apart.
    """
    if not device:
        return "?"
    # EyBond: one TCP listener fronts many dongles — serialize per dongle (PN),
    # not per listener, so different dongles don't block each other.
    if device.startswith("eybond"):
        try:
            pn = parse_qs(urlparse(device).query).get("pn", [""])[0]
        except Exception:
            pn = ""
        return f"eybond:{pn}" if pn else device
    # Network transports (tcp/modbus/pi18/agent): the socket is host:port.
    try:
        p = urlparse(device)
        if p.hostname and p.port:
            return f"{p.scheme}://{p.hostname}:{p.port}"
    except Exception:
        pass
    # Serial / raw path: the device string itself is the endpoint.
    return device


async def get_command_queue(hass, device: str) -> "CommandQueue":
    """Return the serialized queue for ``device``'s endpoint, creating it once.

    Lazily started and cached in ``hass.data[QUEUES_KEY]`` so the first command
    to a new endpoint spins up its worker and later ones reuse it.
    """
    registry: dict[str, CommandQueue] = hass.data.setdefault(QUEUES_KEY, {})
    key = endpoint_key(device)
    queue = registry.get(key)
    if queue is None:
        queue = CommandQueue(min_delay=0.3)
        await queue.start()
        registry[key] = queue
    return queue


async def stop_all_command_queues(hass) -> None:
    """Drain and forget every endpoint queue (integration teardown)."""
    registry: dict[str, CommandQueue] = hass.data.pop(QUEUES_KEY, {}) or {}
    for queue in registry.values():
        await queue.stop()
