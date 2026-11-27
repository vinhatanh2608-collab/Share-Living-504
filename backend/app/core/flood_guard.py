import asyncio

from starlette.responses import JSONResponse


class FloodGuard:
    def __init__(self, app, capacity, header_limit):
        self.app = app
        self.slots = asyncio.Semaphore(capacity)
        self.header_limit = header_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if self.slots.locked():
            response = JSONResponse({"detail": "Server busy"}, 503, {"Retry-After": "1"})
        elif sum(len(k) + len(v) + 4 for k, v in scope["headers"]) > self.header_limit:
            response = JSONResponse({"detail": "Request headers too large"}, 413)
        else:
            async with self.slots:
                return await self.app(scope, receive, send)
        await response(scope, receive, send)
