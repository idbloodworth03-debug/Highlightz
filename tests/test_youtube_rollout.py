"""YouTube opens to accounts ahead of Instagram (owner, 2026-11-18).

"make the changes to the site and make it live. Make it known everywhere on
the landing page and everywhere on the site." The uploader, the connect flow
and Autopilot's posting were built in September and held behind
`PUBLIC_PLATFORMS`; what was missing was a THIRD state. Every page knew
"TikTok alone" or "everything", and the second names Instagram, so adding
`youtube` to the switch would have promised Instagram Reels on the landing
page, the FAQ, both briefs and the compare page the same minute.

Google has not audited the app either, so YouTube keeps what it uploads
private (`YOUTUBE_AUDITED`, tests/test_publish_posting.py). The pages say so
beside every promise while that is true, and stop the day it is not.

What these defend:
  1. the release arithmetic for the mixed state;
  2. no public page promises Instagram while it is held, and each one names
     YouTube and says Instagram is coming soon;
  3. the private sentence is on every one of them before the audit and on
     none after;
  4. the announcements (the line above the headline, the plan row) are there
     only while YouTube is really posted to by itself;
  5. the TikTok-only pages are the pages they were;
  6. the dashboard reads which platforms are open instead of typing them.
"""

import os
import subprocess
import sys

import pytest

from src.publish import release


# ── 1. arithmetic ────────────────────────────────────────────────────────────

@pytest.fixture
def mixed(monkeypatch):
    monkeypatch.setattr(release.settings, "uploads_enabled", True)
    monkeypatch.setattr(release.settings, "public_platforms", "tiktok,youtube")
    monkeypatch.setattr(release.settings, "youtube_audited", False)


def test_youtube_ahead_of_instagram_is_its_own_state(mixed):
    assert release.live_platforms() == ("youtube", "tiktok")
    assert release.auto_live() == ("youtube",)          # never TikTok
    assert release.auto_posting_live() and not release.all_live()
    assert release.join(release.held_platforms()) == "Instagram"


def test_uploads_are_private_until_the_audit_and_only_while_youtube_is_open(mixed, monkeypatch):
    assert release.youtube_private() and release.youtube_note() == release.YOUTUBE_PRIVATE_NOTE
    monkeypatch.setattr(release.settings, "youtube_audited", True)
    assert not release.youtube_private() and release.youtube_note() == ""
    # Held: nobody is promised YouTube, so there is nothing to caveat.
    monkeypatch.setattr(release.settings, "youtube_audited", False)
    monkeypatch.setattr(release.settings, "public_platforms", "tiktok")
    assert not release.youtube_private()


def test_the_note_does_not_promise_it_can_be_made_public():
    """Whether YouTube lets the owner of the channel flip a video uploaded by
    an unaudited app is not known, so the sentence must not say it can."""
    low = release.YOUTUBE_PRIVATE_NOTE.lower()
    assert "private" in low and "google" in low
    assert "set it public" not in low and "make it public" not in low


# ── 2-5. the public pages, rendered under the switch ─────────────────────────

_SNIP = r"""
import asyncio, re
from src.dashboard import api, compare_content as C
def text(h): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
print("LANDING::" + text(api.LANDING_HTML))
print("RAW::" + api.LANDING_HTML.replace("\n", " "))
def body(r): return (r.body.decode() if hasattr(r, "body") else str(r)).replace("\n", " ")
print("LLMS::" + body(asyncio.run(api.llms_txt())))
print("FULL::" + body(asyncio.run(api.llms_full_txt())))
print("PAYWALL::" + api._paywall_copy("new")["subline"])
row = next(f for f in C.FEATURES if f[0] == "Auto-posts to Shorts and Reels")
print("ROW::" + repr(row[1]) + "|" + row[4])
from src.dashboard import compare_html
print("COMPARE::" + text(compare_html.render()))
print("PILL::" + api._proof_youtube())
"""
_KEYS = ("LANDING", "RAW", "LLMS", "FULL", "PAYWALL", "ROW", "COMPARE", "PILL")


def _surfaces(platforms: str, audited: bool = False, x_keys: bool = False) -> dict:
    env = dict(os.environ, UPLOADS_ENABLED="true", PUBLIC_PLATFORMS=platforms,
               TIKTOK_AUDITED="true", YOUTUBE_AUDITED="true" if audited else "false",
               X_CLIENT_ID="i" if x_keys else "", X_CLIENT_SECRET="s" if x_keys else "")
    out = subprocess.run([sys.executable, "-c", _SNIP], env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    got = {k: "" for k in _KEYS}
    for line in out.stdout.splitlines():
        k, _, v = line.partition("::")
        if k in _KEYS:
            got[k] = v
    return got


@pytest.fixture(scope="module")
def live():
    return _surfaces("tiktok,youtube")


@pytest.fixture(scope="module")
def audited():
    return _surfaces("tiktok,youtube", audited=True)


_PAGES = ("LANDING", "RAW", "LLMS", "FULL", "PAYWALL", "ROW")


def test_no_page_promises_instagram_while_it_is_held(live):
    for name in _PAGES:
        low = live[name].lower()
        for bad in ("youtube, tiktok and instagram",
                    "youtube and instagram accounts",
                    "youtube and instagram reels, from the scheduler",
                    "connect youtube or instagram and it posts"):
            assert bad not in low, f"{name} still says {bad!r}"
    # The compare page: a plain tick on "Shorts and Reels" would claim Reels.
    assert live["ROW"].startswith("'Shorts'|")
    assert "Instagram Reels is built and coming soon" in live["ROW"]
    assert "You want Reels or TikTok posted for you automatically." in live["COMPARE"]


def test_every_page_says_youtube_is_open_and_instagram_is_coming(live):
    s = live
    assert "posts your clips to it as Shorts for you" in s["LANDING"]
    assert "posted to YouTube by itself" in s["LANDING"]                  # the FAQ
    assert "auto-posting to YouTube built in." in s["RAW"]                # structured data
    assert "posts them to YouTube and TikTok." in s["RAW"]                # link previews
    assert "posted to YouTube and TikTok from the Scheduler (or by Autopilot" in s["LLMS"]
    assert "the Scheduler posts to YouTube and TikTok" in s["FULL"]
    assert "the Scheduler to post it to YouTube and TikTok for you" in s["PAYWALL"]
    for name in ("LANDING", "LLMS", "FULL"):
        assert "Posting to Instagram is coming soon." in s[name], name
    # "those accounts" Autopilot posts to by itself never include TikTok.
    assert "to the YouTube accounts you connect" in s["LLMS"]


def test_the_private_sentence_is_beside_every_promise_until_the_audit(live, audited):
    note = release.YOUTUBE_PRIVATE_NOTE
    for name in ("LANDING", "LLMS", "FULL", "ROW"):
        assert note in live[name], f"{name} promises YouTube without saying uploads land private"
        assert note not in audited[name], f"{name} still says private after the audit"
    # Twice on the landing page: under "Then post it." and in the FAQ answer
    # (which the FAQ structured data repeats, so count the visible text).
    assert live["LANDING"].count(note) >= 2


def test_the_announcements_are_there_only_while_it_is_true(live):
    assert 'class="proof-new" href="#post"' in live["PILL"]
    assert "now posts to YouTube Shorts" in live["PILL"]
    assert "Autopilot Yes Posts to YouTube Shorts for you Yes Front of the line No" in live["LANDING"]
    assert "| Posts to YouTube Shorts for you | No | Yes | Yes |" in live["FULL"]
    held = _surfaces("tiktok")
    assert held["PILL"] == ""
    assert "Posts to YouTube Shorts for you" not in held["LANDING"] + held["FULL"]
    # And the TikTok-only pages are the pages they were.
    assert "Scheduler Yes Autopilot Yes Front of the line No" in held["LANDING"]
    assert "Posting to YouTube and Instagram is coming soon" in held["LANDING"]
    assert release.YOUTUBE_PRIVATE_NOTE not in held["LANDING"] + held["LLMS"] + held["FULL"]


def test_the_line_above_the_headline_is_filled_per_request(monkeypatch):
    from src.dashboard import api
    assert "<!--PROOF_YOUTUBE-->" in api.LANDING_HTML
    assert api.LANDING_HTML.index("<!--PROOF_YOUTUBE-->") < api.LANDING_HTML.index("<!--PROOF_CAMPAIGNS-->")
    monkeypatch.setattr(release.settings, "uploads_enabled", True)
    monkeypatch.setattr(release.settings, "public_platforms", "tiktok,youtube")
    assert "now posts to YouTube Shorts" in api._proof_youtube()
    monkeypatch.setattr(release.settings, "public_platforms", "tiktok")
    assert api._proof_youtube() == ""
    monkeypatch.setattr(release.settings, "public_platforms", "tiktok,youtube")
    monkeypatch.setattr(release.settings, "uploads_enabled", False)
    assert api._proof_youtube() == ""


def test_everything_still_returns_when_every_platform_opens():
    s = _surfaces("youtube,tiktok,instagram", audited=True)
    assert "auto-posting built in" in s["RAW"]
    assert "YouTube, TikTok and Instagram" in s["LLMS"]
    assert "coming soon" not in s["PAYWALL"]
    assert s["ROW"].startswith("True|")
    assert "Connect your YouTube and Instagram accounts once" in s["LANDING"]


# ── X opened the same day ("lets get X active now too") ─────────────────────

@pytest.fixture(scope="module")
def with_x():
    return _surfaces("tiktok,youtube,x", x_keys=True)


def test_with_x_open_every_page_names_it_and_still_not_instagram(with_x):
    s = with_x
    for name in _PAGES:
        low = s[name].lower()
        for bad in ("youtube, tiktok and instagram", "youtube and instagram accounts",
                    "connect youtube or instagram and it posts"):
            assert bad not in low, f"{name} still says {bad!r}"
    assert "Connect your YouTube channel and X account once and the Scheduler posts your clips to them for you" in s["LANDING"]
    assert "posted to YouTube and X by itself" in s["LANDING"]
    assert "auto-posting to YouTube and X built in." in s["RAW"]
    assert "posts them to YouTube, TikTok and X." in s["RAW"]
    assert "posted to YouTube, TikTok and X from the Scheduler" in s["LLMS"]
    assert "to the YouTube and X accounts you connect" in s["LLMS"]
    assert "post it to YouTube, TikTok and X for you" in s["PAYWALL"]
    assert "now posts to YouTube Shorts and X" in s["PILL"]
    assert "Autopilot Yes Posts to YouTube Shorts and X for you Yes Front of the line No" in s["LANDING"]
    assert "| Posts to YouTube Shorts and X for you | No | Yes | Yes |" in s["FULL"]
    for name in ("LANDING", "LLMS", "FULL"):
        assert "Posting to Instagram is coming soon." in s[name], name
    # The compare row is about Shorts and Reels: X being open ticks neither.
    assert s["ROW"].startswith("'Shorts'|") and "It posts to X by itself too." in s["ROW"]


def test_x_in_the_switch_without_its_keys_changes_no_page(live):
    """`x` typed into PUBLIC_PLATFORMS before the keys are in must not put a
    word about X on the site: it does not exist until it is set up."""
    s = _surfaces("tiktok,youtube,x", x_keys=False)
    for name in ("LANDING", "RAW", "LLMS", "FULL", "PAYWALL", "ROW", "PILL"):
        assert s[name] == live[name], f"{name} changed with no X keys"


def test_x_set_up_but_held_is_coming_soon_and_nothing_more():
    s = _surfaces("tiktok,youtube", x_keys=True)
    assert "Posting to Instagram and X is coming soon." in s["LANDING"]
    assert "now posts to YouTube Shorts<" in s["PILL"]
    assert "Posts to YouTube Shorts for you Yes" in s["LANDING"]


# ── 6. the dashboard ─────────────────────────────────────────────────────────

def test_the_dashboard_names_the_platforms_that_are_open():
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    assert "function autoPosting(connections)" in h
    # The lines that used to type "YouTube and Instagram" read it instead.
    assert "Connected {autoPosting(connections).and} accounts are posted to for you; TikTok goes out" in h
    assert "To post now, tick {ap.or} above" in h
    assert "Connected {ap.and} accounts are posted to for you at this time" in h
    assert "Connect {autoPosting(connections).or} in Settings" in h
    # With neither open the old wording stands (the TikTok-only dashboard).
    assert "and: pitchList(open) || 'YouTube and Instagram'" in h
    assert "or: open.length ? open.join(' or ') : 'Instagram or YouTube'" in h


def test_the_new_bar_is_derived_and_goes_when_the_channel_is_connected():
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    # It names what opened (YouTube Shorts, then X the same day), from /me.
    assert "const NEWS = {youtube: 'YouTube Shorts', x: 'X'};" in h
    assert "New: Highlightz posts your clips to {pitchList(live.map(p => NEWS[p]))} for you." in h
    assert "Object.keys(NEWS).filter(p => (f.posting || []).includes(p))" in h    # only once released
    assert "live.includes(c.id) && c.configured && !c.connected" in h
    assert "'/publish/connect/' + todo.id" in h                   # the button connects that one
    assert "f.youtube_note ? ' ' + f.youtube_note : ''" in h      # the caveat rides with it
    # Closed per platform, so closing the YouTube news does not hide X's; the
    # first day's '1' still means YouTube.
    assert "hz_yt_shorts_seen" in h and "v === '1' ? 'youtube' : v" in h
    assert "if (!live.some(p => !seen.includes(p))) return null;" in h
    assert "refresh" not in h[h.index("New: Highlightz posts your clips"):][:900].lower()


def test_me_and_the_connection_row_carry_the_private_note(mixed, monkeypatch):
    from src.dashboard import api
    import inspect
    src = inspect.getsource(api._connections_payload)
    assert '"private_note"' in src and "settings.youtube_audited" in src
    assert api._release_youtube_note() == release.YOUTUBE_PRIVATE_NOTE
    monkeypatch.setattr(release.settings, "youtube_audited", True)
    assert api._release_youtube_note() == ""
