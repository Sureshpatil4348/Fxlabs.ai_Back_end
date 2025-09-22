import asyncio
from contextlib import asynccontextmanager
from typing import Dict


class ConcurrencyManager:
    """Keyed asyncio lock manager to cap concurrency per resource key.

    Use a stable key format, e.g., f"{symbol}:{timeframe}".
    """

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}
        self._map_lock = asyncio.Lock()

    async def get_lock(self, key: str) -> asyncio.Lock:
        async with self._map_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    @asynccontextmanager
    async def acquire(self, key: str):
        lock = await self.get_lock(key)
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()


# Global instance for pair×timeframe concurrency capping across services
pair_locks = ConcurrencyManager()

