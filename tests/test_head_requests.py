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
    """Bytes the app actually puts ON THE WIRE for a HEAD, via raw ASGI, and
    the scope the server is left holding.

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
        requested, finished = False, asyncio.Event()

        async def receive():
            # As a server does: the request once, then nothing until the
            # client leaves. Answering http.request forever made FileResponse's
            # disconnect listener fail ("Unexpected message received").
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await finished.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                finished.set()

        await api.app(scope, receive, send)
        body = b"".join(m.get("body", b"") for m in messages
                        if m["type"] == "http.response.body")
        start = next(m for m in messages if m["type"] == "http.response.start")
        return start["status"], body, scope

    return asyncio.run(go())


@pytest.mark.parametrize("path", PUBLIC)
def test_head_sends_no_body(path):
    """The whole point of HEAD. A body here wastes the bandwidth the method
    exists to save, and some clients treat it as a protocol error."""
    status, body, _ = _raw_head(path)
    assert body == b"", f"HEAD {path} put {len(body)} bytes on the wire"


@pytest.mark.parametrize("path", PUBLIC)
def test_the_server_is_still_told_head(path):
    """The routes see GET; the server's own scope must keep saying HEAD. uvicorn
    reads it to know a response has no body. The middleware used to rewrite
    it, and every HEAD then raised "Response content shorter than
    Content-Length" (2026-10-20)."""
    _, _, scope = _raw_head(path)
    assert scope["method"] == "HEAD", f"HEAD {path} left the server holding {scope['method']}"


# ── under the real server ─────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def served():
    """The app under uvicorn with httptools, as prod runs it. The 2026-10-20
    bug lived in the server's own bookkeeping, so neither the test client nor a
    raw ASGI call could see it."""
    pytest.importorskip("httptools")
    import logging
    import threading
    import time
    import uvicorn

    errors = []

    class _Catch(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                errors.append(record.getMessage())

    server = uvicorn.Server(uvicorn.Config(
        api.app, host="127.0.0.1", port=0, http="httptools", loop="asyncio",
        lifespan="off", log_config=None, log_level="warning"))
    catch = _Catch()
    logging.getLogger("uvicorn.error").addHandler(catch)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        assert time.time() < deadline, "uvicorn did not start"
        time.sleep(0.02)
    yield server.servers[0].sockets[0].getsockname()[1], errors
    server.should_exit = True
    thread.join(10)
    logging.getLogger("uvicorn.error").removeHandler(catch)


def test_head_under_the_real_server_logs_nothing_and_keeps_the_connection(served):
    """HEAD then GET for each page, on ONE keep-alive connection. Before the fix
    every HEAD logged "Exception in ASGI application ... Response content
    shorter than Content-Length" and the server dropped the connection, so the
    GET after it failed."""
    import http.client
    import time
    from src.dashboard.blog_content import ARTICLES

    port, errors = served
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        for path in ("/", "/health", "/compare", "/favicon.ico", "/blog",
                     "/blog/" + ARTICLES[0].slug):
            conn.request("HEAD", path)
            head = conn.getresponse()
            assert head.read() == b""
            conn.request("GET", path)
            get = conn.getresponse()
            body = get.read()
            assert head.status == get.status == 200, path
            assert head.getheader("content-length") == str(len(body)), path
    finally:
        conn.close()
    time.sleep(0.2)     # anything the server was going to log, it has by now
    assert errors == [], errors


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
