"""HEAD must work wherever GET does.

FastAPI's @app.get registers GET only. Plain Starlette Routes add HEAD
automatically; APIRoute does not — so every page on this site answered GET with
200 and HEAD with 405. Only /static escaped it, because StaticFiles implements
HEAD itself.

Found the hard way: `curl -sI https://highlightz.app/favicon.ico`, run to
confirm a favicon fix, returned 405. `curl -I` sends HEAD, and so do uptime
monitors, link checkers and some crawlers — every one of which would have read
this site as broken.
"""

import pytest
from fastapi.testclient import TestClient

from src.dashboard import api

# Everything a crawler, monitor or link checker actually touches.
PUBLIC = ["/", "/favicon.ico", "/robots.txt", "/sitemap.xml", "/tutorial",
          "/compare", "/tos", "/privacy", "/cookies", "/health",
          "/static/icon.png"]


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.mark.parametrize("path", PUBLIC)
def test_head_matches_get(client, path):
    get = client.request("GET", path, follow_redirects=False)
    head = client.request("HEAD", path, follow_redirects=False)
    assert head.status_code == get.status_code, \
        f"HEAD {path} is {head.status_code} while GET is {get.status_code}"


def _raw_head(path: str):
    """Bytes the app actually puts ON THE WIRE for a HEAD, via raw ASGI.

    The test client cannot answer this: httpx discards the body of a HEAD
    response itself, per HTTP semantics, so `response.content` is empty whether
    the server sent one or not. Checking through the client tests httpx, not us
    — a middleware returning the full page body passed that check happily.
    """
    import asyncio

    async def go():
        messages = []
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "HEAD", "scheme": "http", "path": path, "raw_path": path.encode(),
                 "query_string": b"", "root_path": "", "headers": [(b"host", b"testserver")],
                 "client": ("testclient", 50000), "server": ("testserver", 80)}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await api.app(scope, receive, send)
        body = b"".join(m.get("body", b"") for m in messages
                        if m["type"] == "http.response.body")
        start = next(m for m in messages if m["type"] == "http.response.start")
        return start["status"], body

    return asyncio.run(go())


@pytest.mark.parametrize("path", PUBLIC)
def test_head_sends_no_body(path):
    """The whole point of HEAD. A body here wastes the bandwidth the method
    exists to save, and some clients treat it as a protocol error."""
    status, body = _raw_head(path)
    assert body == b"", f"HEAD {path} put {len(body)} bytes on the wire"


def test_head_reports_the_length_the_body_would_have_been(client):
    """RFC 9110: HEAD returns the headers GET would, without the body. A
    Content-Length of 0 tells a monitor the page is empty."""
    get = client.request("GET", "/", follow_redirects=False)
    head = client.request("HEAD", "/", follow_redirects=False)
    assert head.headers.get("content-length") == str(len(get.content))
    assert int(head.headers["content-length"]) > 1000, \
        "content-length collapsed to zero — the page reads as empty"


def test_the_favicon_answers_head(client):
    """The specific check that surfaced this. `curl -I` on the favicon is how
    anyone verifies the Google search icon, and it must not 405."""
    r = client.request("HEAD", "/favicon.ico", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")


def test_head_does_not_trigger_billing(client):
    """HEAD is dispatched as GET, so a route with side effects would now fire
    on a crawler's probe. Checkout needs a session, so an anonymous HEAD gets
    turned away exactly as an anonymous GET would — no Stripe call."""
    for path in ("/billing/checkout", "/billing/portal"):
        r = client.request("HEAD", path, follow_redirects=False)
        assert r.status_code in (401, 302, 503), \
            f"HEAD {path} returned {r.status_code} — it may have done something"


def test_other_methods_are_still_refused(client):
    """Making HEAD work must not make everything work. POST to a GET-only page
    should still be rejected."""
    r = client.request("POST", "/robots.txt", follow_redirects=False)
    assert r.status_code == 405
