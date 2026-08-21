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
    for el in ('id="wall"', 'id="stage"', 'id="stage-poster"', 'id="stage-frame"',
               'id="wall-state"'):
        assert el in HTML, f"the hero lost {el}"


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

def test_the_wall_shows_the_owners_curated_clips():
    """The channels on screen are showcase entries, so the names, games and
    footage are the real ones. Invented channel names were one of the four
    tells this whole redesign was about."""
    assert "/landing/showcase" in JS, "the wall stopped reading the showcase"
    assert "tl.clip.channel" in JS
    assert "tl.clip.thumbnail_url" in JS


def test_the_showcase_endpoint_is_still_public():
    assert "/landing/showcase" in api._OPEN_PATHS


def test_the_poster_asks_for_the_full_size_and_steps_down_rather_than_giving_up():
    """A freshly created clip 404s on the 1280 variant until Twitch has
    generated its preview frames. Removing the image outright on that error
    left a stage with no picture at all — worse than the soft one."""
    assert "-preview-1280x720." in JS
    assert "data-tried" in JS, "the hi-res fallback chain is gone"


# ── the quality floor ────────────────────────────────────────────────────────

def test_only_the_firing_clip_is_ever_fetched():
    """One player, created on the cross and destroyed on collapse. Nothing on
    this page preloads video."""
    assert JS.count("createElement('iframe')") == 1, \
        "more than one player can exist"
    assert "teardownFrame" in JS
    assert "frameEl.src='about:blank'" in JS, "the player is not really stopped"


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


def test_the_stage_reveals_rather_than_scaling_so_the_clip_is_never_distorted():
    assert "clip-path:inset(" in HTML
    assert "transition:clip-path" in HTML


def test_the_clip_gets_a_real_aspect_ratio():
    """The wall is about 3:1 and a clip is 16:9. Handing the player the whole
    wall letterboxed it down to a strip."""
    assert "aspect-ratio:16/9" in HTML
    assert "container-type:size" in HTML, \
        "the media frame lost the container it measures itself against"


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

def test_the_clip_is_always_muted():
    """Both walls at once. Browsers block autoplay with audio, so muted is what
    buys the hero the right to play at all; and Twitch would ignore the flag in
    the other direction anyway."""
    assert "'&autoplay=true&muted=true'" in JS, \
        "the embed no longer pins muted=true, so it may not autoplay at all"


def test_nothing_tries_to_unmute_through_the_embed_url():
    """The specific broken fix, kept out. muted=false appears in this file only
    inside comments explaining why it does not work."""
    code = re.sub(r"/\*.*?\*/", "", JS, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    assert "muted=false" not in code, \
        "something is trying to unmute via the embed URL again; Twitch ignores it"
    assert "muted='+(" not in code, "the mute flag is conditional again"


def test_the_bar_points_at_the_players_own_control():
    """The only thing that CAN unmute a clip is inside the iframe, so the page
    says where it is rather than offering a switch wired to nothing."""
    assert 'id="stage-hint"' in HTML
    assert "Click the clip for sound" in HTML
    assert 'id="stage-sound"' not in HTML, "the button that cannot work is back"


def test_the_hint_cannot_swallow_the_click_it_is_asking_for():
    """It sits in the stage bar, over the player. A hint that eats the click is
    worse than no hint."""
    m = re.search(r"\.stage-hint\{([^}]*)\}", HTML)
    assert m and "pointer-events:none" in m.group(1), \
        "the hint can intercept the click on the player"


def test_clicking_into_the_player_is_detected():
    """A cross-origin iframe swallows its own clicks and there is no API to
    ask. Focus is the one signal that crosses: clicking inside an iframe blurs
    the parent and leaves document.activeElement on that iframe."""
    assert "window.addEventListener('blur'" in JS
    assert "document.activeElement!==frameEl" in JS, \
        "the engagement check no longer identifies WHICH element took focus"
    assert "setTimeout(" in JS[JS.index("window.addEventListener('blur'"):], \
        "activeElement is read synchronously in the blur handler, before it updates"


def test_nothing_of_ours_is_drawn_on_top_of_the_player():
    """REPORTED: "I can't unmute it when it pops up either."

    The stage bar was absolutely positioned across the bottom of the stage,
    which is exactly where Twitch draws the clip player's own controls — mute
    button included. elementFromPoint at the centre of that control strip
    returned .stage-bar. The one control on the page that CAN unmute a clip
    was under 44px of our own chrome and could not be clicked at all. Measured
    before the fix: 55 of 484 sampled points across four viewports were covered,
    every one of them in the bottom rows.

    So the stage is two grid rows now — the player, then our chrome — and the
    bar sits beneath the picture rather than over it. This asserts the shape,
    because the shape is what makes the control reachable."""
    m = re.search(r"\.stage\{([^}]*)\}", HTML)
    assert m, "the stage rule is gone"
    assert "grid-template-rows:minmax(0,1fr) auto" in m.group(1), \
        "the stage is no longer a media row plus a chrome row"

    bar = re.search(r"\.stage-bar\{([^}]*)\}", HTML)
    assert bar, "the stage bar rule is gone"
    assert "position:absolute" not in bar.group(1), \
        "the bar is absolutely positioned again — it will sit on the player's controls"
    assert "bottom:0" not in bar.group(1), \
        "the bar is pinned to the bottom of the stage, over Twitch's own controls"

    media = re.search(r"\.stage-media\{([^}]*)\}", HTML)
    assert media and "position:absolute" not in media.group(1), \
        "the media is absolutely positioned again, so the bar must overlay it"


def test_a_clip_someone_is_watching_is_allowed_to_finish():
    """Left alone the clip is a beat in a loop. Once somebody has clicked into
    the player — which on this page means they went looking for the sound —
    they are watching it, and taking it away mid-clip is rude."""
    assert "function holdFor(" in JS
    assert "if(!engaged||!tl||!tl.clip) return CYCLE;" in JS, \
        "the hold no longer depends on whether anyone is actually watching"
    assert "duration_seconds" in JS, "the hold ignores the clip's real length"
    m = re.search(r"d=clamp\(d,(\d+),(\d+)\);", JS.replace(" ", ""))
    assert m, "the duration is unclamped — a bad value can strand the hero"
    assert int(m.group(2)) <= 60, "a clip could hold the hero for over a minute"


def test_engagement_does_not_leak_into_the_next_clip():
    """A new cycle is a new clip nobody has asked to hear yet."""
    reseed = JS[JS.index("function reseed(){"):]
    reseed = reseed[:reseed.index("\n  }")]
    assert "engaged=false" in reseed, "engagement survives into the next clip"


def test_the_cycle_length_is_not_a_modulo_any_more():
    """A period that changes partway through cannot be expressed as
    elapsed % CYCLE, and the watch-it-out hold changes it."""
    assert "elapsed%CYCLE" not in JS.replace(" ", ""), \
        "the fixed-period clock is back and the hold cannot work"
    assert "cycleStart" in JS and "cycleLen" in JS


def test_the_readouts_hold_while_a_clip_is_playing():
    """The firing tile's beat decays underneath the stage. Letting the nav and
    the rail follow it down meant they drifted to baseline while the clip that
    crossed the line was still on screen."""
    assert "if(staged&&firedScore) best=firedScore;" in JS.replace("  ", " ")


# ── reduced motion ───────────────────────────────────────────────────────────

def test_reduced_motion_gets_no_motion_at_all():
    """Not less — none. And no player: an autoplaying video is exactly what
    somebody setting that preference is asking not to receive."""
    assert "if(reduce){ composeStatic(); return; }" in JS
    assert "if(reduce||!tl.clip) return;" in JS, \
        "reduced motion can now boot a video player"


def test_the_reduced_motion_frame_still_shows_the_wall():
    """Opening the stage over the whole wall left a frame whose only content
    was a clip — throwing away the part that carries the argument: channels
    still being scored, thresholds visible, one of them over the line."""
    assert "composeStatic" in JS
    assert "stage.classList.add('compact')" in JS, \
        "the static stage covers the whole wall again"


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
