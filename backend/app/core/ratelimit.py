"""Per-client-address rate limiting (sliding window, in memory).

Complements the per-account lockout: lockout stops guessing one account's password, this stops one address from hammering the login or the
whole API (credential stuffing across many accounts, scripted scraping). State lives in this process only, so with several worker
processes each one counts separately (limits are then up to N times looser) and a restart clears the counters. A shared store such as
Redis is the next step if the API is scaled out.
"""

import threading
import time
from collections import deque

from fastapi import Request


def client_ip(request: Request, trusted_proxies: int) -> str:
    """Address of the real client. With `trusted_proxies` = n reverse proxies in front, take the n-th entry from the right of
    X-Forwarded-For + peer; entries a client invents sit further left and are ignored. With 0, only the direct peer is used."""
    peer = request.client.host if request.client else "unknown"
    if trusted_proxies <= 0:
        return peer
    xff = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
    chain = [*xff, peer]
    return chain[max(0, len(chain) - 1 - trusted_proxies)]


class SlidingWindowLimiter:
    def __init__(self, window_s: float = 60.0):
        self.window = window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_prune = 0.0

    def check(self, key: str, limit: int, now: float | None = None) -> int | None:
        """Record a request. Returns None if allowed, else the number of seconds to wait."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= limit:
                return max(1, int(self.window - (now - q[0])) + 1)
            q.append(now)
            if now - self._last_prune > self.window:  # drop idle keys so memory cannot grow without bound
                self._last_prune = now
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window]:
                    del self._hits[k]
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
