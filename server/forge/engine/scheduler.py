from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Callable


class Scheduler:
    def __init__(self, max_concurrent: int):
        self._sem = asyncio.Semaphore(max_concurrent)

    @asynccontextmanager
    async def slot(self, on_queued: Callable[[], None]):
        if self._sem.locked():
            on_queued()
        async with self._sem:
            yield
