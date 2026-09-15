"""One HTTP door for every provider, so tests can stand in front of it.

Every call a provider makes goes through `request` here, and every wait goes
through `sleep`. A test monkeypatches these two names and drives a whole
OAuth exchange or a chunked upload against canned responses without a socket
being opened. Nothing else in src/publish/providers imports httpx.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_TIMEOUT = 60.0
# A resumable upload PUT of a whole render can legitimately take minutes on
# a slow link; the read timeout is what bounds a stalled one.
UPLOAD_TIMEOUT = httpx.Timeout(600.0, connect=30.0)


@dataclass
class Response:
    status: int
    headers: dict = field(default_factory=dict)
    text: str = ""
    _json: Any = None

    def json(self) -> Any:
        if self._json is not None:
            return self._json
        import json
        try:
            return json.loads(self.text) if self.text else {}
        except ValueError:
            return {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


async def request(method: str, url: str, *, headers: dict | None = None,
                  params: dict | None = None, data: dict | None = None,
                  json: Any = None, content: Any = None,
                  timeout: Any = DEFAULT_TIMEOUT) -> Response:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        r = await client.request(method, url, headers=headers, params=params,
                                 data=data, json=json, content=content)
    return Response(status=r.status_code, headers=dict(r.headers), text=r.text)


async def sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def file_chunks(path, start: int, end: int, block: int = 1024 * 1024):
    """Yield bytes [start, end] of a file, a block at a time, so a render is
    never read into memory whole — the box has 2 GB and a render can be
    hundreds of MB. Async because httpx's AsyncClient only streams an async
    iterable; the reads are 1 MB and off a local disk."""
    remaining = end - start + 1
    with open(path, "rb") as fh:
        fh.seek(start)
        while remaining > 0:
            chunk = fh.read(min(block, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
