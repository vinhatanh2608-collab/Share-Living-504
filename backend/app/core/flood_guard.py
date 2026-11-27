import asyncio
import logging

from starlette.responses import JSONResponse
from .request_security import rejection_cors_headers


logger = logging.getLogger(__name__)


class FloodGuard:
    def __init__(self, app, capacity, header_limit, cors_origins=()):
        self.app = app
        self.slots = asyncio.Semaphore(capacity)
        self.header_limit = header_limit
        self.cors_origins = cors_origins

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
        logger.warning("Admission rejected: %s %s status=%d", scope.get("method"), scope.get("path"),
                       response.status_code)
        response.headers.update(rejection_cors_headers(scope, self.cors_origins))
        await response(scope, receive, send)
