"""
StealthClient — async HTTP client for all non-browser collector requests.

Handles: UA rotation, locale headers, cookie jar persistence, request jitter,
and exponential backoff. Browserless.io handles the browser layer separately.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

# ---------------------------------------------------------------------------
# User-Agent pool — realistic Chrome/Firefox on Win/Mac
# ---------------------------------------------------------------------------

_UA_POOL: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

_ACCEPT_LANGUAGE: dict[str, str] = {
    "uk": "en-GB,en;q=0.9",
    "se": "sv-SE,sv;q=0.9,en-GB;q=0.8,en;q=0.7",
}

_JITTER_MIN = 1.5
_JITTER_MAX = 3.5


class StealthClient:
    """
    Async HTTP client with anti-bot mitigations.

    One instance per audit session — maintains a shared cookie jar
    across requests to the same origin.
    """

    def __init__(self, geography: str = "uk") -> None:
        self._geography = geography
        self._lang = _ACCEPT_LANGUAGE.get(geography, _ACCEPT_LANGUAGE["uk"])
        self._cookies: dict[str, str] = {}

    def _headers(self, referer: str | None = None) -> dict[str, str]:
        h = {
            "User-Agent": random.choice(_UA_POOL),
            "Accept-Language": self._lang,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        if referer:
            h["Referer"] = referer
        return h

    async def _jitter(self) -> None:
        await asyncio.sleep(random.uniform(_JITTER_MIN, _JITTER_MAX))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def get(
        self,
        url: str,
        referer: str | None = None,
        params: dict[str, Any] | None = None,
        skip_jitter: bool = False,
    ) -> httpx.Response:
        if not skip_jitter:
            await self._jitter()
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            cookies=self._cookies,
        ) as client:
            response = await client.get(url, headers=self._headers(referer), params=params)
            self._cookies.update(dict(response.cookies))
            response.raise_for_status()
            return response

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def post(
        self,
        url: str,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        referer: str | None = None,
        skip_jitter: bool = False,
    ) -> httpx.Response:
        if not skip_jitter:
            await self._jitter()
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            cookies=self._cookies,
        ) as client:
            response = await client.post(
                url,
                headers=self._headers(referer),
                json=json,
                data=data,
            )
            self._cookies.update(dict(response.cookies))
            response.raise_for_status()
            return response
