"""The logo and the social preview card — the two brand assets that go stale
silently.

TWO REAL BUGS LIVED HERE FOR MONTHS, both invisible from inside the code:

  1. THE LOGO WAS A JPEG WITH THE BACKGROUND BAKED IN. JPEG has no alpha
     channel, so `logo.jpg` shipped a solid #110C22 navy plate around the mark.
     On the old blue-black dashboard that was nearly invisible; once the palette
     moved to plum-black (--void #0E0B11) it read as a dark rectangle sitting
     behind the logo in the header. No CSS can fix that — the box is pixels.

  2. THE SOCIAL CARD ADVERTISED AN OFFER THAT NO LONGER EXISTED. og-card.png
     said "7 days free" and "$15/month". The trial had been removed and $15 was
     the retired legacy price (now Free / $10 / $25). Every share to Instagram,
     Facebook, Twitter and Discord was selling it anyway, because the only place
     that copy existed was inside a binary nobody re-opened.

Both failures share a shape: a fact frozen into an image file, drifting away
from the product with nothing checking. These tests are the thing that checks.
"""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "static"
CARD_SRC = Path(__file__).resolve().parents[1] / "scripts" / "og_card.html"


def _templates():
    """Every HTML template the app serves, by name."""
    from src.dashboard import api, aurora_html
    out = {n: v for n, v in vars(api).items()
           if n.endswith("_HTML") and isinstance(v, str)}
    out["DASHBOARD_HTML"] = aurora_html.DASHBOARD_HTML
    return out


# ── 1. the logo is transparent, and nothing still points at the plated JPEG ──

def test_the_shipped_logo_has_a_real_alpha_channel():
    """The whole bug. A JPEG cannot be transparent, so the mark arrived with its
    background welded on and rendered as a box in the dashboard header."""
    from PIL import Image
    im = Image.open(STATIC / "logo-mark.png")
    assert im.mode == "RGBA", f"the logo is {im.mode} — it has no alpha to be transparent with"


def test_the_corners_of_the_logo_are_fully_transparent():
    """Not just 'has an alpha channel' — the plate has to actually be gone."""
    from PIL import Image
    im = Image.open(STATIC / "logo-mark.png").convert("RGBA")
    w, h = im.size
    px = im.load()
    for x, y in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        assert px[x, y][3] == 0, f"corner {(x, y)} is opaque — the old plate is still there"


def test_no_semi_transparent_pixel_still_carries_the_old_plate_colour():
    """The edge pixels are the trap. A hard threshold leaves the mark's antialiased
    rim literally full of the old navy, which then composites as a dark fringe on
    whatever background it sits on — the same box, just softer."""
    from PIL import Image
    im = Image.open(STATIC / "logo-mark.png").convert("RGBA")
    px = im.load()
    w, h = im.size
    navy = (17, 12, 34)
    bad = [(x, y) for y in range(0, h, 2) for x in range(0, w, 2)
           if 20 < px[x, y][3] < 235
           and all(abs(px[x, y][i] - navy[i]) < 8 for i in range(3))]
    assert not bad, f"{len(bad)} edge pixels still hold the old plate colour, e.g. {bad[:3]}"


def test_the_logo_is_cropped_to_its_own_ink():
    """The source JPEG was mostly empty plate — the mark was ~43% of the file's
    height. Left uncropped, every `height:` in the CSS sizes the padding rather
    than the logo, which is why the header mark looked so small inside its box."""
    from PIL import Image
    im = Image.open(STATIC / "logo-mark.png").convert("RGBA")
    bbox = im.getchannel("A").getbbox()
    assert bbox == (0, 0, *im.size), f"the mark does not fill its own canvas: {bbox} vs {im.size}"


def test_the_favicon_is_square():
    """A portrait image in the square tab slot gets letterboxed by every browser,
    which is how a 784x1168 JPEG became a sliver of a logo in the tab."""
    from PIL import Image
    im = Image.open(STATIC / "icon.png")
    assert im.size[0] == im.size[1], f"favicon is {im.size}, not square"
    assert im.mode == "RGBA"


@pytest.mark.parametrize("name", sorted(_templates()))
def test_no_template_still_serves_the_plated_jpeg(name):
    assert "logo.jpg" not in _templates()[name], (
        f"{name} still points at the JPEG, which renders the mark inside a dark box")


@pytest.mark.parametrize("name", sorted(_templates()))
def test_every_favicon_is_the_square_png(name):
    html = _templates()[name]
    if 'rel="icon"' not in html:
        return
    assert 'href="/static/icon.png"' in html, f"{name} does not use the square favicon"
    assert 'type="image/jpeg"' not in html, f"{name} still declares a JPEG favicon"


def test_the_border_radius_hack_is_gone():
    """`border-radius:4px` on the logo <img> only ever existed to round the
    corners of the plate. Leaving it in implies the rectangle is still there."""
    from src.dashboard import api
    assert "img{height:26px;border-radius:4px}" not in api.LANDING_HTML
    assert "img{height:24px;border-radius:4px}" not in api.ADMIN_HTML


# ── 2. the social card ────────────────────────────────────────────────────────

def test_the_preview_image_is_the_versioned_url():
    """Facebook and Instagram cache the preview keyed on the URL. Overwriting the
    bytes at the old path does not refresh what anyone sees, so a corrected card
    MUST ship under a new filename or the stale one keeps being served."""
    from src.dashboard.api import LANDING_HTML as html
    urls = set()
    for tag in ("og:image", "twitter:image"):
        m = re.search(rf'{tag}"\s+content="([^"]+)"', html)
        assert m, f"{tag} is missing entirely"
        urls.add(m.group(1))
        # Deliberately NOT pinned to a version number. Pinning it means every
        # new card edits this test, and a test you edit to make a change is not
        # guarding the change — what matters is that the URL carries a version
        # at all, so a corrected card is served from a path no cache has seen.
        assert re.search(r"/static/og-card-v\d+\.png$", m.group(1)), \
            f"{tag} points at {m.group(1)} — an unversioned path is cached and will not refresh"
    assert len(urls) == 1, f"og:image and twitter:image disagree: {urls}"


def test_the_declared_preview_image_actually_exists_and_is_the_declared_size():
    """A 404 or a mis-sized card is a link that previews as a grey box."""
    from PIL import Image
    from src.dashboard.api import LANDING_HTML as html
    url = re.search(r'og:image"\s+content="([^"]+)"', html).group(1)
    f = STATIC / url.rsplit("/", 1)[-1]
    assert f.exists(), f"{f.name} is declared in og:image but is not on disk"
    im = Image.open(f)
    assert im.size == (1200, 630), f"card is {im.size}, but the meta tags promise 1200x630"

    w = re.search(r'og:image:width"\s+content="(\d+)"', html).group(1)
    h = re.search(r'og:image:height"\s+content="(\d+)"', html).group(1)
    assert (int(w), int(h)) == im.size, "og:image:width/height disagree with the actual file"


def test_the_retired_card_was_rewritten_rather_than_deleted():
    """Links shared before the rename still point at the old path. Deleting it
    breaks every one of those posts; leaving the ORIGINAL there keeps serving
    "7 days free / $15 a month". So it stays, with the corrected art on it."""
    from src.dashboard.api import LANDING_HTML as html
    current = STATIC / re.search(
        r'og:image"\s+content="[^"]+/([^"/]+)"', html).group(1)
    retired = [p for p in STATIC.glob("og-card*.png") if p != current]
    assert retired, "no retired card on disk — has the card never been reversioned?"
    for old in retired:
        assert old.read_bytes() == current.read_bytes(), (
            f"{old.name} still holds stale artwork; a crawler revalidating an "
            f"old share would re-serve it")


def test_the_card_never_quotes_a_retired_price():
    """The exact failure being prevented. A price baked into a cached image is
    the slowest thing on the internet to correct.

    NOT a blanket ban on mentioning a trial any more: the self-serve 7-day trial
    is real again and the card sells it. What must never come back is $15, the
    price that was retired — and any trial claim has to match TRIAL_DAYS, which
    is the check below, because a cached card quoting the wrong number is the
    same bug wearing different clothes."""
    from src.billing.plans import TRIAL_DAYS
    body = CARD_SRC.read_text(encoding="utf-8").split("-->", 1)[1]
    assert "$15" not in body, "the social card quotes the retired price"
    import re as _re
    for n in _re.findall(r"(\d+)\s*days? free", body, _re.I):
        assert int(n) == TRIAL_DAYS, \
            f"the card advertises a {n}-day trial but TRIAL_DAYS is {TRIAL_DAYS}"


def test_the_card_quotes_no_price_at_all():
    """Any PRICE here will outlive the offer, because the cache is not ours to
    clear — so the card sells the trial instead."""
    from src.billing.plans import TRIAL_DAYS
    body = CARD_SRC.read_text(encoding="utf-8").split("-->", 1)[1]
    assert not re.search(r"\$\s*\d", body), "a price crept onto the social card"
    # It still has to say the thing that replaced the price.
    assert f"{TRIAL_DAYS} days free" in body.lower()
    assert "no card" in body.lower()


def test_the_card_does_not_invent_a_fixed_trigger_threshold():
    """The threshold is ADAPTIVE per channel — that is the product's central
    claim. Printing a constant next to it contradicts the thing being sold."""
    body = CARD_SRC.read_text(encoding="utf-8").split("-->", 1)[1]
    assert not re.search(r"threshold\s+\d", body, re.I), \
        "the card prints a fixed threshold number, but the threshold adapts per channel"


def test_the_card_is_built_from_the_same_tokens_as_the_landing_page():
    """If these drift, the card stops looking like the page it links to — which
    is exactly how the last one ended up in an abandoned design."""
    from src.dashboard.api import LANDING_HTML
    src = CARD_SRC.read_text(encoding="utf-8")
    for token in ("--void:#0E0B11", "--glow:#B86ADC", "--flare:#D26AFB",
                  "--ember:#F7A745", "--ink:#F2EAF7"):
        assert token in src, f"the card does not use {token}"
        assert token in LANDING_HTML, f"{token} is no longer the landing palette"


def test_the_card_uses_the_self_hosted_fonts_not_a_fallback():
    src = CARD_SRC.read_text(encoding="utf-8")
    for font in ("lobster-400.woff2", "sora-var.woff2", "plexmono-600.woff2"):
        assert font in src, f"the card does not load {font}"
        assert (STATIC / "fonts" / font).exists(), f"{font} is not shipped"


def test_the_card_is_regenerable():
    """A checked-in binary with no source is how this went wrong. The build has
    to exist and has to name the file the meta tags actually point at."""
    build = (Path(__file__).resolve().parents[1] / "scripts" / "build_og_card.mjs")
    from src.dashboard.api import LANDING_HTML as html
    assert build.exists(), "the card is a binary with no way to rebuild it"
    declared = re.search(r'og:image"\s+content="[^"]+/([^"/]+)"', html).group(1)
    assert declared in build.read_text(encoding="utf-8"), \
        f"the build writes some other file than the {declared} the page declares"
    assert CARD_SRC.exists()


def test_the_preview_text_matches_the_free_tier_that_actually_shipped():
    from src.dashboard.api import LANDING_HTML as html
    for tag in ("og:description", "twitter:description"):
        m = re.search(rf'{tag}"\s+content="([^"]+)"', html)
        assert m, f"{tag} is missing"
        # "7 day" is ALLOWED again: the self-serve 7-day trial is back and is
        # what the page actually sells. What must never come back is the
        # RETIRED offer this test was written for — the $15 single price.
        assert "$15" not in m.group(1)


def test_the_preview_image_has_alt_text():
    """A preview card is an image, and it is the only thing a screen-reader user
    gets from a shared link."""
    from src.dashboard.api import LANDING_HTML as html
    assert 'property="og:image:alt"' in html
    assert 'name="twitter:image:alt"' in html
