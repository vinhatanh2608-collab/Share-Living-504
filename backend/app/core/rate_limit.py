from collections import OrderedDict, deque
from ipaddress import ip_address
from math import ceil
from threading import Lock
from time import time

import jwt
from starlette.datastructures import Headers
from .security import claims


class RateLimiter:
    def __init__(self, capacity=50000):
        self.capacity = capacity
        self.entries = OrderedDict()
        self.lock = Lock()

    def check(self, key, limit, window, now):
        with self.lock:
            hits = self.entries.pop(key, deque())
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(self.entries) >= self.capacity:
                self.entries.popitem(last=False)
            self.entries[key] = hits
            wait = max(1, ceil(hits[0] + window - now)) if len(hits) >= limit else 0
            if not wait:
                hits.append(now)
            reset = ceil(time() + hits[0] + window - now)
            headers = {"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": str(limit-len(hits)),
                       "X-RateLimit-Reset": str(reset)}
            return wait, headers


def identity(scope):
    header = Headers(scope=scope).get("authorization", "")
    if header.lower().startswith("bearer ") and len(header) < 4096:
        try:
            return ("user", int(claims(header[7:])["sub"]))
        except (jwt.InvalidTokenError, ValueError, KeyError, TypeError):
            pass
    return ("ip", scope["client"][0])


def resolve_proxy(scope, networks):
    host = (scope.get("client") or ("unknown", 0))[0]
    try:
        peer = ip_address(host)
    except ValueError:
        return scope
    if not any(peer in net for net in networks):
        return scope
    headers = Headers(scope=scope)
    chain = headers.get("x-forwarded-for") or headers.get("cf-connecting-ip", "")
    try:
        for hop in reversed(chain.split(",")):
            if not any(peer in net for net in networks):
                break
            peer = ip_address(hop.strip())
    except ValueError:
        peer = ip_address(host)
    scope = dict(scope, client=(str(peer), 0))
    if headers.get("x-forwarded-proto") == "https":
        scope["scheme"] = "wss" if scope["type"] == "websocket" else "https"
    return scope

class LoginCooldown:
    def __init__(self, capacity):
        self.entries = OrderedDict()
        self.capacity = capacity
        self.lock = Lock()

    def remaining(self, host, now):
        with self.lock:
            count, until, expiry = self.entries.get(host, (0, 0, 0))
            if expiry <= now:
                self.entries.pop(host, None)
                return 0
            return max(0, ceil(until - now))

    def record(self, host, status, now):
        with self.lock:
            previous = self.entries.pop(host, (0, 0, 0))
            if status == 200:
                return
            count = 1 + (previous[0] if previous[2] > now else 0)
            delay = min(900, 60 * 2 ** min(4, count - 5)) if count >= 5 else 0
            if len(self.entries) >= self.capacity:
                self.entries.popitem(last=False)
            self.entries[host] = (count, now + delay, now + 1800)
