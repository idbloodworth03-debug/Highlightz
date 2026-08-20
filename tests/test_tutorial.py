"""The public walkthrough at /tutorial.

A tutorial has one failure mode that matters: it documents a button that no
longer exists. Nobody notices, because nobody re-reads a help page — they only
find out when a confused user follows a step that goes nowhere. So the tests
below are mostly not about rendering. They assert that every UI label the page
quotes is still present in the live dashboard source, and that the plan numbers
still match what billing actually enforces.

The rest covers the two things that silently break a public page: the auth
middleware quietly swallowing it, and the /{slug} catch-all shadowing the route.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard import api, tutorial_content as C
from src.dashboard.aurora_html import DASHBOARD_HTML
from src.dashboard.tutorial_html import render


@pytest.fixture(scope="module")
def page() -> str:
    return render()


@pytest.fixture(scope="module")
def client():
    return TestClient(api.app)


# ── it is genuinely public ───────────────────────────────────────────────────

def test_a_signed_out_visitor_gets_the_page_not_a_redirect(client):
    """The whole point. AuthMiddleware bounces anything not explicitly opened,
    so forgetting the allowlist entry turns this into a 302 to /login."""
    r = client.get("/tutorial", follow_redirects=False)
    assert r.status_code == 200, f"got {r.status_code} -> {r.headers.get('location')}"
    assert "How to use Highlightz" in r.text


def test_the_route_is_in_the_open_paths_allowlist():
    assert "/tutorial" in api._OPEN_PATHS


def test_the_catch_all_slug_route_does_not_shadow_it():
    """`@app.get("/{slug}")` matches any single-segment path, and FastAPI
    resolves in declaration order — so a /tutorial declared BELOW it would never
    run, and the referral handler would 404 a path that plainly exists."""
    paths = [r.path for r in api.app.routes if getattr(r, "path", None)]
    assert "/tutorial" in paths
    assert paths.index("/tutorial") < paths.index("/{slug}"), \
        "/tutorial is declared after the catch-all and will never be reached"


def test_no_billing_gate_applies(client):
    """Access control lives per-feature, not in middleware — but a future
    paywall added to the middleware must not swallow the public guide."""
    r = client.get("/tutorial", follow_redirects=False)
    assert "billing" not in r.headers.get("location", "")


# ── discoverable ─────────────────────────────────────────────────────────────

def test_it_is_in_the_sitemap(client):
    assert "https://highlightz.app/tutorial" in client.get("/sitemap.xml").text


def test_robots_does_not_disallow_it(client):
    body = client.get("/robots.txt").text
    disallowed = re.findall(r"Disallow:\s*(\S+)", body)
    assert not any("/tutorial".startswith(d) for d in disallowed), disallowed


@pytest.mark.parametrize("snippet, where", [
    ('<a href="/tutorial" class="nav-link">Tutorial</a>', "landing nav"),
    ('href="/tutorial" class="btn btn-quiet btn-lg"', "landing CTA"),
    ('<a href="/tutorial">Tutorial</a>', "landing footer"),
])
def test_the_landing_page_links_to_it(snippet, where):
    assert snippet in api.LANDING_HTML, f"no tutorial link in the {where}"


def test_the_dashboard_empty_state_links_to_it():
    """The one screen a first-time user reliably lands on with nothing to do."""
    assert 'href="/tutorial"' in DASHBOARD_HTML
    assert "Read the walkthrough" in DASHBOARD_HTML


# ── the anti-drift contract ──────────────────────────────────────────────────

# Every one of these is quoted to the reader as something they will see on
# screen. If a rename lands in the dashboard and not here, this list is what
# catches it.
QUOTED_UI_LABELS = [
    "Live Streams", "Clip Review", "Clip Library", "VOD Scanner",
    "Settings", "Account", "Feedback",
    "search a streamer", "Monitor stream", "Monitored streams",
    "No streams yet.", "Waiting for clips",
    "Small streamer", "Chess / Strategy", "Casino / Gambling",
    "IRL / Outdoor", "Variety / Just Chatting", "Sports",
    "Top Virality", "All streamers",
    "Scan VOD", "Upgrade to Pro", "Manage billing", "Delete my account",
    # Only the two the support section actually names. The Feedback tab also
    # offers "General" and "Feature request", but the page does not quote them,
    # and listing a label here that the page never shows tests nothing.
    "Bug report",
]


@pytest.mark.parametrize("label", QUOTED_UI_LABELS)
def test_every_label_the_tutorial_quotes_still_exists_in_the_dashboard(label):
    assert label in DASHBOARD_HTML, (
        f"the tutorial tells people to look for {label!r}, but it is no longer "
        f"in the dashboard — the walkthrough is now wrong")


def test_the_quoted_labels_actually_appear_in_the_tutorial(page):
    """Guards the guard: a label list that drifts out of the page proves nothing."""
    missing = [l for l in QUOTED_UI_LABELS if l not in page]
    assert not missing, f"listed as quoted but absent from the page: {missing}"


def test_the_plan_numbers_match_what_billing_enforces():
    """A pricing table that drifts from the limits is worse than no table.

    THE COLUMNS CHANGED, and the reason matters. This used to read
    Free / Starter / Pro and assert against PLAN_LIMITS["free"] — but free is
    marked LEGACY ONLY in plans.py and no new account can reach it, so the
    table was documenting a tier the reader could not choose. The first column
    is now the trial, which is what they actually get, and it carries pro's
    numbers because get_plan resolves `trialing` to pro.
    """
    from src.billing.plans import PLAN_LIMITS, TRIAL_DAYS, get_plan
    rows = {r[0]: r[1:] for r in C.PLAN_ROWS}
    head = C.PLAN_ROWS[0][1:]

    assert head == (f"Trial ({TRIAL_DAYS} days)", "Starter", "Pro")
    assert get_plan({"subscription_status": "trialing"}) == "pro", \
        "the trial no longer resolves to pro, so this table's first column is wrong"

    assert rows["Price"] == ("$0, no card",
                             f"${PLAN_LIMITS['starter']['price']}/mo",
                             f"${PLAN_LIMITS['pro']['price']}/mo")
    # trial == pro on every row, which is the claim the page is making.
    for label, key in (("Channels at once", "max_streams"),
                       ("Clips held for review", "max_pending")):
        assert rows[label] == tuple(
            str(PLAN_LIMITS[p][key]) for p in ("pro", "starter", "pro")), \
            f"the {label} row drifted from PLAN_LIMITS"


def test_the_plans_table_does_not_advertise_the_legacy_free_tier():
    """plans.py: free is LEGACY ONLY, "Nothing new ever lands here." Offering
    it as a column sells something nobody can sign up for."""
    flat = [c for row in C.PLAN_ROWS for c in row]
    assert "Free" not in flat, "the plans table still offers a Free column"
    assert "$0" not in flat, "a bare $0 column reads as a permanent free tier"


def test_vod_is_described_as_pro_because_that_is_how_it_is_gated():
    from src.billing.plans import PLAN_LIMITS
    assert PLAN_LIMITS["pro"]["vod"] is True
    assert PLAN_LIMITS["free"]["vod"] is False and PLAN_LIMITS["starter"]["vod"] is False
    vod = next(s for s in C.FEATURES if s.id == "vod-scanner")
    assert vod.plan == "Pro"


# ── scope: do not document what a reader cannot open ─────────────────────────

def test_the_tutorial_does_not_document_features_that_are_off_or_excluded(page):
    """Captions are off in production (CAPTIONS_ENABLED unset), and the Clip
    Editor and Scheduler were excluded deliberately. Sending a reader to a tab
    they cannot use costs more trust than the section would have earned."""
    for absent in ("Clip Editor", "Scheduler", "auto-caption", "Export a clip"):
        assert absent not in page, f"the tutorial documents {absent!r}, which is out of scope"


def test_captions_really_are_off_by_default():
    """If this flips, the scope decision above needs revisiting rather than
    silently becoming wrong."""
    from config.settings import Settings
    assert Settings().captions_enabled is False


# ── structure and accessibility ──────────────────────────────────────────────

class _Doc(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.headings, self.imgs, self.videos = [], [], []
        self.stack, self.errors = [], []
        self._VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                      "link", "meta", "param", "source", "track", "wbr"}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("h1", "h2", "h3", "h4"):
            self.headings.append(int(tag[1]))
        if tag == "img":
            self.imgs.append(a)
        if tag == "video":
            self.videos.append(a)
        if tag not in self._VOID:
            self.stack.append((tag, self.getpos()))

    def handle_endtag(self, tag):
        if tag in self._VOID:
            return
        if not self.stack:
            self.errors.append(f"stray </{tag}>")
            return
        top, pos = self.stack.pop()
        if top != tag:
            self.errors.append(f"</{tag}> closes <{top}> opened at {pos}")


@pytest.fixture(scope="module")
def doc(page):
    d = _Doc()
    d.feed(page)
    return d


def test_the_markup_is_well_formed(doc):
    assert not doc.errors, doc.errors
    assert not doc.stack, f"unclosed at EOF: {doc.stack}"


def test_there_is_exactly_one_h1(doc):
    assert doc.headings.count(1) == 1


def test_heading_levels_never_skip(doc):
    """A screen reader navigates by heading level; a jump from h2 to h4 reads as
    a missing section."""
    bad = [(a, b) for a, b in zip(doc.headings, doc.headings[1:]) if b > a + 1]
    assert not bad, f"heading level jumps: {bad}"


def test_every_image_has_real_alt_text_and_reserved_space(doc):
    for img in doc.imgs:
        alt = img.get("alt", "")
        if img.get("class", "") == "tm-i":       # tutorial media, not chrome
            assert len(alt) > 15, f"weak alt text: {alt!r}"
            assert "screenshot" != alt.lower()
            assert img.get("width") and img.get("height"), \
                "no intrinsic size — the page will shift as it loads"
            assert img.get("loading") == "lazy"


def test_every_video_is_muted_looping_inline_and_preloads_only_metadata(page):
    """Spec'd behaviour, and the difference between a page that feels alive and
    one that burns a phone's data plan on load."""
    for tag in re.findall(r"<video[^>]*>", page):
        for attr in ("muted", "loop", "playsinline", 'preload="metadata"'):
            assert attr in tag, f"video missing {attr}: {tag[:120]}"


def test_every_video_has_a_text_description_so_it_works_with_sound_off():
    for m in C.all_media():
        if m.kind == "video":
            assert len(m.caption) > 40, f"{m.src} has no usable caption"


def test_every_media_slot_has_alt_text():
    for m in C.all_media():
        assert len(m.alt) > 15, f"{m.src} has weak alt text: {m.alt!r}"


def test_a_missing_media_file_renders_a_labelled_placeholder_not_a_broken_image():
    """The page has to be publishable before anything is captured, and a failed
    capture run must degrade rather than fill the page with broken icons."""
    ghost = C.Media(src="does-not-exist-99.png", alt="A slot with no file captured yet.")
    out = api.__dict__ and __import__(
        "src.dashboard.tutorial_html", fromlist=["media_html"]).media_html(ghost)
    assert "tm-ph" in out
    assert "coming soon" in out.lower()
    assert "does-not-exist-99.png" in out
    assert "<img" not in out, "a missing file must not render an <img> that 404s"


def test_every_captured_media_file_is_actually_committed():
    """THE BUG THIS EXISTS FOR. .gitignore carries `*.mp4` to keep captured
    Twitch clips out of the repo, and it swallowed the tutorial's own videos.
    Everything passed locally — the files were on disk, the page rendered them,
    the audit found two videos — and the deploy shipped a page with "video
    coming soon" placeholders, because the .mp4s had never been in a commit.

    Presence on disk proves nothing about what reaches the server. This checks
    the index instead.
    """
    import subprocess
    media_dir = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "static" / "tutorial"
    if not media_dir.is_dir():
        pytest.skip("no media captured yet")
    on_disk = {f.name for f in media_dir.iterdir() if f.is_file() and not f.name.startswith(".")}
    if not on_disk:
        pytest.skip("no media captured yet")
    tracked = subprocess.run(
        ["git", "ls-files", str(media_dir)],
        capture_output=True, text=True, cwd=media_dir.parents[3],   # repo root
    )
    if tracked.returncode != 0:
        pytest.skip("git not available")
    committed = {Path(p).name for p in tracked.stdout.split()}
    missing = sorted(on_disk - committed)
    assert not missing, (
        "these media files exist locally but are not tracked by git, so they "
        f"will be absent on the server: {missing}")


def test_the_page_uses_stills_rather_than_video():
    """Deliberate: screen recordings of this dashboard came out soft and juddery
    because Chrome only emits a screencast frame when the page repaints, so a
    mostly-static UI captures at ~16fps whatever it is encoded at. Stills are
    pixel-exact. This pins the decision so a future capture change does not
    quietly reintroduce video without someone re-reading that reasoning."""
    assert all(m.kind == "image" for m in C.all_media())
    from src.dashboard.tutorial_html import render
    assert "<video" not in render()


def test_the_renderer_still_handles_video_correctly_if_one_is_ever_added(
        tmp_path, monkeypatch):
    """The media component keeps its video branch even though nothing uses it
    today, so exercise it against fabricated files rather than leaving it as
    untested code that will be wrong the day someone needs it."""
    from src.dashboard import tutorial_html as th
    monkeypatch.setattr(th, "_MEDIA_DIR", tmp_path)
    for name in ("demo.mp4", "demo.webm", "demo-poster.jpg"):
        (tmp_path / name).write_bytes(b"x")
    out = th.media_html(C.Media(
        src="demo.mp4", kind="video", width=1440, height=810,
        alt="A demonstration clip used only by this test.",
        caption="Describes the clip so it is usable with the sound off."))
    assert "<video" in out
    for attr in ("muted", "loop", "playsinline", 'preload="metadata"', "poster="):
        assert attr in out, f"video missing {attr}"
    assert 'type="video/webm"' in out and 'type="video/mp4"' in out

    # And it must not blank out when only one encoding is present.
    (tmp_path / "demo.mp4").unlink()
    only_webm = th.media_html(C.Media(src="demo.mp4", kind="video",
                                      alt="A demonstration clip used only by this test.",
                                      caption="Describes the clip for sound-off use."))
    assert "<video" in only_webm and "tm-ph" not in only_webm


def test_the_lightbox_is_a_native_dialog(page):
    """<dialog> gives Escape-to-close, focus return and a backdrop for free —
    the parts of a modal that hand-rolled ones reliably get wrong."""
    assert "<dialog" in page and 'id="lightbox"' in page
    assert "showModal" in page


def test_the_toc_covers_every_section_on_the_page(page):
    for sec in C.FEATURES:
        assert f'href="#{sec.id}"' in page, f"{sec.id} is not in the table of contents"
        assert f'id="{sec.id}"' in page, f"{sec.id} has no anchor to jump to"


# ── SEO ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("needle", [
    '<meta name="description"',
    'rel="canonical" href="https://highlightz.app/tutorial"',
    'property="og:title"',
    'property="og:image"',
    'name="twitter:card" content="summary_large_image"',
])
def test_the_page_carries_its_seo_tags(page, needle):
    assert needle in page


def test_the_title_and_description_are_useful_lengths(page):
    title = re.search(r"<title>(.*?)</title>", page).group(1)
    desc = re.search(r'name="description" content="(.*?)"', page).group(1)
    assert 20 < len(title) <= 70, f"title is {len(title)} chars"
    assert 70 < len(desc) <= 200, f"description is {len(desc)} chars"


def test_the_page_never_promises_a_retired_price_or_the_wrong_trial(page):
    """The same failure the social card had: copy that outlives the offer.

    The 7-day trial is real again, so saying so is correct — but the NUMBER has
    to come from TRIAL_DAYS. A page confidently quoting a trial length the code
    does not grant is the same class of bug as the retired $15."""
    from src.billing.plans import TRIAL_DAYS
    import re as _re
    assert "$15" not in page, "the tutorial quotes the retired price"
    for n in _re.findall(r"(\d+)\s*days? free", page, _re.I):
        assert int(n) == TRIAL_DAYS, \
            f"the tutorial advertises a {n}-day trial but TRIAL_DAYS is {TRIAL_DAYS}"
