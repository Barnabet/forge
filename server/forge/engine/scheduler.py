from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Callable


class Scheduler:
    """Bounds concurrent run slots. The cap is adjustable at runtime so a
    config change (max_concurrent) takes effect without a restart: raising it
    wakes queued waiters immediately; lowering it lets in-flight slots drain
    naturally before new ones are admitted."""

    def __init__(self, max_concurrent: int):
        self._max = max(1, max_concurrent)
        self._active = 0
        self._cond = asyncio.Condition()

    async def set_max_concurrent(self, max_concurrent: int) -> None:
        async with self._cond:
            self._max = max(1, max_concurrent)
            self._cond.notify_all()  # wake waiters to take any freed slots

    @asynccontextmanager
    async def slot(self, on_queued: Callable[[], None]):
        async with self._cond:
            if self._active >= self._max:
                on_queued()
                await self._cond.wait_for(lambda: self._active < self._max)
            self._active += 1
        try:
            yield
        finally:
            async with self._cond:
                self._active -= 1
                self._cond.notify_all()
