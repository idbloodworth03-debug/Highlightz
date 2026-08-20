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
    # A self-serve trial exists again, so the page SHOULD advertise free days.
    # This assertion was the exact inverse while the free tier was the offer.
    assert "7 days free" in html.lower() or "days free" in html.lower()
    # What must stay gone is the retired single price.
    assert "$15" not in html
    assert 'id="lp-count"' in html                   # live counter element
    assert "/landing/stats" in html                  # fed by the public endpoint
    assert 'id="demo"' in html and "TRIGGER FIRED" in html   # live capture demo


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


def test_landing_has_examples_section():
    html = api.LANDING_HTML
    assert 'id="examples"' in html and 'id="ex-grid"' in html
    assert "/landing/showcase" in html
    assert 'id="nav-examples"' in html          # nav tab, revealed when data exists


def test_showcase_entry_carries_embed_url_for_inline_playback():
    entry = api._showcase_entry({"id": "c", "clip_title": "T", "channel": "n",
                                 "twitch_url": "https://t",
                                 "embed_url": "https://clips.twitch.tv/embed?clip=Slug",
                                 "trigger_score": 80})
    assert entry["embed_url"] == "https://clips.twitch.tv/embed?clip=Slug"


def test_accent_word_is_solid_not_outlined():
    """The highlighted word in a title is a SOLID purple fill.

    It used to be transparent with a -webkit-text-stroke outline. Two things
    have to stay true or it silently reverts to that look: the fill must not be
    transparent, and no stroke width may come back.

    The hex is the redesign's --glow (#B86ADC, H281 S62), deliberately off
    Twitch's own #9146FF/#A970FF (H264 S100) — pinned here because "our purple,
    not Twitch's" is the one colour decision a later edit is most likely to undo
    by reaching for a familiar value.
    """
    # Excludes the captured product stylesheet: see the note in
    # test_landing_palette.py. The dashboard's own Twitch-purple button is not
    # this page's palette.
    css = re.sub(r"<style data-product-capture>.*?</style>", "",
                 api.LANDING_HTML, flags=re.S)
    block = css[css.index(".accent{"):css.index("}", css.index(".accent{"))]
    # #B86ADC — 5.35:1 on the charcoal page base. The page briefly went light,
    # which forced a darker plum (#6A2E8A) because the glow only managed 3.15
    # against bone; on charcoal the glow is both readable and the right look,
    # so it is back. What has not changed across either theme is the decision
    # this test exists to protect: ours, not Twitch's.
    assert "color:#B86ADC" in block, "accent must be filled with our purple"
    assert "color:transparent" not in block
    assert "-webkit-text-stroke:0" in block
    for twitch in ("#9146FF", "#A970FF", "#9146ff", "#a970ff"):
        assert twitch not in css, f"Twitch's own purple {twitch} is back in the palette"
    # The old class name described the opposite behaviour and is fully gone.
    assert ".hollow" not in css and 'class="hollow"' not in css
    # Both accented words still exist in the markup.
    assert css.count('class="accent"') == 2


def test_landing_has_inline_clip_lightbox():
    html = api.LANDING_HTML
    # Visitors watch featured clips in-page (like the clip library), with a
    # Twitch link as the escape hatch; playback stops on close.
    assert 'id="exl"' in html and 'id="exl-iframe"' in html
    assert 'id="exl-out"' in html
    assert "about:blank" in html                 # close stops playback
    assert "parent='+location.hostname" in html  # Twitch embed parent param


def test_landing_faq_answers_what_clippers_ask_first():
    html = api.LANDING_HTML
    assert 'id="faq"' in html
    # CUT FROM 12 TO 7, and opened up. Thirteen accordions is the shape of a
    # generated page; the five that went are the ones that restate the steps or
    # answer a question nobody asks before signing up (clip length, other
    # platforms, how the scoring works). They live in the walkthrough now, which
    # the section links to.
    assert html.count('class="faq-item"') == 7
    # Dropdowns are deliberate — see test_the_faq_is_a_short_grouped_accordion.
    # What this test guards is the COUNT and which questions survived the cut.
    # A few key answers exist and stay honest
    assert "Is this AI?" in html and "transparent mathematical formula" in html
    assert "How does billing work?" in html and "$10/month" in html and "$25/month" in html
    # The objections a clipper actually has before paying. Order matters: the
    # first thing they want to know is whether they may clip other people.
    order = [html.index(q) for q in ("Can I clip channels I don't own?",
                                     "Does it work for small channels?",
                                     "Is this allowed on Twitch?")]
    assert order == sorted(order)
    assert "/tutorial" in html, "the cut questions are not linked anywhere"


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

    # Above the fold, in the three places a visitor cannot miss. The h1 is the
    # brand slogan and deliberately carries no number — so the hero has to say
    # it in the lead, or the page's main claim lives only in the pricing table.
    assert "Never miss a <span class=\"accent\">highlight</span> again." in html
    assert f"Up to {pro} channels at the same time on Pro." in html   # hero lead
    assert f"Up to {pro} channels at once" in html                    # hero tag
    assert f"channels watched at once on Pro, {starter} on Starter" in html

    # Pricing, where the paid tiers have to be unambiguous. The three cards with
    # tick lists are gone: Pro is stated on its own and Starter is a sentence
    # underneath, since the plans differ on this one axis and three parallel
    # columns of near-identical ticks pretended otherwise. `free` is still read
    # from PLAN_LIMITS above because the tier exists for grandfathered accounts;
    # it just is not what the page sells. Digits, not words, so the number
    # survives a scan.
    assert f"<b>{pro} channels</b> watched at the same time" in html
    assert f"<b>{starter} channels</b> watched at the same time" in html

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
        types.add(data.get("@type"))
    assert types == {"SoftwareApplication", "FAQPage"}
    faq = [_json.loads(b) for b in blocks if _json.loads(b)["@type"] == "FAQPage"][0]
    assert all("<" not in q["acceptedAnswer"]["text"] for q in faq["mainEntity"])  # plain text
    # The schema is DERIVED from the visible FAQ, so assert they agree rather
    # than counting to a literal. Serving Google answers the page no longer
    # gives is invisible in a browser and is what structured-data penalties are
    # for; a hardcoded count would not have caught the drift, only the drift's
    # size. Every question on the page, in page order, and nothing extra.
    # Tag-agnostic, like _faq_schema itself: this pairing has now flipped
    # between <p> and <summary> twice, and each time a tag-specific pattern
    # here silently compared the schema against an empty list.
    shown = _re.findall(
        r'<(?:p|summary)[^>]*\bclass="[^"]*\bfaq-q\b[^"]*"[^>]*>(.*?)</(?:p|summary)>',
        html, _re.S)
    assert [q["name"] for q in faq["mainEntity"]] == [
        __import__("html").unescape(q).strip() for q in shown]
    assert faq["mainEntity"], "the FAQ schema is empty"
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

    Scope narrowed in the late-night-room redesign: a characterful display face
    used on four headings stops being characterful and starts being the page's
    default voice, so it now appears exactly TWICE — the h1 and the closing
    line. Section headings are Sora; every number and label is the mono.
    """
    import re
    css = api.LANDING_HTML

    # Every rule that sets Lobster must not also uppercase or embolden it.
    for m in re.finditer(r"([^{};]+)\{([^}]*font-family:'Lobster'[^}]*)\}", css):
        sel, body = m.group(1).strip(), m.group(2)
        assert "text-transform:uppercase" not in body, f"{sel} uppercases a script face"
        weight = re.search(r"font-weight:(\d+)", body)
        assert weight and weight.group(1) == "400", f"{sel} would synthesise bold"

    # Used twice, and only twice. The @font-face block declares the family
    # rather than using it, so it is not a usage site.
    users = {m.group(1).strip().split("*/")[-1].strip() for m in
             re.finditer(r"([^{};]+)\{[^}]*font-family:'Lobster'[^}]*\}", css)}
    users = {u for u in users if not u.startswith("@")}
    assert users == {".hero-copy h1", ".final h2"}, f"Lobster scope drifted: {sorted(users)}"

    # Headings are the text face; data is the instrument face. Neither is the
    # script face, and neither is Inter.
    for sel in ("h2.sec-title",):
        block = css[css.index(sel + "{"):css.index("}", css.index(sel + "{"))]
        assert "var(--sans)" in block, f"{sel} should be the body face"
    for sel in (".nav-logo span", ".stat .n", ".price-amt .num", ".demo-score span"):
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
    button on the page and is invisible in a screenshot.

    The layer this guards has changed three times now — the aurora orbs, then
    the grain, now the through-line and its section wash — and the docstring
    said each time that the invariant moves with the decoration rather than
    dying with it. So it moves again. The grain is gone (it was drawn for the
    dark palette and did nothing on bone); what is fixed and full-bleed today
    is .thread and the ::after wash on every seam.
    """
    css = api.LANDING_HTML
    assert ".grain{" not in css, "the grain came back — it is dead weight on a light page"

    thread = css[css.index(".thread{"):css.index("}", css.index(".thread{"))]
    assert "position:fixed" in thread
    assert "pointer-events:none" in thread, \
        "the through-line is fixed over the page and would swallow every click"

    wash = css[css.index(".seam::after,.wash::after{"):
               css.index("}", css.index(".seam::after,.wash::after{"))]
    assert "pointer-events:none" in wash, \
        "the section wash covers a whole band and would swallow its buttons"
    # It is decoration, so it must never be in the accessibility tree either.
    assert 'id="thread"' in css and 'aria-hidden="true"' in css



def test_landing_vertical_rhythm_stays_tight():
    """Sections used to carry 64px top AND bottom padding, so every seam
    between two sections was a 128px dead band on a page that is mostly dark —
    which is what read as "too much empty space". Keep the rhythm tight enough
    that the aurora is the thing filling the gaps, not emptiness.
    """
    import re
    css = api.LANDING_HTML
    block = css[css.index("section{"):css.index("}", css.index("section{"))]
    pads = [int(v) for v in re.findall(r"padding-(?:top|bottom):(\d+)px", block)]
    assert len(pads) == 2, "section should set top and bottom padding explicitly"
    assert max(pads) <= 50, f"section padding crept back up: {pads}"


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


def test_the_nav_collapses_before_it_can_push_the_cta_off_screen():
    """The section links used to collapse at 720px, but the full nav needs
    ~925px (logo 205 + links 349 + right group 293 + padding 44). Every width
    from 721 to ~925 pushed Sign in and Get started past the right edge, and
    body{overflow-x:hidden} clipped them away silently — so on tablets and
    small laptops the primary CTA was simply absent. Nobody would report that;
    it just looks like a nav without a button."""
    html = api.LANDING_HTML
    m = re.search(r"@media\(max-width:(\d+)px\)\{\s*\.nav-links\{display:none\}", html)
    assert m, "no breakpoint collapses .nav-links"
    assert int(m.group(1)) >= 925, (
        f".nav-links collapse at {m.group(1)}px, but the full nav needs ~925px — "
        f"between the two the Get started button is pushed off the edge")


# ── the showcase clips load at the best quality available ────────────────────

def test_the_showcase_asks_twitch_for_the_large_thumbnail():
    """Twitch stores the showcase thumbnail at ~480x272, but these cards render
    about 380x146 CSS px — 760x292 of real pixels on a 2x display. The stored
    URL was being upscaled by the browser and looked soft on every modern
    screen. Swapping the size segment asks the CDN for the 1280x720 master."""
    html = api.LANDING_HTML
    assert "-preview-1280x720." in html, "the showcase no longer requests the large variant"
    # The RESULT has to be used, not merely computed. Asserting that
    # hiResThumb(...) appears anywhere passes happily against `var hi = ...`
    # followed by `img.src = c.thumbnail_url`, which is the bug.
    assert "var hi=hiResThumb(c.thumbnail_url);" in html
    assert "img.src=hi;" in html, "the upgraded URL is computed but not used"


def test_a_missing_large_variant_steps_down_instead_of_losing_the_picture():
    """The 1280 variant is NOT guaranteed: a freshly-created clip 404s until
    Twitch finishes generating its previews, and some older clips never got the
    size at all. The original handler removed the <img> outright, so one missing
    upscale left a card with no picture — strictly worse than the soft one it
    replaced."""
    html = api.LANDING_HTML
    i = html.index("img.onerror=function(){")
    block = html[i:i + 400]
    # The GUARD, not just the strings inside it — neutering the condition to
    # if(false) leaves every one of these substrings in place while removing
    # the behaviour entirely.
    assert "if(hi!==c.thumbnail_url && img.getAttribute('data-tried')!=='1'){" in block, \
        "the step-down guard is gone or neutered"
    assert "img.src=c.thumbnail_url;" in block, "no step-down to the stored URL"
    assert "data-tried','1'" in block, "nothing stops the fallback looping on itself"


def test_the_regex_is_built_not_written_as_a_literal():
    """This file is a Python triple-quoted string, so Python resolves escapes
    before the browser sees them. A regex literal here would need a backslash,
    which Python would eat — the same trap already documented for SLUG."""
    html = api.LANDING_HTML
    assert "RE_PREVIEW=new RegExp(" in html
    assert "-preview-[0-9]+x[0-9]+[.]" in html, "character classes, not escapes"


def test_the_clip_player_is_sized_from_the_viewport_not_a_fixed_920():
    """Twitch's clip embed picks its rendition by adaptive bitrate, and player
    size is the main input — there is no URL parameter that forces quality on a
    clip embed. A fixed 920px player on a 1920 screen asks Twitch for a low
    rendition and then shows it in a box with room to spare."""
    html = api.LANDING_HTML
    # The STANDALONE rule. ".exl-card{" also appears inside the dark-context
    # selector list (.band-dark,.panel,.demo,.shot-frame,.exl-card{), which
    # index() finds first and which carries no sizing at all.
    i = html.index("\n  .exl-card{position:relative")
    card = html[i:html.index("}", i)]
    assert "min(1440px" in card, "the player is capped small again"
    assert "100vh" in card, "the player is not bounded by height — it will overflow a short screen"
    # aspect-ratio, because padding-bottom is a percentage of WIDTH and cannot
    # honour the height cap the card now carries.
    frame = html[html.index(".exl-frame{"):html.index("}", html.index(".exl-frame{"))]
    assert "aspect-ratio:16/9" in frame
    assert "padding-bottom:56.25%" not in frame, "the two sizing methods would fight"


def test_the_lightbox_is_revealed_before_the_player_loads():
    """THE reason showcase clips played at 360p.

    Twitch's clip embed chooses its rendition from the player's size when it
    BOOTS. #exl starts display:none, so assigning src before revealing it meant
    the player measured itself at 0x0, took the lowest rendition, and a
    30-second clip ended long before ABR could climb. Measured in Chromium:
    0x0 at src-assignment, 1438x809 one frame later.

    Widening the player did nothing on its own — it was never the size the
    player saw. Order is the fix, and it is invisible in a screenshot: the clip
    plays either way, just badly.
    """
    html = api.LANDING_HTML
    i = html.index("var lb=document.getElementById('exl');")
    block = html[i:i + 1400]
    reveal = block.index("lb.style.display='';")
    load = block.index("ifr.src=src+")
    assert reveal < load, \
        "the player is loaded while its container is still display:none — it will boot at 0x0"
    # A reflow between the two, or the browser may batch the style change and
    # the iframe still has no dimensions when src is assigned.
    reflow = block.index("void lb.offsetHeight;")
    assert reveal < reflow < load, "no forced layout between revealing and loading"
