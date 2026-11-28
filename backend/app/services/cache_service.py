"""Optional Redis acceleration. SQL and authentication remain authoritative."""
import json
import logging
from hashlib import sha256
from time import monotonic, time
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)
COUNTER = """
local hits = tonumber(redis.call('GET', KEYS[1]) or '0')
if hits < tonumber(ARGV[1]) then
    hits = redis.call('INCR', KEYS[1])
    if hits == 1 then redis.call('EXPIRE', KEYS[1], ARGV[2]) end
    return {hits, 0, redis.call('TTL', KEYS[1])}
end
local ttl = redis.call('TTL', KEYS[1])
return {hits, math.max(1, ttl), ttl}
"""


class CacheService:
    def __init__(self, url, namespace="shared-living:v1"):
        self.client = None
        if url:
            try:
                self.client = Redis.from_url(url, socket_timeout=.25, socket_connect_timeout=.25,
                                             max_connections=20)
            except ValueError:
                logger.warning("Invalid Redis URL. Using primary SQL and local rate limits.")
        self.namespace = namespace
        self.retry_at = 0
        self.dirty = False

    async def call(self, command, *args):
        if self.client is None or monotonic() < self.retry_at:
            return None
        try:
            return await getattr(self.client, command)(*args)
        except (RedisError, OSError, TimeoutError):
            logger.warning("Redis unavailable. Using primary SQL and local rate limits.")
            self.retry_at = monotonic() + 3
            return None

    async def invalidate(self):
        if self.client is None:
            return
        self.dirty = True
        if await self.call("set", self.namespace + ":generation", uuid4().hex):
            self.dirty = False

    async def get_or_load(self, parts, loader, ttl=20):
        if self.client is None:
            return await loader()
        if self.dirty:
            await self.invalidate()
        if self.dirty:
            return await loader()
        generation = await self.call("get", self.namespace + ":generation")
        if generation is None:
            # A missing generation may be an empty cache or an outage.
            if not await self.call("setnx", self.namespace + ":generation", uuid4().hex):
                return await loader()
            generation = await self.call("get", self.namespace + ":generation")
        if generation is None:
            return await loader()
        digest = sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
        key = f"{self.namespace}:cache:{generation.decode()}:{digest}"
        cached = await self.call("get", key)
        if cached is not None:
            return json.loads(cached)
        result = await loader()
        await self.call("setex", key, ttl, json.dumps(jsonable_encoder(result), ensure_ascii=False))
        return result

    async def rate(self, name, subject, limit, window):
        digest = sha256(repr(subject).encode()).hexdigest()
        result = await self.call("eval", COUNTER, 1,
                                 f"{self.namespace}:rate:{name}:{window}:{digest}", limit, window)
        if result is None:
            return None
        hits, wait, ttl = result
        # An exhausted key can have less than one second remaining.
        return (max(1, wait) if wait > 0 else 0), {
            "X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": str(max(0, limit-hits)),
            "X-RateLimit-Reset": str(int(time()) + max(1, ttl)),
        }

    async def close(self):
        if self.client is not None:
            await self.client.aclose()


async def cached(request, parts, loader, ttl=20):
    service = getattr(request.app.state, "cache", None)
    return await service.get_or_load(parts, loader, ttl) if service else await loader()
