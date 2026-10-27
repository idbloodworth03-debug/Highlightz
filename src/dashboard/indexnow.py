"""IndexNow: tell search engines the moment a public page is new or changed
(owner, 2026-10-27: "make my site properly indexed to help things show up in
broader searches").

WHY. A crawler finds a changed page on its own schedule, which for a young
site can be weeks. IndexNow is the push half: one POST to api.indexnow.org and
Bing, Yandex, Seznam, Naver and Yep all hear about it. Bing's index also feeds
Copilot, Yahoo and DuckDuckGo, and is one of the sources ChatGPT search uses,
so outside Google this is the fastest way in. Google does not take IndexNow;
it reads the sitemap robots.txt names, and Search Console.

HOW.
  - A key (32 hex characters) is made once and kept beside the other stores
    (LOCAL_STORAGE_PATH/indexnow.json), so it survives deploys. The site serves
    it at /<key>.txt: that file is how an engine knows a submission came from
    whoever controls the domain.
  - Two minutes after each start, then once a day, the sitemap's pages and
    their lastmod dates are compared with what was last sent. New or changed
    pages are submitted, and so are pages that left the sitemap (an engine has
    to re-crawl a removed page to drop it). Nothing changed: nothing is sent.
  - A failed send is tried again an hour later; a 429 waits a day.

ONLY THE REAL SITE SUBMITS. Before sending, the task fetches its own key file
from the public origin. A development copy, whose key is not the one
highlightz.app serves, sees the mismatch and stays quiet, and so does a
production server whose key file cannot be reached yet. INDEXNOW_ENABLED=false
turns all of it off, the key file included.

NEVER RAISES. A search engine being down is not this server's problem.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

ENDPOINT = "https://api.indexnow.org/indexnow"
FIRST_DELAY_S = 120
EVERY_S = 24 * 3600
RETRY_S = 3600
MAX_URLS = 10_000                       # per request, the protocol's limit
_KEY_RE = re.compile(r"^[A-Za-z0-9-]{8,128}$")

_key: str | None = None
_key_read_at = 0.0


def _store() -> Path:
    return Path(settings.local_storage_path) / "indexnow.json"


def _read() -> dict:
    try:
        d = json.loads(_store().read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write(d: dict) -> None:
    p = _store()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
    os.replace(tmp, p)


def current_key() -> str | None:
    """The key if one has been made; never makes one. What the key-file route
    serves. While there is none the store is re-read at most once a minute, so
    a key made by the first pass is served from then on."""
    global _key, _key_read_at
    if not settings.indexnow_enabled:
        return None
    if _key is None and time.time() - _key_read_at >= 60:
        _key_read_at = time.time()
        k = _read().get("key")
        if isinstance(k, str) and _KEY_RE.match(k):
            _key = k
    return _key


def key_path() -> str | None:
    """"/<key>.txt", the address engines fetch the key from, or None."""
    k = current_key()
    return f"/{k}.txt" if k else None


def ensure_key() -> str:
    """The key, made and stored the first time it is needed."""
    global _key
    d = _read()
    k = d.get("key")
    if not (isinstance(k, str) and _KEY_RE.match(k)):
        k = secrets.token_hex(16)
        d["key"] = k
        _write(d)
    _key = k
    return k


def due(pages: dict[str, str], sent: dict[str, str]) -> list[str]:
    """New or changed pages first, then pages that have left the sitemap."""
    return ([u for u, mod in pages.items() if sent.get(u) != mod]
            + [u for u in sent if u not in pages])


async def _serves_own_key(client, origin: str, key: str) -> bool:
    try:
        r = await client.get(f"{origin}/{key}.txt", timeout=15, follow_redirects=False)
    except Exception:
        return False
    return r.status_code == 200 and r.text.strip() == key


async def submit_due(pages: dict[str, str], origin: str) -> float:
    """One pass: send what is due. Returns the seconds to wait before the next."""
    import httpx
    key = ensure_key()
    d = _read()
    sent = d.get("sent") if isinstance(d.get("sent"), dict) else {}
    urls = due(pages, sent)
    if not urls:
        return EVERY_S
    async with httpx.AsyncClient() as client:
        if not await _serves_own_key(client, origin, key):
            # A development copy, or the site not answering yet. Quietly.
            log.info("indexnow_skipped", reason="this server's key is not what the public site serves")
            return RETRY_S
        body = {"host": urlsplit(origin).hostname, "key": key,
                "keyLocation": f"{origin}/{key}.txt", "urlList": urls[:MAX_URLS]}
        try:
            r = await client.post(ENDPOINT, json=body, timeout=30)
        except Exception as exc:
            log.warning("indexnow_failed", error=str(exc)[:200])
            return RETRY_S
    if r.status_code in (200, 202):        # 202: accepted, key check pending
        d["sent"] = dict(pages)
        d["sent_at"] = time.time()
        _write(d)
        log.info("indexnow_submitted", urls=len(urls), status=r.status_code)
        return EVERY_S
    if r.status_code == 429:
        log.warning("indexnow_rate_limited")
        return EVERY_S
    log.warning("indexnow_rejected", status=r.status_code, body=r.text[:200])
    return RETRY_S


async def run(pages: Callable[[], dict[str, str]], origin: str) -> None:
    """The background task (src/main.py): a pass two minutes after start, so
    the site is answering when its key is checked, then one a day."""
    if not settings.indexnow_enabled:
        return
    await asyncio.sleep(FIRST_DELAY_S)
    while True:
        try:
            wait = await submit_due(pages(), origin)
        except Exception as exc:            # never into the caller
            log.warning("indexnow_failed", error=str(exc)[:200])
            wait = RETRY_S
        await asyncio.sleep(wait)
