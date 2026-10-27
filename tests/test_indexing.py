"""Getting the site properly indexed (owner, 2026-10-27: "Do everything you
need to make my site properly indexed to help things show up in broader
searches").

What these defend:
  1. a link written with a trailing slash (/blog/, /tutorial/) reaches the
     page through one permanent redirect to the canonical address; it used to
     land on /login, so the page lost the link. Only open pages, only GET and
     HEAD, and never anywhere but the same path without the slash;
  2. IndexNow (src/dashboard/indexnow.py): the key file is served signed out
     once a key exists and never before; only new, changed or removed pages
     are sent; nothing is sent unless the public site serves this server's
     own key; failures wait and retry, and nothing is recorded as sent until
     an engine accepted it;
  3. every public page allows a large image preview, and the private pages
     stay noindex;
  4. the site icon answers at /apple-touch-icon.png instead of a /login hop.
"""

import asyncio
import inspect
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard import api
from src.dashboard import indexnow
from src.dashboard.blog_content import paths as _blog_paths

anon = TestClient(api.app)
PUBLIC = (["/", "/tutorial", "/compare"] + list(_blog_paths())
          + ["/tos", "/privacy", "/cookies", "/opt-out"])


# ── 1. trailing slashes ─────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [p for p in PUBLIC if p != "/"])
def test_a_trailing_slash_redirects_permanently_to_the_canonical_page(path):
    r = anon.get(path + "/", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == path, (path, r.status_code)
    assert anon.get(path + "/").url.path == path


def test_the_redirect_keeps_the_query_and_eats_every_slash():
    r = anon.get("/blog/?utm_source=reddit&ref=x", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/blog?utm_source=reddit&ref=x"
    assert anon.get("/tutorial//", follow_redirects=False).headers["location"] == "/tutorial"
    assert anon.head("/compare/", follow_redirects=False).status_code == 301


def test_only_open_pages_and_only_reads_are_redirected():
    # A private page with a slash is still the sign-in page's business.
    r = anon.get("/admin/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"
    # Never an open redirect: the target is always this path, slashless.
    for evil in ("//evil.example/", "/%2F%2Fevil.example/", "///evil.example//"):
        loc = anon.get(evil, follow_redirects=False).headers.get("location", "")
        assert "evil" not in loc, (evil, loc)
    # A POST is not a link; it is answered (or refused) where it was sent.
    assert anon.post("/kick/webhook/", content=b"{}", follow_redirects=False).status_code != 301
    assert anon.get("/", follow_redirects=False).status_code == 200


# ── 2. IndexNow ─────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "local_storage_path", str(tmp_path))
    monkeypatch.setattr(api.settings, "indexnow_enabled", True)
    monkeypatch.setattr(indexnow, "_key", None)
    monkeypatch.setattr(indexnow, "_key_read_at", 0.0)
    return tmp_path


def test_no_key_file_until_a_key_exists_then_only_that_one(store):
    assert indexnow.key_path() is None
    assert anon.get("/0123456789abcdef0123456789abcdef.txt",
                    follow_redirects=False).status_code in (302, 404)
    key = indexnow.ensure_key()
    assert re.fullmatch(r"[0-9a-f]{32}", key)
    assert indexnow.ensure_key() == key, "the key must survive: engines remember it"
    assert (store / "indexnow.json").is_file()
    r = anon.get(f"/{key}.txt", follow_redirects=False)
    assert r.status_code == 200 and r.text == key
    assert r.headers["content-type"].startswith("text/plain")
    other = key[:-1] + ("1" if key[-1] != "1" else "2")
    assert anon.get(f"/{other}.txt", follow_redirects=False).status_code in (302, 404)


def test_switched_off_there_is_no_key_file_and_no_task(store, monkeypatch):
    key = indexnow.ensure_key()
    monkeypatch.setattr(api.settings, "indexnow_enabled", False)
    assert indexnow.key_path() is None
    assert anon.get(f"/{key}.txt", follow_redirects=False).status_code in (302, 404)
    slept = []

    async def no_sleep(s):
        slept.append(s)
    monkeypatch.setattr(indexnow.asyncio, "sleep", no_sleep)
    asyncio.run(indexnow.run(lambda: {"https://highlightz.app/": "2026-10-27"}, api.SITE_ORIGIN))
    assert slept == [], "a switched-off task must return at once"


def test_due_is_new_and_changed_pages_then_removed_ones():
    sent = {"https://h/a": "1", "https://h/b": "1", "https://h/gone": "1"}
    pages = {"https://h/a": "1", "https://h/b": "2", "https://h/new": "1"}
    assert indexnow.due(pages, sent) == ["https://h/b", "https://h/new", "https://h/gone"]
    assert indexnow.due(pages, dict(pages)) == []


class _Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


class _Client:
    """httpx.AsyncClient's surface, as submit_due uses it."""

    def __init__(self, serves=None, post_status=200, post_exc=None):
        self.serves, self.post_status, self.post_exc = serves, post_status, post_exc
        self.gets, self.posts = [], []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kw):
        self.gets.append(url)
        return _Resp(200, self.serves) if self.serves is not None else _Resp(404)

    async def post(self, url, json=None, **kw):
        if self.post_exc:
            raise self.post_exc
        self.posts.append((url, json))
        return _Resp(self.post_status)


def _with(monkeypatch, client):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: client)
    return client


PAGES = {"https://highlightz.app/": "2026-10-27", "https://highlightz.app/blog": "2026-10-27",
         "https://highlightz.app/tos": "2026-10-20"}


def test_the_first_pass_sends_every_page_once_and_then_nothing(store, monkeypatch):
    key = indexnow.ensure_key()
    c = _with(monkeypatch, _Client(serves=key))
    assert asyncio.run(indexnow.submit_due(dict(PAGES), api.SITE_ORIGIN)) == indexnow.EVERY_S
    assert c.gets == [f"https://highlightz.app/{key}.txt"]
    (url, body), = c.posts
    assert url == "https://api.indexnow.org/indexnow"
    assert body == {"host": "highlightz.app", "key": key,
                    "keyLocation": f"https://highlightz.app/{key}.txt", "urlList": list(PAGES)}
    # Nothing changed: no request at all, not even the self-check.
    c2 = _with(monkeypatch, _Client(serves=key))
    assert asyncio.run(indexnow.submit_due(dict(PAGES), api.SITE_ORIGIN)) == indexnow.EVERY_S
    assert c2.gets == [] and c2.posts == []


def test_a_changed_and_a_removed_page_are_sent_and_nothing_else(store, monkeypatch):
    key = indexnow.ensure_key()
    _with(monkeypatch, _Client(serves=key))
    asyncio.run(indexnow.submit_due(dict(PAGES), api.SITE_ORIGIN))
    now = {"https://highlightz.app/": "2026-10-28", "https://highlightz.app/blog": "2026-10-27"}
    c = _with(monkeypatch, _Client(serves=key))
    asyncio.run(indexnow.submit_due(now, api.SITE_ORIGIN))
    (_, body), = c.posts
    assert body["urlList"] == ["https://highlightz.app/", "https://highlightz.app/tos"]


def test_a_server_whose_key_the_site_does_not_serve_sends_nothing(store, monkeypatch):
    """A development copy has its own key, which highlightz.app does not
    serve; so does a production server that is not answering yet."""
    indexnow.ensure_key()
    for c in (_Client(serves=None), _Client(serves="somebody-elses-key")):
        _with(monkeypatch, c)
        assert asyncio.run(indexnow.submit_due(dict(PAGES), api.SITE_ORIGIN)) == indexnow.RETRY_S
        assert c.posts == []
    assert "sent" not in indexnow._read()


@pytest.mark.parametrize("status,wait", [(422, indexnow.RETRY_S), (403, indexnow.RETRY_S),
                                         (500, indexnow.RETRY_S), (429, indexnow.EVERY_S)])
def test_a_refusal_is_not_recorded_as_sent(store, monkeypatch, status, wait):
    key = indexnow.ensure_key()
    _with(monkeypatch, _Client(serves=key, post_status=status))
    assert asyncio.run(indexnow.submit_due(dict(PAGES), api.SITE_ORIGIN)) == wait
    assert "sent" not in indexnow._read(), "the next pass must try these pages again"


def test_202_counts_as_accepted_and_a_network_error_waits_an_hour(store, monkeypatch):
    key = indexnow.ensure_key()
    _with(monkeypatch, _Client(serves=key, post_exc=OSError("unreachable")))
    assert asyncio.run(indexnow.submit_due(dict(PAGES), api.SITE_ORIGIN)) == indexnow.RETRY_S
    _with(monkeypatch, _Client(serves=key, post_status=202))
    assert asyncio.run(indexnow.submit_due(dict(PAGES), api.SITE_ORIGIN)) == indexnow.EVERY_S
    assert indexnow._read()["sent"] == PAGES


def test_the_task_never_raises_and_keeps_going(store, monkeypatch):
    waits = []

    class Stop(BaseException):
        pass

    async def fake_sleep(s):
        waits.append(s)
        if len(waits) == 3:
            raise Stop

    def boom():
        raise RuntimeError("sitemap exploded")
    monkeypatch.setattr(indexnow.asyncio, "sleep", fake_sleep)
    with pytest.raises(Stop):
        asyncio.run(indexnow.run(boom, api.SITE_ORIGIN))
    assert waits == [indexnow.FIRST_DELAY_S, indexnow.RETRY_S, indexnow.RETRY_S]


def test_what_is_announced_is_exactly_the_sitemap():
    xml = anon.get("/sitemap.xml").text
    locs = re.findall(r"<url><loc>([^<]+)</loc>(?:<lastmod>([^<]+)</lastmod>)?</url>", xml)
    assert api.indexnow_pages() == {loc: mod for loc, mod in locs}


def test_the_server_starts_the_task():
    import src.main as main
    assert "dashboard_api.indexnow_task()" in inspect.getsource(main)


def test_robots_does_not_block_the_key_file(store):
    key = indexnow.ensure_key()
    robots = anon.get("/robots.txt").text
    for line in robots.splitlines():
        if line.startswith("Disallow:"):
            assert not f"/{key}.txt".startswith(line.split(":", 1)[1].strip()), line


# ── 3. large previews; private pages stay out ───────────────────────────────

@pytest.mark.parametrize("path", PUBLIC)
def test_every_public_page_allows_a_large_image_preview(path):
    h = re.search(r"<head>(.*?)</head>", anon.get(path).text, re.S).group(1)
    assert h.count('<meta name="robots" content="max-image-preview:large">') == 1, path
    assert "noindex" not in h, path


def test_the_streamer_pitch_allows_it_too_once_live(monkeypatch):
    monkeypatch.setattr(api.settings, "campaign_requests_enabled", True)
    assert '<meta name="robots" content="max-image-preview:large">' in anon.get("/promote").text


def test_the_private_pages_are_still_noindex():
    for html in (api.LOGIN_HTML, api.PAYWALL_HTML):
        assert '<meta name="robots" content="noindex">' in html
        assert "max-image-preview" not in html


# ── 4. the touch icon ───────────────────────────────────────────────────────

def test_the_touch_icon_is_the_site_icon_signed_out():
    icon = (Path(api._STATIC_DIR) / "icon.png").read_bytes()
    for p in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
        r = anon.get(p, follow_redirects=False)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png", p
        assert r.content == icon, p
