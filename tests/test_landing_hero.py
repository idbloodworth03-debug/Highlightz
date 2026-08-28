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
    """They were removed deliberately: the hero shows the product working, so a
    static crop of the dashboard three sections down argued the same thing with
    worse evidence."""
    assert gone not in HTML, f"a product screenshot came back: {gone}"


def test_the_landing_page_did_not_regrow_the_dashboard_stylesheet():
    """The captures shipped 56KB of the app's own CSS to every visitor. Nothing
    should quietly reintroduce that."""
    assert len(HTML) < 160_000, (
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
    hero = HTML[HTML.index('class="hero-stack"'):HTML.index('id="examples"')]
    assert "<iframe" not in hero, "the hero builds a player again"


def test_the_wall_fills_the_viewport_and_is_not_boxed_into_the_text_column():
    """`.hero-band` alone loses to three later `.wrap` media queries, which is
    how the wall ended up in a 1280 column with the bottom of the viewport
    empty. Two class names is what makes it stick."""
    assert ".hero.hero-band{" in HTML
    m = re.search(r"\.hero\.hero-band\{([^}]*)\}", HTML)
    assert m and "max-width:none" in m.group(1)
    assert "min-height:calc(100svh" in m.group(1)


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
    assert "@media(max-width:1180px){" in HTML
    assert ".wall .tile:nth-child(n+3){display:none}" in HTML


def test_the_simulation_agrees_with_the_stylesheet_about_what_is_visible():
    """If this says four while the CSS is showing two, the cycle can pick a
    hidden tile to fire and the payoff of the entire hero happens off screen."""
    css_bp = re.search(r"@media\(max-width:(\d+)px\)\{\s*\.wall\{grid-template-columns:"
                       r"repeat\(2", HTML)
    js_bp = re.search(r"matchMedia\('\(max-width:(\d+)px\)'\)\.matches\?2:4", JS)
    assert css_bp and js_bp, "could not read both breakpoints"
    assert css_bp.group(1) == js_bp.group(1), (
        f"CSS drops to two tiles at {css_bp.group(1)}px but the simulation "
        f"switches at {js_bp.group(1)}px")


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
    assert "if(!used[nm.toLowerCase()])" in sel, \
        "the invented names can collide with a real channel or with each other"


def test_the_wall_still_works_with_no_curated_clips_at_all():
    """The pool is empty until /landing/showcase returns, and may stay empty.
    Every tile must still get a name."""
    assert "pick.push(chosen||{clip:null,name:names[j%names.length]})" in JS, \
        "a tile can end up with no name when the pool is empty"
    assert "var clips=[], names=[" in JS, "the fallback names are gone"


# ── the masthead ─────────────────────────────────────────────────────────────

def test_the_page_opens_with_the_logo_and_the_name():
    """Front and centre, first thing on the page."""
    mark = HTML[HTML.index('class="hero-mark"'):]
    mark = mark[:mark.index("</div>")]
    assert "/static/logo-mark.png" in mark, "the masthead uses a different logo"
    assert "Highlightz" in HTML[HTML.index('class="hero-wordmark"'):][:80]
    # and it comes BEFORE the rest of the hero
    assert HTML.index('class="hero-mark"') < HTML.index('class="hero-lede"')


def test_the_wordmark_matches_the_nav_exactly():
    """A name set two different ways on one screen reads as two different
    names. Only the size may differ between the nav and the masthead."""
    nav = re.search(r"\.nav-logo span\{([^}]*)\}", HTML).group(1)
    mast = re.search(r"\.hero-wordmark\{([^}]*)\}", HTML).group(1)
    for prop in ("font-family", "font-weight", "letter-spacing", "text-transform"):
        a = re.search(prop + r":([^;]+)", nav)
        b = re.search(prop + r":([^;]+)", mast)
        assert a and b and a.group(1) == b.group(1), \
            f"the masthead and the nav disagree on {prop}"


def test_the_logo_reserves_its_real_shape():
    """The file is 374x501 — taller than it is wide. Declaring 96x96 would
    reserve a square box for it, which is a layout shift dressed up as a fix.
    The attributes are the natural size; the browser takes the ratio from them
    and combines it with the CSS height."""
    import struct
    from pathlib import Path
    from src.dashboard.api import _STATIC_DIR
    raw = Path(_STATIC_DIR, "logo-mark.png").read_bytes()
    w, h = struct.unpack(">II", raw[16:24])
    mark = HTML[HTML.index('class="hero-mark"'):]
    mark = mark[:mark.index("</div>")]
    assert f'width="{w}"' in mark and f'height="{h}"' in mark, \
        f"the masthead declares a shape that is not the file's {w}x{h}"


def test_the_masthead_mark_is_painted_flat_like_the_nav_mark():
    """Same logo, same treatment. A glow on the big one and none on the small
    one makes a single mark read as two. The nav img rule carries no filter,
    so neither may this one."""
    nav = re.search(r"\.nav-logo img\{([^}]*)\}", HTML).group(1)
    mast = re.search(r"\.hero-mark img\{([^}]*)\}", HTML).group(1)
    assert "filter" not in nav, "the nav mark grew a filter; re-check this pair"
    assert "filter" not in mast, \
        "the masthead mark is decorated in a way the nav mark is not"


def test_the_masthead_does_not_crush_the_wall_on_a_short_screen():
    """The hero is min-height:100svh with the wall in a minmax(0,1fr) row, so
    anything added above it comes straight out of the wall's height. A laptop
    at 720 has no room for a 96px mark AND a 38px wordmark AND the wall."""
    assert "@media(max-height:820px){" in HTML, \
        "the masthead does not shrink on a short viewport"
    block = HTML[HTML.index("@media(max-height:820px){"):]
    block = block[:block.index("}\n") + 200]
    assert ".hero-mark" in block and ".hero-wordmark" in block
