"""HTTP-клиент с rate limit (token bucket) и ретраями с экспоненциальным backoff."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class RateLimiter:
    """Token bucket: `rate` запросов в секунду, всплеск до `burst`."""

    def __init__(self, rate: float, burst: int | None = None):
        self.rate = rate
        self.capacity = float(burst or max(1, int(rate)))
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class ApiClient:
    def __init__(self, base_url: str, rate: float, burst: int | None = None,
                 max_retries: int = 5, timeout: float = 20.0, concurrency: int = 8):
        self.limiter = RateLimiter(rate, burst)
        self.max_retries = max_retries
        self._sem = asyncio.Semaphore(concurrency)
        self.client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout,
            headers={"User-Agent": "arb-scanner/0.1 (read-only)", "Accept": "application/json"},
        )

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        delay = 1.0
        for attempt in range(1, self.max_retries + 1):
            await self.limiter.acquire()
            try:
                async with self._sem:
                    resp = await self.client.request(method, path, **kwargs)
            except httpx.TransportError as e:
                err: str = f"{type(e).__name__}: {e}"
            else:
                if resp.status_code not in RETRY_STATUSES:
                    resp.raise_for_status()
                    return resp.json()
                err = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = max(delay, float(retry_after))
            if attempt == self.max_retries:
                raise RuntimeError(f"{method} {self.client.base_url}{path}: {err} после {attempt} попыток")
            sleep = delay + random.uniform(0, delay / 2)
            log.warning("%s %s: %s, повтор %d через %.1fs", method, path, err, attempt, sleep)
            await asyncio.sleep(sleep)
            delay = min(delay * 2, 60)

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params={k: v for k, v in params.items() if v is not None})

    async def post(self, path: str, json: Any) -> Any:
        return await self.request("POST", path, json=json)

    async def aclose(self) -> None:
        await self.client.aclose()
