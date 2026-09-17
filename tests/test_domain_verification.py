"""Proving we own highlightz.app to the posting consoles.

WHY THIS EXISTS. TikTok will not accept a web redirect URI until the domain is
verified under URL properties, and Meta asks for the same on some review
paths. Both offer a file at the site root, a <meta> tag, or a DNS record.

The file method was impossible before 2026-09-17: an unauthenticated request
for a root path is bounced to /login by AuthMiddleware, so the verifier saw a
302 and read it as "this domain is not yours". The redirect is the thing these
tests pin, because it fails silently in exactly the place nobody looks.
"""

import re

import pytest
from fastapi.testclient import TestClient

from src.dashboard import api


@pytest.fixture
def scene(tmp_path, monkeypatch):
    d = tmp_path / "verify"
    d.mkdir()
    monkeypatch.setattr(api, "_VERIFY_DIR", d)
    monkeypatch.setattr(api, "_verify_cache", (0.0, frozenset()))
    # No session on the client: a verifier is a crawler.
    return TestClient(api.app, base_url="https://testserver"), d


def _drop(d, name, body="ownership-proof"):
    (d / name).write_text(body)
    api._verify_cache = (0.0, frozenset())     # the 60s listing cache
    return name


# ── the file method ──────────────────────────────────────────────────────────

def test_a_verification_file_is_served_at_the_site_root_with_no_session(scene):
    c, d = scene
    _drop(d, "tiktokAbC123def.txt", "tiktok-developers-site-verification=AbC123")
    r = c.get("/tiktokAbC123def.txt", follow_redirects=False)
    assert r.status_code == 200, f"the verifier got {r.status_code}, not the file"
    assert "tiktok-developers-site-verification" in r.text
    assert r.headers["content-type"].startswith("text/plain")


def test_meta_html_verification_files_work_too(scene):
    """Meta's method is an .html file, and it reads the body, not the type."""
    c, d = scene
    _drop(d, "meta-domain-verify.html", "<meta name='facebook-domain-verification' content='z9'>")
    r = c.get("/meta-domain-verify.html", follow_redirects=False)
    assert r.status_code == 200 and "facebook-domain-verification" in r.text


def test_an_unknown_root_path_still_redirects_a_signed_out_visitor(scene):
    """The door is only open for files that are actually there. Everything
    else keeps the old behaviour, or the catch-all becomes a probe for what
    exists on disk."""
    c, _ = scene
    r = c.get("/not-a-verification-file.txt", follow_redirects=False)
    assert r.status_code in (302, 404), r.status_code
    assert "verification" not in r.text.lower()


@pytest.mark.parametrize("name", [
    "../../../etc/passwd", "..%2f..%2fetc%2fpasswd", ".env", "sub/dir.txt",
])
def test_nothing_can_climb_out_of_the_verify_directory(scene, name):
    c, d = scene
    _drop(d, "real.txt")
    r = c.get("/" + name, follow_redirects=False)
    assert r.status_code != 200 or "ownership-proof" not in r.text
    assert "root:" not in r.text


def test_the_readme_is_not_served_as_a_verification_file(scene):
    c, d = scene
    (d / "README.md").write_text("# how to verify")
    api._verify_cache = (0.0, frozenset())
    assert api._verification_file("README.md") is None


def test_the_listing_is_cached_but_refreshes_within_a_minute(scene, monkeypatch):
    """Dropping a file on the server must not need a restart — the console is
    usually waiting on the other tab — but the check runs on unmatched
    requests, so it cannot stat the disk every time either."""
    c, d = scene
    assert api._verification_file("late.txt") is None          # primes the cache
    (d / "late.txt").write_text("x")
    assert api._verification_file("late.txt") is None, "the cache is not being used"
    monkeypatch.setattr(api.time, "time", lambda: api._verify_cache[0] + 61)
    assert api._verification_file("late.txt") is not None, "the cache never expires"


def test_a_referral_link_still_works_with_a_verification_file_present(scene, monkeypatch):
    """The two share the catch-all route. A verification filename is never a
    referral code, but the ordering has to be proven rather than assumed."""
    c, d = scene
    _drop(d, "tiktok123.txt")
    monkeypatch.setattr(api, "_referral_paths", lambda: {"/tommy"})
    from src.auth import referrals
    monkeypatch.setattr(referrals, "normalise", lambda s: "tommy" if s == "tommy" else "")
    r = c.get("/tommy", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"


# ── the meta-tag method ──────────────────────────────────────────────────────

def test_the_meta_tag_method_lands_in_the_landing_head(monkeypatch):
    monkeypatch.setattr(
        api.settings, "site_verification_tags",
        "tiktok-developers-site-verification=AbC123, facebook-domain-verification=z9")
    html = api.render_landing()
    head = html[:html.index("</head>")]
    assert '<meta name="tiktok-developers-site-verification" content="AbC123">' in head
    assert '<meta name="facebook-domain-verification" content="z9">' in head


def test_no_setting_leaves_no_marker_and_no_tags(monkeypatch):
    monkeypatch.setattr(api.settings, "site_verification_tags", "")
    html = api.render_landing()
    assert "<!--VERIFY-->" not in html, "the placeholder reached a visitor"
    assert "verification" not in html[:html.index("</head>")].lower()


def test_a_half_typed_pair_cannot_break_the_page_or_inject_markup(monkeypatch):
    monkeypatch.setattr(api.settings, "site_verification_tags",
                        'broken,,=nothing,name=,ok=fine,x="><script>alert(1)</script>')
    head = api.render_landing()
    head = head[:head.index("</head>")]
    assert '<meta name="ok" content="fine">' in head
    assert "<script>alert(1)</script>" not in head
    assert head.count("<meta name=") >= 1
    assert len(re.findall(r'<meta name="(?:broken|name)"', head)) == 0
