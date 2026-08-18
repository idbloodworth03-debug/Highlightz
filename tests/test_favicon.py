"""The favicon has to be reachable at the domain root.

Google search results were showing the default globe instead of the site's
mark. The icon itself was never the problem — 256x256 square PNG, declared via
<link rel="icon"> on every page, and robots.txt does not block /static. What
was missing was /favicon.ico, which returned 404: Google's favicon crawler asks
for that path at the root IN ADDITION to reading the link tag.
"""

import struct
import pathlib

import pytest
from fastapi.testclient import TestClient

from src.dashboard import api

STATIC = pathlib.Path(api.__file__).parent / "static"


@pytest.fixture
def client():
    return TestClient(api.app)


def test_the_root_favicon_path_serves_the_icon(client):
    """THE bug. A 404 here is the single most common cause of the globe."""
    r = client.get("/favicon.ico", follow_redirects=False)
    assert r.status_code == 200, "/favicon.ico is a 404 again"
    assert r.headers["content-type"].startswith("image/")
    assert len(r.content) > 500, "the icon came back empty"


def test_it_is_reachable_without_signing_in(client):
    """Crawlers are anonymous. Behind the auth middleware it may as well not
    exist — and the redirect to /login would look like a valid response."""
    assert "/favicon.ico" in api._OPEN_PATHS
    r = client.get("/favicon.ico", follow_redirects=False)
    assert r.status_code != 302


def test_it_is_declared_before_the_catch_all():
    """`/{slug}` swallows any single-segment path declared after it."""
    paths = [getattr(r, "path", "") for r in api.app.routes]
    assert paths.index("/favicon.ico") < paths.index("/{slug}")


def test_the_icon_meets_what_google_asks_for():
    """Square, and big enough. Google renders it at 16px but wants at least
    48px to downscale from; a non-square icon gets rejected outright."""
    data = (STATIC / "icon.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "icon.png is not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    assert width == height, f"favicon is not square ({width}x{height})"
    assert width >= 48, f"favicon is too small for Google ({width}px)"


def test_the_link_tag_still_points_at_a_real_file():
    """Belt and braces: the root path is the crawler's fallback, the link tag
    is what browsers and Google actually prefer. Both must resolve."""
    from src.dashboard.api import LANDING_HTML
    assert 'rel="icon"' in LANDING_HTML
    i = LANDING_HTML.index('rel="icon"')
    tag = LANDING_HTML[LANDING_HTML.rindex("<link", 0, i):LANDING_HTML.index(">", i)]
    assert "/static/icon.png" in tag
    assert (STATIC / "icon.png").is_file()


def test_crawlers_are_not_blocked_from_the_icon(client):
    """A Disallow covering /static would stop Google fetching the icon at all,
    and the failure would look identical to it not existing."""
    robots = client.get("/robots.txt").text
    disallowed = [l.split(":", 1)[1].strip()
                  for l in robots.splitlines() if l.lower().startswith("disallow")]
    for rule in disallowed:
        assert not "/static".startswith(rule.rstrip("/") or "\0"), \
            f"robots.txt rule {rule!r} blocks the favicon"
        assert not "/favicon.ico".startswith(rule.rstrip("/") or "\0"), \
            f"robots.txt rule {rule!r} blocks the favicon"
