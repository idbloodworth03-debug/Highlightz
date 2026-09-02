"""What a stranger can see, and what a crawler can understand.

TWO AUDITS THAT PULL OPPOSITE WAYS, which is why they live in one file: the
marketing surface should be as legible as possible to search engines and
language models, and the application surface should be invisible. A change that
helps one can quietly damage the other — opening a path for a crawler is how a
data endpoint gets exposed, and locking down a prefix is how a sitemap entry
starts 302-ing to a login page.

So these tests assert both edges of the same line:

  * Every route that is not deliberately public requires authentication, and
    the ones that ARE public carry only whitelisted fields.
  * Every page a crawler is pointed at actually returns 200 to a signed-out
    request, and the structured data on it is generated from the same values
    the page renders rather than typed alongside them.
"""

import json
import re

import pytest
from starlette.testclient import TestClient

from src.billing.plans import PLAN_LIMITS
from src.dashboard import api


@pytest.fixture()
def anon():
    """A stranger: no session cookie at all."""
    return TestClient(api.app)


# ── nothing loose ────────────────────────────────────────────────────────────

def test_only_the_intended_pages_answer_a_signed_out_visitor(anon):
    """The allowlist, enforced against the router rather than read off it.

    A new route is public if somebody adds it to _OPEN_PATHS. This is the check
    that a route did not become public some OTHER way — a prefix rule, a
    middleware early-return, a path that happens to start with /static.
    """
    expected = {
        "/", "/login", "/health", "/favicon.ico", "/tos", "/privacy", "/cookies",
        "/opt-out", "/opt-out/success", "/landing/stats", "/landing/showcase",
        "/robots.txt", "/sitemap.xml", "/llms.txt", "/llms-full.txt", "/tutorial",
        "/compare", "/billing/paywall",
    }
    got = set()
    for r in api.app.routes:
        path = getattr(r, "path", "")
        if "{" in path or "GET" not in (getattr(r, "methods", set()) or set()):
            continue
        if anon.get(path, follow_redirects=False).status_code == 200:
            got.add(path)
    assert got == expected, (
        f"public surface changed.\n  newly public: {sorted(got - expected)}"
        f"\n  no longer public: {sorted(expected - got)}")


def test_the_allowlist_itself_holds_only_intended_paths():
    """THE OUTCOME TEST ABOVE IS NOT ENOUGH, which mutation testing showed:
    adding "/clips" to _OPEN_PATHS left every response unchanged, because the
    handler checks authentication too. Defence in depth is why nothing broke —
    and exactly why the weakening would have gone unnoticed. The allowlist is
    the boundary, so assert the boundary."""
    allowed = set(api._OPEN_PATHS)
    data_routes = {"/clips", "/streams", "/profiles", "/me", "/vod", "/uploads",
                   "/admin", "/feedback", "/publish", "/training"}
    for path in allowed:
        for d in data_routes:
            assert not path.startswith(d), \
                f"{path} is in _OPEN_PATHS but sits on the {d} surface"
    assert "/llms.txt" in allowed and "/robots.txt" in allowed


@pytest.mark.parametrize("path", [
    "/clips", "/streams", "/profiles", "/me", "/vod/jobs", "/uploads",
    "/admin/users", "/admin/overview", "/admin/optout/list",
    "/admin/clip-refusals", "/feedback/mine", "/publish/schedule",
])
def test_application_data_is_never_served_to_a_stranger(anon, path):
    r = anon.get(path, follow_redirects=False)
    assert r.status_code != 200, f"{path} serves data to an unauthenticated request"


def test_a_signed_in_user_cannot_reach_admin_data():
    """403, not 404 and not 200 — the routes exist, they are just not theirs."""
    import base64, json as _j
    from itsdangerous import TimestampSigner
    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": "punter", "username": "punter",
         "is_admin": False, "subscription_status": "active"}).encode())).decode())
    for path in ("/admin/users", "/admin/overview", "/admin/optout/list",
                 "/admin/clip-refusals"):
        assert c.get(path).status_code == 403, f"{path} is not admin-gated"


def test_the_public_showcase_publishes_only_whitelisted_fields():
    """It is a projection, not a filter — which is why adding age_restricted to
    the clip record did not leak it. Anything NOT listed here is new and has to
    be considered before it ships."""
    from src.dashboard.api import _showcase_entry
    entry = _showcase_entry({
        "id": "c1", "channel": "aceu", "twitch_url": "u", "embed_url": "e",
        # None of these may come out the other side.
        "user_id": "u-secret", "chat_snapshot": ["something a viewer said"],
        "age_restricted": True, "trigger_signals": [{"type": "x"}],
        "approved_at": 1, "storage_url": "/opt/highlightz/clips/x.mp4",
    })
    assert set(entry) == {
        "hero", "gallery", "id", "clip_title", "channel", "game",
        "twitch_url", "embed_url", "thumbnail_url", "score", "duration_seconds",
        # The signal that led the clip — a category, not a person. Added for
        # the v4 shelf so a clip files under the right rail tab.
        "signal"}


def test_no_public_page_leaks_a_secret_or_an_internal_path(anon):
    BAD = ["client_secret", "sk_live", "sk_test", "whsec_", "redis://",
           "postgres://", "TOKEN_ENCRYPTION_KEY", "/opt/highlightz",
           "137.184.24.121", "DASHBOARD_PASSWORD"]
    for path in ("/", "/login", "/tutorial", "/compare", "/tos", "/privacy",
                 "/cookies", "/opt-out", "/billing/paywall", "/llms.txt",
                 "/landing/showcase", "/landing/stats"):
        body = anon.get(path).text.lower()
        hits = [b for b in BAD if b.lower() in body]
        assert not hits, f"{path} exposes {hits}"


def test_the_opt_out_registry_is_admin_only(anon):
    """Who has opted out is a list of streamers who asked not to be clipped.
    Publishing it would hand a scraper exactly the channels to avoid — or to
    target."""
    assert anon.get("/admin/optout/list", follow_redirects=False).status_code != 200
    # And the public page is a form to opt OUT, not a directory of who has.
    import inspect
    page_src = inspect.getsource(api.optout_landing)
    assert "get_all" not in page_src, \
        "the public opt-out page renders the registry"


def test_an_age_restricted_clip_cannot_be_featured_publicly():
    """The landing hero plays featured clips in an iframe and a gated clip
    cannot play in one, so featuring it puts a dead player on the marketing
    page. Checked at the endpoint, because that is where an admin does it."""
    import inspect
    src = inspect.getsource(api.admin_toggle_showcase)
    assert 'clip.get("age_restricted")' in src, \
        "an age-restricted clip can still be featured on the landing page"


# ── legible to crawlers and models ───────────────────────────────────────────

def test_every_page_in_the_sitemap_is_actually_reachable(anon):
    """A sitemap entry that 302s to /login is worse than no entry: it spends
    crawl budget and reports a soft 404."""
    urls = re.findall(r"<loc>([^<]+)</loc>", anon.get("/sitemap.xml").text)
    assert urls, "the sitemap is empty"
    for url in urls:
        path = url.replace("https://highlightz.app", "") or "/"
        assert anon.get(path, follow_redirects=False).status_code == 200, \
            f"{path} is in the sitemap but does not answer a signed-out request"


def test_robots_does_not_block_anything_the_sitemap_advertises(anon):
    txt = anon.get("/robots.txt").text
    blocked = [l.split(":", 1)[1].strip() for l in txt.splitlines()
               if l.lower().startswith("disallow:")]
    urls = re.findall(r"<loc>([^<]+)</loc>", anon.get("/sitemap.xml").text)
    for url in urls:
        path = url.replace("https://highlightz.app", "") or "/"
        for b in blocked:
            if b != "/" and path.startswith(b):
                pytest.fail(f"robots.txt disallows {b}, which blocks sitemap entry {path}")


def test_ai_crawlers_are_not_blocked(anon):
    """The whole point of the SEO half. A model that cannot fetch the page
    cannot recommend the product."""
    txt = anon.get("/robots.txt").text
    assert re.search(r"User-agent:\s*\*", txt), "no wildcard agent rule"
    assert re.search(r"Allow:\s*/", txt), "the wildcard agent is not allowed in"
    for bot in ("GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended",
                "CCBot", "anthropic-ai"):
        assert not re.search(rf"User-agent:\s*{bot}\s*\n\s*Disallow:\s*/",
                             txt, re.I), f"{bot} is blocked outright"


def test_personal_links_are_kept_out_of_the_index(anon):
    """Invite and referral links are one-to-one. Nothing secret is behind them,
    but indexing one puts somebody's referral code in search results."""
    txt = anon.get("/robots.txt").text
    assert "Disallow: /i/" in txt
    assert "Disallow: /r/" in txt


def test_llms_txt_is_public_and_plain_text(anon):
    """It was added to the router first and bounced to /login, because it was
    not in the open-path allowlist. A crawler file behind a login is a crawler
    file that does not exist."""
    r = anon.get("/llms.txt", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text.lstrip().startswith("# Highlightz")


def test_llms_txt_quotes_the_real_plans():
    """Derived, not typed. Every other surface that quoted a plan number went
    stale exactly once before it was generated; this one starts generated."""
    import importlib
    from src.dashboard import api as _api
    c = TestClient(_api.app)
    body = c.get("/llms.txt").text
    f = PLAN_LIMITS["free"]
    for n in (f["max_streams"], f["max_pending"], f["max_library_week"],
              PLAN_LIMITS["starter"]["price"], PLAN_LIMITS["pro"]["price"]):
        assert str(n) in body, f"llms.txt does not quote {n}"

    # Every field, not one of them: the first version moved max_library_week
    # only, so typing out max_pending survived untouched.
    d = PLAN_LIMITS["free"]
    for key, probe in (("max_streams", 8181), ("max_pending", 4242),
                       ("max_library_week", 9595)):
        before = d[key]
        d[key] = probe
        try:
            importlib.reload(_api)
            assert str(probe) in TestClient(_api.app).get("/llms.txt").text, \
                f"llms.txt types out free's {key} instead of reading it"
        finally:
            d[key] = before
            importlib.reload(_api)


def test_llms_txt_says_what_the_product_actually_is():
    """The point is that a model reading it describes the product correctly —
    live capture through Twitch's API, not an upload-and-edit tool."""
    body = TestClient(api.app).get("/llms.txt").text.lower()
    for phrase in ("twitch", "clips api", "live", "review queue", "vod"):
        assert phrase in body, f"llms.txt never mentions {phrase!r}"
    assert "never records" in body or "does not" in body, \
        "the no-video-hosting position is not stated"
    assert "opt-out" in body, "the broadcaster opt-out is not linked"


@pytest.mark.parametrize("path,expected_type", [
    ("/", "SoftwareApplication"),
    ("/tutorial", "HowTo"),
    ("/compare", "ItemList"),
])
def test_structured_data_is_present(anon, path, expected_type):
    body = anon.get(path).text
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                        body, re.S)
    types = set()
    for b in blocks:
        types.update(re.findall(r'"@type"\s*:\s*"([A-Za-z]+)"', b))
    assert expected_type in types, f"{path} has no {expected_type} markup"


def test_every_json_ld_block_is_valid_json(anon):
    """Malformed markup is ignored silently, so a broken block looks exactly
    like no block at all."""
    for path in ("/", "/tutorial", "/compare"):
        for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                            anon.get(path).text, re.S):
            json.loads(b)


def test_the_howto_steps_match_the_steps_on_the_page():
    """Structured data that disagrees with the visible page is what search
    engines treat as deceptive markup, so it is generated from the same
    source the page renders."""
    from src.dashboard.tutorial_html import render
    from src.dashboard import tutorial_content as C
    body = render()
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                        body, re.S)
    data = next(d for d in (json.loads(b) for b in blocks)
                if d.get("@type") == "HowTo")
    assert len(data["step"]) == len(C.QUICKSTART)
    for step, sec in zip(data["step"], C.QUICKSTART):
        assert step["name"] == sec.title
        assert sec.id in step["url"]
