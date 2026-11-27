import asyncio
from time import monotonic


class CircuitBreaker:
    """One event loop owns the breaker and the admission semaphore."""

    def __init__(self, failures=3, cooldown=30, concurrency=4, timeout=12):
        self.threshold = failures
        self.cooldown = cooldown
        self.timeout = timeout
        self.slots = asyncio.Semaphore(concurrency)
        self.failures = 0
        self.state = "CLOSED"
        self.opened = 0
        self.generation = 0

    def trip(self):
        self.state = "OPEN"
        self.opened = monotonic()
        self.generation += 1

    async def call(self, operation, *args):
        if self.slots.locked() or self.state == "HALF_OPEN":
            return None
        if self.state == "OPEN":
            if monotonic() - self.opened < self.cooldown:
                return None
            self.state = "HALF_OPEN"
        generation = self.generation
        async with self.slots:
            try:
                async with asyncio.timeout(self.timeout):
                    result = await operation(*args)
            except asyncio.CancelledError:
                if self.state == "HALF_OPEN" and generation == self.generation:
                    self.trip()
                raise
            except Exception:
                if generation == self.generation:
                    self.failures += 1
                    if self.state == "HALF_OPEN" or self.failures >= self.threshold:
                        self.trip()
                raise
            if generation == self.generation:
                self.failures = 0
                self.state = "CLOSED"
            return result
