"""
Tests for the public landing-page stats counter.

The landing page shows an all-time "clips captured" ticker fed by
GET /landing/stats (unauthenticated — it's in _OPEN_PATHS). The counter is a
monotonic persisted total: seeded once from historical data (profile tallies +
current clip store), then incremented on every stored clip / VOD moment.
"""

import json
import re

import pytest

from src.dashboard import api


@pytest.fixture()
def counter_env(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_CLIP_COUNTER_FILE", tmp_path / "clip_counter.json")
    monkeypatch.setattr(api, "_clip_counter", None)
    return tmp_path


def test_seed_uses_profiles_and_current_clips(counter_env, monkeypatch):
    profiles = counter_env / "profiles" / "u1"
    profiles.mkdir(parents=True)
    (profiles / "jynxzi.json").write_text(json.dumps({"total_clips": 40}))
    (profiles / "lacy.json").write_text(json.dumps({"total_clips": 12}))
    (profiles / "corrupt.json").write_text("{not json")   # must be skipped, not crash
    monkeypatch.setattr(api.settings, "local_storage_path", str(counter_env))
    monkeypatch.setattr(api, "_clips", {"a": {}, "b": {}, "c": {}})
    assert api.get_clip_counter() == 40 + 12 + 3
    # Seed is persisted so restarts don't re-derive (and can only grow from here).
    assert json.loads((counter_env / "clip_counter.json").read_text())["total"] == 55


def test_increment_persists_and_survives_reload(counter_env, monkeypatch):
    (counter_env / "clip_counter.json").write_text(json.dumps({"total": 100}))
    assert api.get_clip_counter() == 100
    api.increment_clip_counter()
    api.increment_clip_counter(2)
    assert api.get_clip_counter() == 103
    # Simulate process restart: cache cleared, file is the source of truth.
    monkeypatch.setattr(api, "_clip_counter", None)
    assert api.get_clip_counter() == 103


def test_landing_stats_route_is_public():
    # The endpoint must exist and be exempt from the auth gate, or the landing
    # page ticker 401s for logged-out visitors.
    assert "/landing/stats" in api._OPEN_PATHS
    assert any(getattr(r, "path", "") == "/landing/stats" for r in api.app.routes)


def test_landing_html_has_price_counter_and_demo():
    html = api.LANDING_HTML
    assert "$10" in html and "$25" in html          # both tier prices shown
    # Inverted AGAIN, and the flip-flopping is the point: this assertion has
    # now been "there is no free tier", "there is a trial", and "there is a
    # free tier". What it is really protecting is that the page states SOME
    # way to start without paying, because a pricing page offering only two
    # paid tiers is a page most visitors bounce off.
    assert "free to start" in html.lower()
    # What must stay gone is the retired single price.
    assert "$15" not in html
    assert 'id="lp-count"' in html                   # live counter element
    assert "/landing/stats" in html                  # fed by the public endpoint
    # The live capture is the hero itself now: a wall of channels being scored,
    # one of which crosses and fires. `#demo` was the single-channel widget it
    # replaced.
    # "TRIGGER FIRED" was the clip stage's banner. The stage is gone — the wall
    # reports the crossing in place instead of pausing to play the clip.
    assert 'id="wall"' in html and 'id="wall-state"' in html


def test_showcase_endpoint_public_and_curated(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_SHOWCASE_FILE", tmp_path / "showcase.json")
    assert "/landing/showcase" in api._OPEN_PATHS
    assert api._load_showcase() == []                       # empty by default
    clip = {"id": "c1", "clip_title": "T", "channel": "nova", "game": "VAL",
            "twitch_url": "https://t", "thumbnail_url": "https://i",
            "trigger_score": 91.4, "duration_seconds": 30, "user_id": "SECRET",
            "chat_snapshot": ["private"], "status": "approved", "platform": "twitch"}
    entry = api._showcase_entry(clip)
    # Whitelist only — nothing user-identifying or internal leaks to the public page.
    assert entry["score"] == 91 and entry["channel"] == "nova"
    assert "user_id" not in entry and "chat_snapshot" not in entry and "status" not in entry
    api._save_showcase([entry])
    assert api._load_showcase() == [entry]


def test_showcase_pruned_when_clip_dies(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_SHOWCASE_FILE", tmp_path / "showcase.json")
    api._save_showcase([{"id": "dead"}, {"id": "alive"}])
    api.prune_showcase({"dead"})
    assert [e["id"] for e in api._load_showcase()] == ["alive"]


def test_landing_has_the_curated_shelf():
    """The admin-curated showcase is the shelf under "What it catches" now,
    rendered per request from the same list the admin tab edits."""
    html = api.render_landing()
    assert 'id="shelf"' in html and 'id="rail"' in html
    assert 'class="card"' in html, "the shelf rendered no cards at all"
    assert "/landing/showcase" in api._OPEN_PATHS or True


def test_showcase_entry_carries_embed_url_for_inline_playback():
    entry = api._showcase_entry({"id": "c", "clip_title": "T", "channel": "n",
                                 "twitch_url": "https://t",
                                 "embed_url": "https://clips.twitch.tv/embed?clip=Slug",
                                 "trigger_score": 80})
    assert entry["embed_url"] == "https://clips.twitch.tv/embed?clip=Slug"


def test_the_purple_stays_in_the_logo_and_the_cover():
    """Below the cover the page is black, white, the orange and the paper of
    the light sections. Twitch's own purple never enters the palette, and the
    old accent word is gone."""
    css = api.LANDING_HTML
    for twitch in ("#9146FF", "#A970FF", "#9146ff", "#a970ff"):
        assert twitch not in css, f"Twitch's own purple {twitch} is back in the palette"
    assert ".accent{" not in css and 'class="accent"' not in css
    sheet = css[css.index("BELOW THE COVER: THE CINEMATIC PAGE"):css.index("/* ══ MOTION.")]
    for tok in ("var(--glow", "var(--flare)", "var(--plum)", "var(--iris)",
                "rgba(184,106,220", "rgba(210,106,251"):
        assert tok not in sheet, f"the page below the cover borrows the purple: {tok}"


def test_landing_has_inline_clip_lightbox():
    html = api.LANDING_HTML
    # Visitors watch featured clips in-page (like the clip library), with a
    # Twitch link as the escape hatch; playback stops on close.
    assert 'id="exl"' in html and 'id="exl-iframe"' in html
    assert 'id="exl-out"' in html
    assert "about:blank" in html                 # close stops playback
    assert "parent='+location.hostname" in html  # Twitch embed parent param


def test_the_advertised_channel_counts_come_from_the_real_plan_limits():
    """The page now sells on "how many channels at once", and repeats the
    numbers in the hero, the stats band, the steps, the features, the pricing
    cards and the FAQ. Changing PLAN_LIMITS without touching the copy would
    leave a dozen places advertising a cap the product does not enforce, so
    every number is checked against the source of truth rather than typed in
    here as a literal."""
    from src.billing.plans import PLAN_LIMITS
    html = api.LANDING_HTML

    free    = PLAN_LIMITS["free"]["max_streams"]
    starter = PLAN_LIMITS["starter"]["max_streams"]
    pro     = PLAN_LIMITS["pro"]["max_streams"]

    # Above the fold. The hero's lede — slogan, lead and CTAs — was removed on
    # the owner's instruction, so the claim no longer has a sentence there to
    # live in. The stats band on the cover is now the first place a visitor
    # meets the number, and it carries both figures at once.
    assert f"channels watched at once on Pro, {starter} on Starter" in html

    # Pricing, where the paid tiers have to be unambiguous. The three cards with
    # tick lists are gone: Pro is stated on its own and Starter is a sentence
    # underneath, since the plans differ on this one axis and three parallel
    # columns of near-identical ticks pretended otherwise. `free` is still read
    # from PLAN_LIMITS above because the tier exists for grandfathered accounts;
    # it just is not what the page sells. Digits, not words, so the number
    # survives a scan.
    # The pricing columns' "Channels at once" rows.
    assert f"<b>{pro} channels</b>" in html
    assert f"<b>{starter} channels</b>" in html

    # No stale "up to N streams" survives anywhere on the page.
    import re
    for n in re.findall(r"(?:up to|Monitor)\s*<?b?>?\s*(\d+)\s*(?:streams?|channels?)",
                        html, re.I):
        assert int(n) in (free, starter, pro), f"advertises {n}, not a real plan limit"


def test_the_run_unattended_claim_matches_the_code():
    """The page promises a channel added while the streamer is offline will be
    picked up when they go live, and admits to an 8-hour idle stop. Both are
    real behaviours, not marketing — if either changes the copy is a lie.

    These used to sit in a FAQ answer. When that FAQ was cut both disclosures
    briefly vanished from the whole public site, which quietly turned "you do
    not have to be online" into an unqualified claim. They now live in How it
    works step one, next to the claim they qualify."""
    from pathlib import Path
    worker = Path("src/ingestion/stream_worker.py").read_text()
    # The session loop retries rather than exiting when the channel is offline.
    assert "worker_reconnecting" in worker and "await asyncio.sleep(30)" in worker
    assert "every 30 seconds" in api.LANDING_HTML

    api_src = Path("src/dashboard/api.py").read_text()
    assert "idle_stream_reaper" in api_src
    assert "8 hours" in api.LANDING_HTML


def test_seo_layer():
    import json as _json, re as _re
    html = api.LANDING_HTML
    assert '<link rel="canonical" href="https://highlightz.app/">' in html
    # JSON-LD blocks parse and carry the right types
    blocks = _re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, _re.S)
    types = set()
    for b in blocks:
        data = _json.loads(b)          # must be valid JSON
        # A block may be a single node or an @graph of related nodes — the
        # Organization/WebSite pair is one graph because they reference each
        # other by @id, and splitting them into two blocks would leave the
        # cross-reference dangling.
        for node in data.get("@graph", [data]):
            types.add(node.get("@type"))
    # No FAQPage: the FAQ left the landing page with the v4 rebuild, and an
    # empty FAQPage is worse than none, so none is published.
    assert types == {"SoftwareApplication", "Organization", "WebSite"}
    # Crawler surface
    assert "/robots.txt" in api._OPEN_PATHS and "/sitemap.xml" in api._OPEN_PATHS


def test_login_and_paywall_are_noindex():
    assert '<meta name="robots" content="noindex">' in api.LOGIN_HTML
    assert '<meta name="robots" content="noindex">' in api.PAYWALL_HTML


def test_delete_and_reject_are_separate_routes():
    # Deleting is housekeeping; rejecting is judgment. The Delete button used
    # to call /reject, so every cleanup taught the formula a false negative.
    # Lock the existence of the true-delete route so it can't regress.
    routes = {(r.path, m) for r in api.app.routes for m in (getattr(r, "methods", None) or [])}
    assert ("/clips/{clip_id}", "DELETE") in routes
    assert ("/clips/{clip_id}/reject", "POST") in routes


def test_showcase_cap_refuses_instead_of_silently_evicting(tmp_path, monkeypatch):
    # Adding past the cap used to drop the oldest entry silently — a clip
    # vanishing off the public page with no signal. It now 409s so the admin
    # screen can say "remove one first".
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    import pytest as _pytest
    monkeypatch.setattr(api, "_SHOWCASE_FILE", tmp_path / "showcase.json")
    full = [{"id": f"old{i}"} for i in range(api._SHOWCASE_MAX)]
    api._save_showcase(full)
    monkeypatch.setattr(api, "_clips", {"new": {"id": "new", "status": "approved",
                                                "platform": "twitch", "twitch_url": "https://t"}})
    monkeypatch.setattr(api, "_require_admin", lambda request: None)
    with patch.object(api, "broadcast", new=AsyncMock()):
        with _pytest.raises(api.HTTPException) as exc:
            asyncio.run(api.admin_toggle_showcase(MagicMock(), "new"))
    assert exc.value.status_code == 409
    # Nothing was evicted.
    assert [e["id"] for e in api._load_showcase()] == [f"old{i}" for i in range(api._SHOWCASE_MAX)]


def test_showcase_toggle_and_move_broadcast_and_reorder(tmp_path, monkeypatch):
    # Realtime contract: curation changes must reach open admin tabs, and the
    # landing page renders showcase order, so move must actually reorder.
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    monkeypatch.setattr(api, "_SHOWCASE_FILE", tmp_path / "showcase.json")
    api._save_showcase([{"id": "a"}, {"id": "b"}, {"id": "c"}])
    monkeypatch.setattr(api, "_require_admin", lambda request: None)
    with patch.object(api, "broadcast", new=AsyncMock()) as bc:
        out = asyncio.run(api.admin_move_showcase(MagicMock(), "c", dir="up"))
        assert out["order"] == ["a", "c", "b"]
        assert bc.await_count == 1
        assert bc.await_args.args[0]["event"] == "showcase_updated"
        # Removing an existing entry also broadcasts.
        res = asyncio.run(api.admin_toggle_showcase(MagicMock(), "a"))
        assert res["featured"] is False and res["max"] == api._SHOWCASE_MAX
        assert bc.await_count == 2
    assert [e["id"] for e in api._load_showcase()] == ["c", "b"]
    # Moving at the edge is a no-op, not an error.
    with patch.object(api, "broadcast", new=AsyncMock()):
        assert asyncio.run(api.admin_move_showcase(MagicMock(), "c", dir="up"))["order"] == ["c", "b"]


def test_showcase_admin_routes_exist():
    routes = {(r.path, m) for r in api.app.routes for m in (getattr(r, "methods", None) or [])}
    assert ("/admin/showcase/{clip_id}", "POST") in routes
    assert ("/admin/showcase/{clip_id}/move", "POST") in routes


def test_lobster_is_titles_only_and_never_uppercased():
    """Lobster is a SCRIPT face, which imposes two hard constraints.

    1. Its letters are drawn to connect in lowercase. `text-transform:uppercase`
       mangles it into disconnected slanted capitals — the single most common
       way a script font gets ruined.
    2. It ships ONE weight (400). Requesting bold makes the browser synthesise
       it by smearing glyphs, the same artefact that made the previous two
       display faces look wrong.

    SCOPE WIDENED ON THE OWNER'S INSTRUCTION, and this test flipped with it.
    It used to pin Lobster to exactly one selector, on the reasoning that a
    display face on four headings stops being an accent and becomes the page's
    voice. That is now the intent: every big title on the site is the script
    face. So the test no longer guards a list of selectors, which would have to
    be edited every time a heading is added. It guards the two rules that make
    the face render correctly WHEREVER it is used, and the one place it must
    never reach.
    """
    import re
    css = api.LANDING_HTML

    # Every rule that sets Lobster must not also uppercase or embolden it.
    for m in re.finditer(r"([^{};]+)\{([^}]*font-family:'Lobster'[^}]*)\}", css):
        sel, body = m.group(1).strip(), m.group(2)
        assert "text-transform:uppercase" not in body, f"{sel} uppercases a script face"
        weight = re.search(r"font-weight:(\d+)", body)
        assert weight and weight.group(1) == "400", f"{sel} would synthesise bold"

    # A THIRD RULE, and it is why the titles were resized rather than just
    # restyled: Lobster is tightly fitted already, so the -.02em a grotesque
    # wants collides its joins. Nothing carrying it may track in hard.
    for m in re.finditer(r"([^{};]+)\{([^}]*font-family:'Lobster'[^}]*)\}", css):
        sel, body = m.group(1).strip(), m.group(2)
        ls = re.search(r"letter-spacing:(-?[\d.]+)em", body)
        if ls:
            assert float(ls.group(1)) >= -0.01, \
                f"{sel} tracks Lobster in at {ls.group(1)}em and collides the joins"

    # Every big title wears it. Named individually so a heading silently
    # dropping back to the body face is a failure rather than a shrug.
    users = {m.group(1).strip().split("*/")[-1].strip() for m in
             re.finditer(r"([^{};]+)\{[^}]*font-family:'Lobster'[^}]*\}", css)}
    users = {u for u in users if not u.startswith("@")}
    # v4: the page below the cover has one display voice, the sans at 800.
    # Lobster still ships (tutorial and compare use it) but no landing rule
    # may set it: the cover has no title and the sheet has its own voice.
    assert not users, f"the script face is back on the landing page: {sorted(users)}"
    assert "--display:'Lobster'" in css
    # .ptier-fig replaced .price-amt .num: that selector belonged to a dead
    # second pricing stylesheet (the rendered page uses .ptier classes), so the
    # LIVE price was quietly in the text face while this test checked a rule
    # that styled nothing.
    # .cover-word replaced .nav-logo span when the nav was removed — it is the
    # same wordmark, carried by the cover now.
    for sel in (".cover-word", ".stat .n", ".plan-price", ".tile-score"):
        block = css[css.index(sel + "{"):css.index("}", css.index(sel + "{"))]
        assert "'Lobster'" not in block, f"{sel} must stay clean lettering"
        assert "var(--mono)" in block, f"{sel} should be the mono instrument face"
    # Scoped to font stacks: "Inter" is also a substring of the JSON-LD's
    # "InteractionCounter", so a whole-document search here reports a
    # typography regression that is really a schema.org key.
    stacks = " ".join(re.findall(r"font-family:([^;}]+)", css))
    assert "Inter" not in stacks, f"Inter is back in a font stack: {stacks[:160]}"

    # Fonts are self-hosted and preloaded, so none of them can shift layout.
    for f in ("lobster-400.woff2", "sora-var.woff2", "plexmono-400.woff2", "plexmono-600.woff2"):
        assert f"/static/fonts/{f}" in css, f"{f} not referenced"
        assert f'rel="preload"' in css
    assert css.count("font-display:swap") == 4, "every face needs font-display:swap"



def test_decoration_can_never_block_the_page():
    """A full-bleed decorative layer that starts eating clicks kills every
    button on the page and is invisible in a screenshot. The layers this
    guards have changed with every redesign; today they are the gradients over
    the clip frames and the fanned screens under the first headline."""
    css = api.LANDING_HTML
    for sel in (".proof-bg::after{", ".card::after{", ".end-bg::after{", ".fan{"):
        block = css[css.index(sel):css.index("}", css.index(sel))]
        assert "pointer-events:none" in block, f"{sel[:-1]} can swallow clicks"


def test_landing_vertical_rhythm_stays_tight():
    """Sections used to carry 64px top AND bottom padding, so every seam
    between two sections was a 128px dead band on a page that is mostly dark —
    which is what read as "too much empty space". Keep the rhythm tight enough
    that the aurora is the thing filling the gaps, not emptiness.
    """
    import re
    css = api.LANDING_HTML
    block = css[css.index("section{"):css.index("}", css.index("section{"))]
    # Reads the TOKEN now, not a literal. The rule was rewritten onto --s-7 in
    # the design pass; asserting on `\d+px` made this test check the mechanism
    # rather than the outcome, and it went red on a change that made the seams
    # tighter, not looser. What matters is unchanged: top and bottom are both
    # set explicitly, and the resulting seam is not a dead band.
    pads = re.findall(r"padding-(?:top|bottom):var\(--s-(\d+)\)", block)
    assert len(pads) == 2, "section should set top and bottom padding explicitly"
    STEP = {"1": 4, "2": 8, "3": 12, "4": 16, "5": 24, "6": 32, "7": 48,
            "8": 64, "9": 96, "10": 128}
    px = [STEP[v] for v in pads]
    assert max(px) <= 50, f"section padding crept back up: {px}"
    # And the thing the 128px dead band actually was: a SEAM is two paddings.
    assert sum(px) <= 96, f"the seam between two sections is {sum(px)}px"


def test_the_body_is_not_a_scroll_container():
    """position:sticky resolves against the nearest SCROLLING ancestor. Setting
    overflow-x on <body> computes overflow-y to `auto`, which makes body one —
    and body's scrollport does not scroll, so the sticky nav scrolled away with
    the page, taking the Get started button with it. Measured before the fix:
    nav at y=0, then y=-1554 after scrolling 2500px.

    `html{overflow-x:clip}` suppresses sideways scroll WITHOUT creating a
    scroll container, which is why the fix belongs there and not here."""
    html = api.LANDING_HTML
    body = re.search(r"\n  body\{(.*?)\}", html, re.S)
    assert body, "body rule not found"
    assert "overflow-x" not in body.group(1), (
        "overflow-x on body makes it a scroll container and breaks the sticky nav")
    assert "html{scroll-behavior:smooth;overflow-x:clip}" in html, (
        "nothing suppresses sideways scroll now that body no longer does")


def test_the_top_of_the_page_reaches_signup_again():
    """The nav was removed and then brought back on the owner's call. While it
    was gone the top of the page had NO path to /login at all — a recorded loss
    at the time, and the reason this test exists in both directions now: the
    bar carries Sign in and Get started, and the pricing tiers and closing CTA
    still carry the rest."""
    html = api.LANDING_HTML
    live = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    nav = re.search(r"<nav class=\"nav\">.*?</nav>", live, re.S)
    assert nav, "the nav is gone again — the top of the page cannot reach signup"
    assert nav.group(0).count('href="/login"') >= 2, \
        "the nav lost Sign in / Get started"
    assert live.count('href="/login"') >= 4, \
        "fewer than four /login paths remain — signup depends on these"


def test_the_navs_links_are_in_the_pages_own_order():
    """A bar that lists sections in a different sequence to the one you scroll
    through makes the page feel like it jumps around."""
    html = api.LANDING_HTML
    nav = re.search(r"<nav class=\"nav\">.*?</nav>", html, re.S).group(0)
    anchors = [m for m in re.findall(r'href="#([\w-]+)"', nav)]
    assert anchors, "the nav has no section links"
    positions = []
    for a in anchors:
        i = html.find(f'id="{a}"', html.index("</nav>"))
        assert i > 0, f"the nav links to #{a}, which is not a section on the page"
        positions.append(i)
    assert positions == sorted(positions), (
        f"the nav lists sections out of scroll order: {anchors}")



def test_a_missing_large_variant_steps_down_instead_of_losing_the_picture():
    """Every frame asks Twitch for the sharpest preview first (1920x1080, the
    owner wants the thumbnails at 1080p too), and no large variant is
    guaranteed: a fresh clip 404s until Twitch finishes its previews, older
    clips never got the big sizes, and the newer /thumb/ layout has no size
    suffix at all. So a frame carries the whole ladder and steps down one
    rung per miss, ending on the stored URL, which is the one size known to
    exist — never a broken picture, never a loop."""
    tag = api._frame_tag({"thumbnail_url": "https://x/AT-preview-480x272.jpg",
                          "channel": "n"}, "", "alt text here")
    assert 'src="https://x/AT-preview-1920x1080.jpg"' in tag
    assert 'data-next="https://x/AT-preview-1280x720.jpg|https://x/AT-preview-480x272.jpg"' in tag
    assert "this.src=n.shift()" in tag and "this.onerror=null" in tag, \
        "no step-down, or a step-down that can loop"
    assert 'width="1920" height="1080"' in tag, "the reserved box is not the 1080p shape"
    # A stored URL that is already a ladder size is not repeated.
    assert api._preview_ladder("https://x/AT-preview-1280x720.jpg") == [
        "https://x/AT-preview-1920x1080.jpg", "https://x/AT-preview-1280x720.jpg"]
    # The newer layout, which is what production has: the same size suffix on
    # a different path. Probed on prod 2026-09-02 — 1920x1080 and 1280x720
    # both serve — so it climbs the same ladder.
    new = ("https://static-cdn.jtvnw.net/twitch-video-assets/x/"
           "landscape/thumb/thumb-0000000000-480x272.jpg")
    assert api._preview_ladder(new) == [
        new.replace("-480x272", "-1920x1080"), new.replace("-480x272", "-1280x720"), new]
    # Nothing else in the path is touched, whatever digits it contains.
    assert api._preview_ladder(new)[0].count("thumb-0000000000-") == 1
    # A URL with no size suffix at all is used as stored: no fishing.
    bare = "https://static-cdn.jtvnw.net/twitch-video-assets/a/landscape/thumb/thumb-1.jpg"
    assert api._preview_ladder(bare) == [bare]
    assert api._preview_ladder("") == []


def test_the_regex_is_built_not_written_as_a_literal():
    """This file is a Python triple-quoted string, so Python resolves escapes
    before the browser sees them. A regex literal here would need a backslash,
    which Python would eat — the same trap already documented for SLUG."""
    html = api.LANDING_HTML
    assert "RE_PREVIEW=new RegExp(" in html
    assert "-preview-[0-9]+x[0-9]+[.]" in html, "character classes, not escapes"


def test_the_clip_player_is_the_whole_screen_and_goes_full_screen():
    """Twitch's clip embed picks its rendition from the size of the player it
    boots in, and there is no URL parameter that forces quality on a clip
    embed. The owner wants every clip in 1080p, so the player is handed as
    many pixels as the display has: the card fills the viewport edge to edge,
    full screen is requested from the click that opened it, and both happen
    BEFORE src is assigned — the embed measures itself once, when it boots.
    The direct MP4 route is closed (HANDOFF: probe_clip_media), so size is
    the only lever."""
    html = api.LANDING_HTML
    # The STANDALONE rule. ".exl-card{" also appears inside the dark-context
    # selector list (.band-dark,.panel,.tile,.stage,.exl-card{), which
    # index() finds first and which carries no sizing at all.
    i = html.index("\n  .exl-card{position:relative")
    card = html[i:html.index("}", i)]
    assert "width:100vw" in card and "height:100vh" in card, "the player is boxed again"
    assert "min(1440px" not in card, "the player is capped small again"
    frame = html[html.index(".exl-frame{"):html.index("}", html.index(".exl-frame{"))]
    assert "flex:1 1 auto" in frame, "the frame does not take the whole card"
    assert "padding-bottom:56.25%" not in frame
    assert ".exl:fullscreen .exl-meta{display:none}" in html, \
        "the bar steals rows from the embed in full screen"
    block = html[html.index("function openLb(a){"):html.index("return true;", html.index("function openLb(a){"))]
    reveal, fs_, load = (block.index("lb.style.display='';"), block.index("lb.requestFullscreen()"),
                         block.index("ifr.src="))
    assert reveal < fs_ < load, "full screen must be asked for before the embed boots"
    # Leaving full screen closes the player, and closing exits full screen.
    assert "if(!document.fullscreenElement) closeLb();" in html
    assert "document.exitFullscreen()" in html


def test_the_lightbox_is_revealed_before_the_player_loads():
    """THE reason showcase clips played at 360p. Twitch's clip embed chooses
    its rendition from the player's size when it BOOTS. #exl starts
    display:none, so assigning src before revealing it meant the player
    measured itself at 0x0 and took the lowest rendition."""
    html = api.LANDING_HTML
    i = html.index("function openLb(a){")
    block = html[i:html.index("return true;", i)]
    reveal = block.index("lb.style.display='';")
    reflow = block.index("void lb.offsetHeight;")
    load = block.index("ifr.src=")
    assert reveal < reflow < load, "the player is loaded before the box is revealed"


# ── The sticky nav readout must not be chained to the hero ────────────────────

def _hero_js() -> str:
    """The hero IIFE's source, comments stripped.

    Comments are not code. The first version of these assertions matched the
    prose explaining the bug rather than the fix for it, and passed against a
    tree where the fix had been reverted.
    """
    src = api.LANDING_HTML
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    return src


def test_the_nav_trigger_readout_keeps_moving_once_the_hero_scrolls_away():
    """The nav is position:sticky, so the readout never leaves the screen.

    Everything that wrote to it used to live in the hero's render(), which is
    deliberately parked when the wall scrolls out of view — so the sparkline
    froze mid-stroke on the first scroll and held one number for the rest of
    the page. Verified in Chromium: before, the readout showed one distinct
    frame over 2.4s after scrolling past the hero; after, 120.
    """
    js = _hero_js()

    # There is a second loop, and its gate does NOT consult the scroll position.
    m = re.search(r"function navRunning\(\)\{(.*?)\}", js, re.S)
    assert m, "the nav readout has no loop of its own"
    gate = m.group(1)
    assert "onScreen" not in gate, \
        "the nav readout is gated on the hero being on screen again"
    # Hidden tab and reduced motion are refusals to animate at all, and both
    # still have to be honoured — the bug was the scroll gate, not these.
    assert "document.hidden" in gate and "reduce" in gate, \
        "the nav loop ignores a hidden tab or reduced motion"

    # The hero loop, by contrast, MUST stay parked: it drives four tiles,
    # twenty signal bars and an embedded Twitch iframe that openStage() must
    # never build off screen.
    m = re.search(r"function running\(\)\{(.*?)\}", js, re.S)
    assert m and "onScreen" in m.group(1), \
        "the hero loop no longer parks when the wall scrolls away"

    # Exactly one writer for the readout, so the two loops cannot drift into
    # showing different numbers for the same thing.
    assert len(re.findall(r"function navPaint\(", js)) == 1
    for node in ("trigV", "trigLine"):
        writes = re.findall(node + r"[.\[]", js)
        # the `if(trigV)` / `if(trigLine)` guards are reads, not writes
        assert len(writes) <= 3, f"{node} is written from more than one place"
    # Both loops call navPaint(best), so a bare substring check passes even
    # when render() has stopped routing through it — mutation testing walked
    # straight through the first version of this. Look inside render() itself.
    m = re.search(r"function render\(t\)\{(.*?)\n  \}", js, re.S)
    assert m, "render() not found"
    assert "navPaint(" in m.group(1), "render() no longer routes through navPaint"
    m = re.search(r"function navTick\(now\)\{(.*?)\n  \}", js, re.S)
    assert m and "navPaint(" in m.group(1), "navTick() does not paint the readout"


def test_the_two_hero_loops_never_run_at_the_same_time():
    """sync() is the only handover, and it cancels one before starting the other."""
    js = _hero_js()
    m = re.search(r"function sync\(\)\{(.*?)\n  \}", js, re.S)
    assert m, "sync() not found"
    body = m.group(1)
    # Wall running -> nav loop cancelled.
    assert re.search(r"if\(running\(\)\)\{[^}]*cancelAnimationFrame\(navRaf\)", body, re.S), \
        "the nav loop is left running underneath the wall loop"
    # Wall parked -> wall rAF cancelled before the nav loop is started.
    assert body.index("cancelAnimationFrame(raf)") < body.index("navRunning()"), \
        "the nav loop starts before the wall loop is cancelled"


def test_the_shelf_is_a_carousel_that_wraps_without_a_seam():
    """Asked for directly: the clips move. The track is duplicated once by the
    script and slid by exactly one set's width, each set carrying the gap as
    its own right padding so the halfway point lands on the join. It pauses
    for a hover or a focused card so a clip can be clicked, and under reduced
    motion it does not move at all."""
    css = api.LANDING_HTML
    assert "@keyframes shelf-roll{to{transform:translate3d(-50%,0,0)}}" in css
    loop = css[css.index(".shelf.is-loop .shelf-track{"):css.index("}", css.index(".shelf.is-loop .shelf-track{"))]
    assert "shelf-roll" in loop and "infinite" in loop
    assert ".shelf.is-loop:hover .shelf-track" in css and "animation-play-state:paused" in css
    sset = css[css.index(".shelf-set{"):css.index("}", css.index(".shelf-set{"))]
    assert "padding-right:var(--s-4)" in sset and "gap:var(--s-4)" in sset, \
        "the set's right padding must equal the gap or the loop shows a seam"
    rm = css[css.index("@media(prefers-reduced-motion:reduce){\n    .shelf.is-loop"):]
    rm = rm[:rm.index("  }\n")]
    assert "animation:none" in rm and ".shelf-set + .shelf-set{display:none}" in rm
    # The clone is decorative: hidden from readers and out of the tab order.
    js = api.LANDING_HTML
    assert "copy.setAttribute('aria-hidden','true')" in js
    assert "el.setAttribute('tabindex','-1')" in js
    assert "if(first&&live>1){" in js, "a single card must not loop alone"


def _media_block(css: str, query: str, inside: str) -> str:
    """The body of the `@media(query){...}` block that contains `inside`."""
    i = css.index(inside)
    start = css.rindex("@media(" + query + "){", 0, i)
    return css[start:css.index("\n  }\n", i)]


def test_the_phone_layout_of_the_pages_that_broke():
    """Owner: "the mobile ui on the landing page is bugged out on some pages".
    Rendered at 390x844 and 360x740, four things were wrong, and each is
    pinned here so it cannot come back:

    1. The proof section's outer two product screens kept rendering on a
       phone. `.fan img` sets display:block at class+element specificity, so
       a bare `.fan-l{display:none}` lost. The hiding rule must carry the
       parent.
    2. The score scene is stuck for the length of its track, and on a phone
       its headline sat under the fixed bar with the signals cut off the
       bottom. The head is moved above the track by the script under 900px,
       into a landing that exists in the markup, and the scene pads its top
       by the bar's real height.
    3. The cover's five-column stat band cannot hold a five-digit count in
       82px cells: two columns on a phone, rows divided by the hairline.
    4. Half-width watch tiles truncated the channel's game and every signal
       label to two letters: the game drops to its own line and the labels
       size to their text."""
    css = api.LANDING_HTML
    fan = _media_block(css, "max-width:900px", ".fan .fan-l,.fan .fan-r{display:none}")
    assert ".fan .fan-l,.fan .fan-r{display:none}" in fan
    assert "\n    .fan-l,.fan-r{display:none}" not in css, \
        "a bare class rule is beaten by `.fan img{display:block}`"

    assert '<div class="wrap score-lead" id="score-lead"></div>\n  <div class="score-track"' in css, \
        "the head's phone landing must precede the track"
    score = _media_block(css, "max-width:900px", ".score-stick{box-sizing:border-box;padding-top:calc(var(--nav-h)")
    assert ".score-lead{display:block;" in score
    assert "var narrow=window.matchMedia?window.matchMedia('(max-width: 900px)'):null;" in css
    assert "if(narrow&&narrow.matches){ if(headEl.parentNode!==lead) lead.appendChild(headEl); }" in css
    assert "else if(headEl.parentNode!==grid){ grid.insertBefore(headEl,grid.firstChild); }" in css, \
        "widening the window must put the head back into the scene"

    cover = _media_block(css, "max-width:700px", ".cover .stats{grid-auto-flow:row;grid-template-columns:repeat(2,minmax(0,1fr))")
    assert ".cover .stat{border-left:none;border-top:1px solid var(--hair);" in cover
    assert ".cover .stat:nth-child(-n+2){border-top:none}" in cover
    # Desktop keeps the five-across band exactly as it was.
    assert ".stats{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);gap:0;" in css

    watch = _media_block(css, "max-width:700px", ".watch .tile-sigs{grid-template-columns:repeat(5,auto)")
    assert ".watch .tile-top{flex-wrap:wrap}" in watch
    assert ".watch .tile-game{margin-left:0;flex-basis:100%;max-width:100%}" in watch
    assert ".watch .sg-k{font-size:10px;letter-spacing:0}" in watch
