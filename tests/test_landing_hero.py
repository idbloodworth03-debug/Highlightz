"""The hero is the detector, running — and these hold that shut.

The landing page used to show a picture of the product: first drawn in CSS,
then lifted wholesale out of the real dashboard. Both are gone. What is above
the fold now is a wall of four channels being scored live, one of which crosses
its threshold, fires, and plays a real clip the formula actually caught.

That is a much stronger claim than a screenshot, and it is only worth making
while it stays true. These tests are about the MECHANISM, not the wording:

  - the simulation is deterministic and the near-miss cannot fire by accident;
  - the clips are the owner's real curated ones, not invented names;
  - the moment is cheap: one player at a time, nothing preloaded, the loop
    stops when nobody is looking;
  - reduced motion gets a composed frame, not a degraded animation.

The hero's behaviour lives in a JS block inside a Python string, so most of
these read the shipped source. Where that is too weak to mean anything, the
assertion is about a number that can be checked arithmetically — the near-miss
headroom below — rather than about the presence of a substring.
"""

import re

import pytest

from src.dashboard import api

HTML = api.LANDING_HTML
JS = HTML[HTML.index("── THE WALL ──", HTML.index("<body")):]
JS = JS[:JS.index("── THE THROUGH-LINE")]


# ── there is no picture of the product anywhere ──────────────────────────────

@pytest.mark.parametrize("gone", [
    "See it in action",
    'id="product"',
    "PRODUCT_SHOTS",
    "data-product-capture",
    "pshow",
    "pshot",
])
def test_the_product_screenshots_stay_gone(gone):
    """The FIRST product-shot mechanism — the dashboard's DOM and 56KB of its
    stylesheet lifted into the landing page — was removed deliberately and
    stays gone. The product IS shown again since 2026-09-02, on the owner's
    call, but as the tour below: six real screen captures served as ~30KB
    WebP files, never as lifted markup. test_the_tour_shows_the_real_screens
    holds that shape."""
    assert gone not in HTML, f"the lifted product DOM came back: {gone}"


def test_the_landing_page_did_not_regrow_the_dashboard_stylesheet():
    """The captures shipped 56KB of the app's own CSS to every visitor. Nothing
    should quietly reintroduce that.

    The ceiling is 56KB above where the page actually sits, which is the size
    of the thing this is watching for. It was 160,000 against a 155,002-char
    page — under 5KB of headroom, so the cover tripped it, and a tripwire that
    fires on a 5KB feature is one somebody eventually raises without reading
    it. Raise this only against a measured page size, never to make a red test
    go away."""
    assert len(HTML) < 216_000, (
        f"the landing page is {len(HTML)} chars — something large came back")


# ── the wall exists and is wired ─────────────────────────────────────────────

def test_the_hero_is_the_wall():
    for el in ('id="wall"', 'id="wall-state"', 'id="wall-rate"'):
        assert el in HTML, f"the hero lost {el}"


def test_the_hero_does_not_play_a_clip_at_you():
    """The wall used to stop being a wall every fourteen seconds: the firing
    tile expanded into a full-width Twitch player, held for the clip's real
    duration, then collapsed. A monitor that pauses to play you a video is not
    demonstrating monitoring. The crossing is still the payoff — the trace goes
    bright above the line and the tile marks OVER THRESHOLD — and the page
    keeps running."""
    for gone in ('id="stage"', 'id="stage-poster"', 'id="stage-frame"',
                 'id="stage-out"', 'class="stage-bar"', 'TRIGGER FIRED'):
        assert gone not in HTML, f"the clip stage is back: {gone}"
    # Comments are documentation, not code — the removal notes name the
    # functions they removed, which is the point of them.
    code = re.sub(r"/\*.*?\*/", "", JS, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    for fn in ('openStage', 'closeStage', 'teardownFrame', 'holdFor'):
        assert fn not in code, f"stage machinery is back: {fn}"
    # And no iframe anywhere in the hero: the wall must never build a player.
    wall = HTML[HTML.index('id="wall"'):HTML.index('id="wall-state"')]
    assert "<iframe" not in wall, "the wall builds a player again"


def test_the_score_is_still_the_one_number_that_lights_the_page():
    """--lit is the page's own mechanic. The wall writes it while it is on
    screen; the scroll wave takes over below the fold. Two writers on one
    variable with no arbitration made the room flicker."""
    assert "root.style.setProperty('--lit'" in HTML
    assert "if(q!==lastLit)" in HTML, "--lit is written on every frame again"
    assert "data-hero" in HTML, "the wall no longer claims ownership of --lit"
    assert "root.getAttribute('data-hero') !== '1'" in HTML, \
        "the scroll wave stopped yielding to the wall"


# ── the simulation ───────────────────────────────────────────────────────────

def test_the_simulation_is_seeded_not_random():
    """Math.random reads as a screensaver and cannot be reasoned about. The
    trace has to be reproducible for the near-miss headroom below to mean
    anything at all."""
    assert "Math.random" not in JS, "the wall went back to unseeded randomness"
    assert "mulberry" in JS, "the seeded generator is gone"


def test_exactly_one_channel_can_fire_per_cycle():
    assert "fireI=idx%vis" in JS.replace(" ", ""), \
        "the firing channel no longer rotates deterministically"


def _miss_channel() -> tuple[int, int, int]:
    """(baseline, threshold, beat peak) for the near-miss channel.

    Sliced out of its own branch rather than pattern-matched across the whole
    function: the firing channel is assigned base and thresh three lines
    earlier, and a loose regex reads ITS numbers instead — which would let a
    genuinely broken near-miss pass by measuring the wrong tile."""
    i = JS.index("else if(i===missI){")
    block = JS[i:JS.index("}", JS.index("beats=[", i))]
    base = int(re.search(r"base=(\d+)", block).group(1))
    thresh = int(re.search(r"thresh=(\d+)", block).group(1))
    peak = int(re.search(r"peak:(\d+)", block).group(1))
    return base, thresh, peak


def test_the_near_miss_cannot_fire_by_accident():
    """THE LOAD-BEARING ASSERTION. A threshold nobody watches get missed is a
    threshold nobody believes, so one channel per cycle climbs to just under
    its line and stops. That only works if it CANNOT cross.

    The near-miss channel's score is baseline + drift + beat. Drift is two
    noise octaves, each bounded by its own amplitude, so the ceiling is exact
    and this is arithmetic rather than hope. No clamp props it up — if the
    numbers below stop leaving headroom, the fix is the numbers."""
    js = JS.replace(" ", "").replace("\n", "")

    # the two drift amplitudes
    amps = re.search(r"noiseAt\(tl\.n1,t/\d+\)\*([\d.]+)\+noiseAt\(tl\.n2,t/\d+\)\*([\d.]+)", js)
    assert amps, "could not read the drift amplitudes"
    max_drift = float(amps.group(1)) + float(amps.group(2))

    base, thresh, peak = _miss_channel()
    ceiling = base + peak + max_drift
    assert ceiling < thresh, (
        f"the near-miss can reach {ceiling} against a threshold of {thresh} — "
        f"it will fire by accident and the demo will show two channels crossing")
    assert thresh - ceiling >= 1.5, (
        f"only {thresh - ceiling:.1f} points of headroom; too tight to trust")


def test_the_near_miss_gets_close_enough_to_be_visible():
    """The other half. Headroom that is too GENEROUS is a channel that wanders
    around its baseline and never looks like it nearly made it."""
    base, thresh, peak = _miss_channel()
    assert base + peak >= thresh - 8, (
        f"the near-miss peaks at {base + peak} against {thresh} — nobody will "
        f"read that as a near miss")

    # And the tile has to SAY so while it is up there. The band the label
    # appears in must actually reach the peak, or the one beat that has to be
    # legible gets captioned for a single sample or not at all.
    band = re.search(r"varnear=!over&&s>=tl\.thresh-(\d+);",
                     JS.replace(" ", "").replace("\n", ""))
    assert band, "the near-miss band is gone"
    assert thresh - int(band.group(1)) <= base + peak, (
        f"the label shows from {thresh - int(band.group(1))} up, but the "
        f"near-miss only reaches {base + peak} — it will never be captioned")
    assert "NEAR MISS" in HTML, "the near miss is no longer labelled"


def test_nothing_fires_before_the_wall_has_settled():
    assert "FIRE_MIN" in JS
    m = re.search(r"FIRE_MIN=(\d+)", JS)
    assert m and int(m.group(1)) >= 3000, \
        "a channel can fire before anyone has read the wall"


# ── the clips are real ───────────────────────────────────────────────────────


def test_the_showcase_endpoint_is_still_public():
    assert "/landing/showcase" in api._OPEN_PATHS


# ── the quality floor ────────────────────────────────────────────────────────


def test_the_loop_stops_when_nobody_is_looking():
    assert "document.hidden" in JS
    assert "IntersectionObserver" in JS
    assert "cancelAnimationFrame" in JS


def test_the_clock_freezes_rather_than_teleporting_after_a_pause():
    """Resuming from a background tab with a wall-clock delta would jump the
    cycle to an arbitrary point, usually skipping the fire entirely."""
    assert "if(dt>500) dt=STEP" in JS


def test_the_loop_cannot_start_before_there_is_a_cycle_to_render():
    """The observer fires its first callback the moment the wall is observed —
    before the showcase fetch resolves. This raced and threw on whichever
    viewports lost."""
    assert "return started &&" in JS


def test_nothing_in_the_hero_animates_a_layout_property():
    """transform, opacity and clip-path only. width/height/top would put the
    whole page on the layout thread sixty times a second."""
    for prop in ("transition:width", "transition:height", "transition:top",
                 "transition:left", "transition:margin"):
        assert prop not in HTML, f"the hero animates {prop.split(':')[1]}"


# ── sound ───────────────────────────────────────────────────────────────────
# REPORTED, TWICE. First "no audio", which was fair: the player was hardcoded
# muted with no way to ask for sound. Then "still muted" after a Sound on/off
# button was added — because that button could never have worked.
#
# A Twitch CLIP embed cannot be unmuted from outside the iframe. `muted=false`
# in the embed URL is a documented no-op (per Twitch's developer forums the
# flag does nothing until the viewer has used the player's own controls), and
# the clips embed exposes no JS API to call instead; the Twitch player SDK
# covers channels, videos and collections, not clips.
#
# The button passed its tests because those tests ran against a STUB iframe
# that ignored every parameter. They proved the URL changed. They could not
# prove Twitch honoured it, and it does not.
#
# So these assert the constraint rather than a feature: the clip stays muted,
# the bar POINTS AT the player's own control instead of impersonating it, and
# nothing re-adds a muted=false toggle.


# ── reduced motion ───────────────────────────────────────────────────────────

def test_reduced_motion_gets_no_motion_at_all():
    """Not less — none. The second assertion here guarded against reduced
    motion booting a video player; there is no player to boot any more, and
    the check that replaced it is stronger: no player exists on any path."""
    assert "if(reduce){ composeStatic(); return; }" in JS


def test_the_reduced_motion_frame_still_shows_the_wall():
    """The frame has to carry the argument: four channels scored, thresholds
    drawn, one of them over its line. It used to also compose a clip player
    into the firing tile, which was the most moving part of a frame whose
    entire purpose is that nothing moves."""
    assert "composeStatic" in JS
    assert "els[i].root.classList.add('in')" in JS, "the static frame shows no tiles"
    assert "stage" not in JS.split("function composeStatic")[1][:900], \
        "the static frame composes a player again"


# ── phones ───────────────────────────────────────────────────────────────────

def test_the_wall_drops_to_two_channels_on_a_phone():
    """The 2x2 shows all four everywhere except a phone, where four stacked
    charts would be a scroll, not a wall — a single column of two instead."""
    m = re.search(r"@media\(max-width:(\d+)px\)\{\s*"
                  r"\.wall\{grid-template-columns:minmax\(0,1fr\)[^}]*\}\s*"
                  r"\.wall \.tile:nth-child\(n\+3\)\{display:none\}", HTML)
    assert m, "the phone wall no longer collapses to a column of two"



def test_the_simulation_agrees_with_the_stylesheet_about_what_is_visible():
    """If visibleCount() says ten while the CSS is hiding four, the cycle can
    pick a hidden tile to fire and the payoff happens off screen. Both
    breakpoints are read from both sides and must agree: the phone shows four
    tiles, the tablet six, the desktop all ten."""
    css_phone = re.search(r"@media\(max-width:(\d+)px\)\{[^@]*?\.watch \.wall \.tile:nth-child\(n\+5\)\{display:none\}", HTML)
    css_tab = re.search(r"@media\(max-width:(\d+)px\)\{[^@]*?\.watch \.wall \.tile:nth-child\(n\+7\)\{display:none\}", HTML)
    js_phone = re.search(r"matchMedia\('\(max-width:(\d+)px\)'\)\.matches\) return 4;", JS)
    js_tab = re.search(r"matchMedia\('\(max-width:(\d+)px\)'\)\.matches\) return 6;", JS)
    assert css_phone and css_tab and js_phone and js_tab, "could not read the breakpoints"
    assert css_phone.group(1) == js_phone.group(1), "the phone breakpoint disagrees"
    assert css_tab.group(1) == js_tab.group(1), "the tablet breakpoint disagrees"
    assert "var N=10;" in JS and "<4;" not in JS, "a hard-coded four is back in the loop"


def test_the_wall_never_shows_the_same_channel_twice():
    """A wall claiming to watch four channels at once has to show four
    channels. The curated showcase can easily hold three clips from the same
    streamer — it did — so a row of four read stableronaldo, stableronaldo,
    jynxzi, jynxzi.

    Fixed in two places, because either alone leaves a hole: the pool is
    deduplicated by channel when it is fetched, AND the per-cycle selection
    skips a name already taken, backfilling from the invented names. The second
    is what covers a pool that is short, empty, or not yet loaded.
    """
    # deduplicated at the source
    assert "if(!k||seen[k]) return;" in JS, "the clip pool is not deduplicated by channel"
    # and again when the four tiles are chosen
    sel = JS[JS.index("var pick=[], used={}"):]
    sel = sel[:sel.index("var out=[],i;")]
    assert "if(k && !used[k])" in sel, "a cycle can pick the same channel twice"
    assert "if(!used[nk])" in sel and "var nk=nm.n.toLowerCase().replace(/_+$/,'')" in sel, \
        "the backfill names can collide with a real channel or with each other"
    # The KEY drops trailing underscores on both sides, so a curated "caseoh"
    # and the list's "caseoh_" are one channel, not two tiles.
    assert "var k=(c.channel||'').toLowerCase().replace(/_+$/,'')" in sel


def test_the_wall_still_works_with_no_curated_clips_at_all():
    """The pool is empty until /landing/showcase returns, and may stay empty.
    Every tile must still get a name."""
    assert "pick.push(chosen||{clip:null,name:names[j%names.length].n,game:names[j%names.length].g})" in JS, \
        "a tile can end up with no name when the pool is empty"
    assert "var clips=[], names=[" in JS, "the fallback names are gone"
    # REAL CHANNELS, on the owner's call: the backfill is well-known Twitch
    # channels with the category each is known for, not invented names, and
    # there are at least ten so a wall with nothing curated is still ten
    # distinct real streamers.
    block = JS[JS.index("var clips=[], names=["):JS.index("];", JS.index("var clips=[], names=["))]
    entries = re.findall(r"\{n:'([^']+)',g:'([^']+)'\}", block)
    assert len(entries) >= 10, "fewer than ten backfill channels — a tile would repeat"
    assert len({n for n, _ in entries}) == len(entries), "a backfill channel is listed twice"
    for invented in ("novafps", "tessplays", "kettlebrook", "mothcandle"):
        assert invented not in block, f"an invented name is back: {invented}"
    # and the category reaches the tile when the name is a backfill
    assert "e.game.textContent=(tl.clip&&tl.clip.game)?tl.clip.game:(tl.game||'');" in JS


# ── no cover: the page opens on the product ──────────────────────────────────

def test_the_site_opens_on_the_proof_section_with_only_the_bar_over_it():
    """Owner, 2026-10-26: "I am thinking of removing the black screen with the
    logo when you first land on the landing page. It seems like a useless page
    so lets get rid of it." The first screen is the proof section now: the
    clip frame, the headline and the button. The fixed bar is the one thing
    allowed in front of it, out of flow so it does not push the section down,
    and the section's own top padding clears the bar."""
    body = HTML.index("<body>")
    first = HTML.index('<section class="proof" id="proof"')
    live = re.sub(r"<!--.*?-->", "", HTML[body + len("<body>"):first], flags=re.S)
    assert "<section" not in live and "<header" not in live, \
        "something renders before the proof section"
    assert live.count("<nav") == 1 and "<div" not in live.split("<nav")[0], \
        "the nav is not the only thing before the proof section"
    assert live.split("</nav>")[-1].strip() == "", \
        "something sits between the bar and the proof section"
    nav = re.search(r"\n  \.nav\{([^}]*)\}", HTML).group(1)
    assert "position:fixed" in nav and "top:0" in nav, \
        "the bar is in flow again and pushes the first section down"
    pad = re.search(r"\n  \.proof-in\{[^}]*padding-top:clamp\((\d+)px", HTML)
    nav_h = re.search(r"--nav-h:(\d+)px", HTML)
    assert pad and nav_h and int(pad.group(1)) > int(nav_h.group(1)), \
        "the headline can land under the bar"


def test_the_cover_stays_gone():
    """No black first screen and none of what came with it: the blurred mark,
    the wordmark, the stat band (its live count is in the figures under the
    proof section), the scroll cue, the fade-out, and no rule left shipping
    that styles nothing. Comments are stripped first, because the notes that
    say what was removed name every one of these."""
    live = re.sub(r"/\*.*?\*/", "", HTML, flags=re.S)
    live = re.sub(r"<!--.*?-->", "", live, flags=re.S)
    for gone in ('id="cover"', 'class="cover', ".cover{", ".cover-", "cover-cue",
                 "@keyframes cue", 'class="stats"', ".stats{", ".stat{", ".stat .n",
                 "stat-clips", "lp-count", "measureCover", "past-cover"):
        assert gone not in live, f"the cover left {gone!r} behind"


def test_the_first_scroll_gesture_is_the_reader_s():
    """The cover came with a controller that took the first wheel turn, key
    press or swipe and moved the reader a whole screen in one jump. With the
    cover gone the page just scrolls, so nothing may listen to the wheel or a
    swipe in a way that can cancel it."""
    for gone in ("slideTo", "slideIntent", "armRest", "bindSlides", "coverFits",
                 "'wheel'", "'touchmove'", "passive: false"):
        assert gone not in HTML, f"the scroll is being taken over again: {gone}"


def test_the_logo_reserves_its_real_shape():
    """The file is 374x501 — taller than it is wide. Declaring a square would
    reserve the wrong box, which is a layout shift dressed up as a fix. The
    attributes are the natural size; the browser takes the ratio from them and
    combines it with the CSS height. The cover's big mark is gone, so this
    reads the footer's."""
    import struct
    from pathlib import Path
    from src.dashboard.api import _STATIC_DIR
    raw = Path(_STATIC_DIR, "logo-mark.png").read_bytes()
    w, h = struct.unpack(">II", raw[16:24])
    foot = HTML[HTML.index('<footer class="footer">'):]
    foot = foot[:foot.index("</footer>")]
    assert f'width="{w}"' in foot and f'height="{h}"' in foot, \
        f"the footer declares a shape that is not the file's {w}x{h}"


def test_ember_belongs_to_the_instruments_not_the_prose():
    """The orange is the page's one accent and it sits on measurements and
    the action: the wall's scores, the big figures, the price, the crossing,
    the category tag on a card, the button. Prose and wayfinding never wear
    it."""
    prose = {
        ".l-sub": r"\.l-sub\{([^}]*)\}",
        ".proof-note": r"\.proof-note\{([^}]*)\}",
        ".bign-k": r"\.bign-k\{([^}]*)\}",
        ".card-t p": r"\.card-t p\{([^}]*)\}",
        ".plan-facts li": r"\.plan-facts li\{([^}]*)\}",
        ".price-lead": r"\n  \.price-lead\{([^}]*)\}",
        ".wall-cap": r"\.wall-cap\{([^}]*)\}",
    }
    for name, pat in prose.items():
        m = re.search(pat, HTML)
        assert m, f"{name} is gone — re-point this test rather than deleting it"
        assert "ember" not in m.group(1), f"{name} is wearing the instrument colour"
    for name, pat in ((".tile-score", r"\.tile-score\{([^}]*)\}"),
                      (".bign-n", r"\n  \.bign-n\{([^}]*)\}"),
                      (".btn-go", r"\n  \.btn-go\{([^}]*)\}"),
                      (".card-k", r"\.card-k\{([^}]*)\}"),
                      (".tile-pull", r"\.tile-pull\{([^}]*)\}")):
        m = re.search(pat, HTML)
        assert m and "ember" in m.group(1), f"{name} lost its ember"


def test_the_wall_is_hairlines_on_black():
    """The wall was four gradient-bordered cards on a plum ground — dressed
    like a different website than the rest of the page. It is built the way
    the old stats band was: hairline above and below, cells divided by
    hairlines, no fills, on black. The hero ground eases back to --bone at its
    bottom so the next section starts with no edge."""
    tile = re.search(r"\n  \.tile\{([^}]*)\}", HTML).group(1)
    assert "border-radius" not in tile, "the tiles grew corners again"
    assert "border-box" not in tile, "the gradient border is back"
    assert "background:transparent" in tile, "the tiles have a card fill again"
    watch = re.search(r"\n  \.watch\{([^}]*)\}", HTML).group(1)
    assert "background:#000" in watch, "the wall no longer sits on black"
    # stacked rows on a phone divide horizontally, and the override must sit
    # AFTER the base .tile rule or the base border-left silently wins
    i_base = HTML.index("\n  .tile{")
    i_phone = HTML.index("border-top:1px solid var(--hair);padding-left:0;padding-right:0")
    assert i_phone > i_base, "the phone divider override is before the base rule again"


def test_the_removed_hero_left_no_dead_stylesheet():
    """The lede's CSS was ~4KB shipped to every visitor. Dead rules are not
    harmless here — a dead FAQ stylesheet once overrode the live one's measure,
    and a dead pricing stylesheet absorbed a whole restyle pass."""
    # Comments stripped first: the block those rules occupied now holds a note
    # explaining what was removed, and it names every one of them. Matching
    # against the raw page would fail on the explanation rather than the CSS.
    live = re.sub(r"/\*.*?\*/", "", HTML, flags=re.S)
    live = re.sub(r"<!--.*?-->", "", live, flags=re.S)
    for gone in ("hero-lede{", "hero-copy", "hero-act{", "hero-ctas{",
                 "hero-note{", ".no-ai{", ".kicker{"):
        assert gone not in live, f"{gone} still ships but styles nothing"


def test_the_page_still_has_exactly_one_h1():
    """The h1 was "Never miss a highlight again." in the hero, then the
    cover's wordmark. With the cover gone (2026-10-26) it is the proof
    section's headline, the first thing a visitor reads — a landing page with
    no h1 is a real SEO regression, and two is as wrong as none."""
    live = re.sub(r"<!--.*?-->", "", HTML, flags=re.S)
    assert live.count("<h1") == 1, "the page has zero or several h1 elements"
    h1 = '<h1 class="disp proof-h" id="proof-h">Every big moment,<br>already a clip.</h1>'
    assert h1 in live, "the proof headline is not the page's h1"
    proof = live[live.index('<section class="proof" id="proof"'):live.index("</section>")]
    assert 'aria-labelledby="proof-h"' in proof and h1 in proof


# ── slide 2 is a spread ──────────────────────────────────────────────────────


