import asyncio
from typing import Callable, Awaitable, Any


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