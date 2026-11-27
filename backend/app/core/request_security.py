import asyncio
import logging
from ipaddress import ip_network
from .rate_limit import LoginCooldown, RateLimiter, identity, resolve_proxy
from math import ceil
from re import fullmatch
from time import monotonic, time

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

JSON_LIMIT = 1_000_000
RECEIPT_LIMIT = 5_000_000
MULTIPART_OVERHEAD = 65_536
RECEIPT_PATH = r"/(?:groups/[^/]+/proofs(?:/upload)?|finance/(?:debts/[^/]+/proof(?:-upload|s)|proof-batches(?:/upload)?))"


def is_receipt_path(path):
    return bool(fullmatch(RECEIPT_PATH, path.removeprefix("/api/v1").rstrip("/")))


def rejection_cors_headers(scope, origins):
    origin = Headers(scope=scope).get("origin")
    if origin in origins:
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Expose-Headers": "Retry-After"}
    return {}


class RequestSecurity:
    def __init__(self, app, config):
        self.app = app
        self.config = config
        self.limiter = RateLimiter(config.rate_limit_max_keys)
        self.networks = [ip_network(net) for net in config.trusted_proxy_ips] if config.trust_proxy else []
        self.slots = asyncio.Semaphore(10)
        self.connections = {}
        self.failures = LoginCooldown(config.rate_limit_max_keys)

    def retry_after(self, scope, receipt):
        now = monotonic()
        host = (scope.get("client") or ("unknown",))[0]
        path = scope["path"].removeprefix("/api/v1").rstrip("/")
        rules = [("api", self.config.api_requests_per_minute, 60)]
        if scope["method"] == "POST":
            if path in {"/auth/login", "/auth/register"}:
                rules.append((path, self.config.auth_requests_per_minute, 60))
            elif path == "/ai-chat/message":
                rules.extend((("advisor", self.config.ai_requests_per_minute, 60),
                              ("advisor", self.config.ai_requests_per_hour, 3600)))
            elif receipt:
                rules.append(("receipt", self.config.receipt_requests_per_minute, 60))
        for name, limit, window in rules:
            subject = identity(scope) if name in {"api", "advisor", "receipt"} else host
            wait, headers = self.limiter.check((name, window, subject), limit, window, now)
            if wait:
                return wait, headers
        return 0, headers

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        scope = resolve_proxy(scope, self.networks)
        production = self.config.environment == "production"
        # Only the ASGI server may promote the scheme after validating the proxy peer.
        secure = scope.get("scheme") in {"https", "wss"}
        if kind == "websocket":
            if (production or self.config.force_https) and not secure:
                logger.warning("WebSocket upgrade rejected: HTTPS required")
                return await send({"type": "websocket.close", "code": 1008})
            ip = (scope.get("client") or ("unknown",))[0]
            if self.connections.get(ip, 0) >= 5:
                logger.warning("WebSocket upgrade rejected: IP connection limit")
                return await send({"type": "websocket.close", "code": 1008})
            self.connections[ip] = self.connections.get(ip, 0) + 1
            try:
                return await self.app(scope, receive, send)
            finally:
                self.connections[ip] -= 1
                if not self.connections[ip]:
                    del self.connections[ip]

        host = (scope.get("client") or ("unknown",))[0]
        login = scope["method"] == "POST" and scope["path"].removeprefix("/api/v1").rstrip("/") == "/auth/login"
        rate_headers = {}
        async def secured_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if login and message["status"] in {200, 401}:
                    self.failures.record(host, message["status"], monotonic())
                headers.update(rate_headers)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin" if production else "no-referrer"
                if production and secure:
                    headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
            await send(message)

        async def reject(status, detail, headers=None):
            logger.warning("Request rejected: %s %s status=%d reason=%s", scope["method"], scope["path"], status, detail)
            response_headers = {**(headers or {}), **rejection_cors_headers(scope, self.config.cors_origins)}
            await JSONResponse({"detail": detail}, status_code=status, headers=response_headers)(scope, receive, secured_send)

        if (production or self.config.force_https) and not secure:
            return await reject(400, "HTTPS is required")
        path = scope["path"].removeprefix("/api/v1").rstrip("/")
        receipt = is_receipt_path(path)
        if path not in {"/health/live", "/health/ready"} and scope["method"] != "OPTIONS":
            retry, rate_headers = self.retry_after(scope, receipt)
            if login:
                cooldown = self.failures.remaining(host, monotonic())
                if cooldown > retry:
                    retry = cooldown
                    rate_headers = {"X-RateLimit-Limit": str(self.config.auth_requests_per_minute), "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(ceil(time() + retry))}
            if retry:
                return await reject(429, "Too many requests. Try again shortly.", {"Retry-After": str(retry)})
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            headers = Headers(scope=scope)
            media = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            raw_receipt = receipt and (media.startswith("image/") or media == "multipart/form-data")
            limit = RECEIPT_LIMIT if raw_receipt else JSON_LIMIT
            if receipt and media == "multipart/form-data":
                limit += MULTIPART_OVERHEAD
            lengths = headers.getlist("content-length")
            if lengths:
                if len(lengths) != 1 or not fullmatch(r"[0-9]{1,20}", lengths[0]):
                    return await reject(400, "Invalid Content-Length")
                if int(lengths[0]) > limit:
                    return await reject(413, "Request body is too large")
            if headers.get("content-encoding", "identity").lower() != "identity":
                return await reject(415, "Unsupported request encoding")
            buffer = bytearray()
            received = 0
            try:
                async with asyncio.timeout(15):
                    while True:
                        chunk = await receive()
                        if chunk["type"] == "http.disconnect":
                            return
                        data = chunk.get("body", b"")
                        received += len(data)
                        if received > limit:
                            return await reject(413, "Request body is too large")
                        if not raw_receipt:
                            buffer.extend(data)
                        if not chunk.get("more_body"):
                            break
            except TimeoutError:
                return await reject(408, "Request body timed out")
            if raw_receipt:
                return await reject(400, "Use a signed upload URL for receipt bytes")
            body = bytes(buffer)
            del buffer

            async def replay():
                nonlocal body
                if body is not None:
                    result, body = body, None
                    return {"type": "http.request", "body": result, "more_body": False}
                return await receive()

            if receipt or path == "/ai-chat/message":
                if self.slots.locked():
                    return await reject(503, "Service temporarily busy", {"Retry-After": "1"})
                async with self.slots:
                    return await self.app(scope, replay, secured_send)
            return await self.app(scope, replay, secured_send)
        return await self.app(scope, receive, secured_send)
