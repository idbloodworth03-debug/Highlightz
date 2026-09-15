"""The Terms, Privacy Policy and Cookie Policy must describe the real product.

WHY THIS FILE EXISTS. An audit on 2026-08-27 found fifteen places where the
three published documents and the application disagreed. Two were statements
that were simply false: both documents said no Kick credentials are requested
or stored while a Connect button stored the access AND refresh token, and the
Terms said a paid subscription was required two months after the free tier
reopened.

Neither was written carelessly. Both were TRUE when written and quietly became
false when the code moved underneath them — which is exactly the failure a test
can catch and a proofread cannot. So these tests do not check that the prose is
good; they check the handful of claims that have a fact in the codebase to be
checked against.

The pattern throughout: assert the claim and the code TOGETHER, so removing
either one fails. A test that only greps the document proves nothing about the
product.
"""

import re

import pytest

from src.billing.plans import PLAN_LIMITS
from src.dashboard.api import COOKIES_HTML, PRIVACY_HTML, TOS_HTML, app

DOCS = {"Terms": TOS_HTML, "Privacy Policy": PRIVACY_HTML, "Cookie Policy": COOKIES_HTML}


def _text(html: str) -> str:
    """Rendered copy only — comments and style blocks are not claims."""
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    return html


def test_the_stripper_actually_strips():
    """Guards the guard: if _text stopped removing comments, every 'the document
    does not say X' test below would silently start reading code notes."""
    assert "secret" not in _text("<p>a</p><!-- secret --><style>secret{}</style>")


# ── the two false statements ─────────────────────────────────────────────────

def test_no_document_denies_storing_kick_credentials_while_we_store_them():
    """One assertion, deliberately, over both halves.

    The denial is only safe while nothing can write the credentials. Re-adding a
    Kick OAuth route without rewriting the documents fails here; so does keeping
    the route and deleting the sentence, because then the claim is unsupported
    rather than false.
    """
    routes = {getattr(r, "path", "") for r in app.routes}
    kick_oauth_live = any(p.startswith("/auth/kick") for p in routes)

    denials = [n for n, d in DOCS.items()
               if re.search(r"no kick (account )?credentials", _text(d), re.I)]
    if kick_oauth_live:
        assert not denials, (
            f"{denials} still say no Kick credentials are stored, but a Kick "
            f"OAuth route is registered again")
    else:
        assert denials, ("the Kick OAuth flow is gone but no document says so — "
                         "the claim was dropped instead of becoming true")


def test_no_document_claims_the_service_is_paid_only():
    """PLAN_LIMITS has a free plan, so no document may say payment is required."""
    assert PLAN_LIMITS["free"]["price"] == 0, "the free plan is gone — fix these docs"
    for name, doc in DOCS.items():
        body = _text(doc).lower()
        for claim in ("requires an active paid subscription",
                      "access to the service requires a paid subscription",
                      "requires a paid subscription to access"):
            assert claim not in body, f"the {name} still says the product is paid-only"


def test_the_terms_describe_the_real_plan_ladder():
    """Section 4 is generated, so moving a limit must move the sentence."""
    from src.dashboard.api import _tos_plans
    f = PLAN_LIMITS["free"]
    body = _tos_plans()
    for n in (f["max_streams"], f["max_pending"], f["max_suggested"],
              PLAN_LIMITS["starter"]["price"], PLAN_LIMITS["pro"]["price"]):
        assert str(n) in body, f"the Terms no longer quote {n}"

    # Derived, not typed. `str(20) in text` passes either way; moving the limit
    # is the only thing that tells them apart.
    d = PLAN_LIMITS["free"]
    before = d["max_pending"]
    d["max_pending"] = 4242
    try:
        assert "4242" in _tos_plans(), "the Terms type their plan numbers"
    finally:
        d["max_pending"] = before


# ── things we do that must be disclosed ──────────────────────────────────────

@pytest.mark.parametrize("what,phrase", [
    ("chat messages stored with each clip", "chat messages"),
    ("video uploaded to the Clip Editor",   "uploaded video"),
    ("the broadcaster opt-out record",      "opt-out"),
    ("feedback submissions",                "feedback"),
    ("tuning the detector from your decisions", "how sensitive"),
])
def test_the_privacy_policy_discloses(what, phrase):
    assert phrase.lower() in _text(PRIVACY_HTML).lower(), \
        f"the Privacy Policy does not mention {what}"


def test_what_the_code_stores_on_a_clip_is_what_the_policy_describes():
    """The clip record is the list most likely to grow without the policy
    following. Every field on it is either disclosed or deliberately listed
    here as needing no disclosure."""
    import dataclasses
    from src.processor.metadata import ClipMetadata

    # Fields that carry no information about a person, or that the policy
    # covers under a broader heading.
    covered = {
        "id", "channel", "platform", "trigger_score", "trigger_signals",
        "stream_title", "game", "created_at", "storage_url", "duration_seconds",
        "status", "virality_score", "clip_title", "vertical_url", "user_id",
        "twitch_clip_id", "twitch_url", "embed_url", "thumbnail_url",
        "platform_url",           # "the channel page a Kick clip came from"
        "suggested", "clipper_count", "suggested_views",
        "chat_snapshot",          # disclosed as "chat samples"
        # Twitch's own label ON THE CHANNEL, not information about a person.
        # Disclosed anyway in the clip-metadata bullet, because the list being
        # complete is worth more than the argument that it need not be.
        "age_restricted",
    }
    actual = {f.name for f in dataclasses.fields(ClipMetadata)}
    undisclosed = actual - covered
    assert not undisclosed, (
        f"new clip field(s) {sorted(undisclosed)} — add them to the Privacy "
        f"Policy's collection list, then to `covered` here")

    assert "suggested_by" not in actual, \
        "the clipper's name is stored again; the policy says we do not keep it"


def test_the_opt_out_is_in_the_terms_that_rely_on_it():
    """Section 5 makes the user responsible for clipping other broadcasters. It
    has to say those broadcasters can opt out — that is the mechanism the
    section depends on, and it was missing entirely."""
    from src.auth import optout
    assert hasattr(optout, "opt_out"), "the opt-out flow is gone"
    body = _text(TOS_HTML)
    assert "opt out" in body.lower()
    assert "/opt-out" in body, "the Terms mention opting out but do not link it"


# ── the smaller facts ────────────────────────────────────────────────────────

def test_the_cookie_policy_lists_every_browser_key_we_write():
    """It promised 'cookies and similar technologies' and listed only the
    cookie. Read the keys out of the dashboard rather than trusting a list."""
    from src.dashboard.aurora_html import DASHBOARD_HTML
    keys = set(re.findall(r"localStorage\.setItem\(['\"]([^'\"]+)", DASHBOARD_HTML))
    assert keys, "no localStorage keys found — this test has stopped testing anything"
    for k in keys:
        assert k in COOKIES_HTML, f"the Cookie Policy does not list {k}"


def test_the_session_duration_matches_the_middleware():
    from src.dashboard import api
    mw = next(m for m in api.app.user_middleware if "Session" in str(m.cls))
    days = mw.kwargs["max_age"] / 86400
    assert f"{int(days)} days" in COOKIES_HTML, \
        f"the Cookie Policy does not say {int(days)} days"


def test_every_scope_we_request_is_explained_in_the_terms():
    from src.auth.twitch_oauth import _SCOPES
    body = _text(TOS_HTML)
    for scope in _SCOPES.split():
        assert scope in body, f"the Terms do not mention the {scope} permission"


def test_all_three_documents_are_dated_together():
    dates = {n: re.search(r"Effective date: ([^&<|]+)", _text(d)).group(1).strip()
             for n, d in DOCS.items()}
    assert len(set(dates.values())) == 1, f"the documents disagree on their date: {dates}"


def test_the_legal_pages_have_meta_descriptions():
    for name, doc in DOCS.items():
        assert re.search(r'<meta name="description" content="[^"]{40,}"', doc), \
            f"the {name} page has no meta description"


# ── the 2026-09-02 audit: claims that were false, made true ──────────────────

def test_the_learning_log_keeps_no_name_or_id_of_a_clipper(tmp_path, monkeypatch):
    """The Privacy Policy says the only record held on non-users, apart from
    the public clip records, is the opt-out list, and that those clip records
    carry a one-way code in place of the clipper. The viewer-clip log used to
    write the clipper's Twitch name AND id. Both halves are asserted: the
    fields are gone, the code is stable (so distinct clippers still count)
    and cannot be reversed."""
    import json
    from src.trigger import viewer_clips
    monkeypatch.setattr(viewer_clips, "_LOG_FILE", tmp_path / "v.jsonl")
    viewer_clips._record([{"clip_id": "a", "clipper": viewer_clips._pseudonym("999")}])
    row = json.loads((tmp_path / "v.jsonl").read_text().splitlines()[0])
    assert "creator" not in row and "creator_id" not in row
    assert row["clipper"] == viewer_clips._pseudonym("999")
    assert "999" not in row["clipper"] and len(row["clipper"]) == 16
    assert viewer_clips._pseudonym("999") != viewer_clips._pseudonym("998")
    assert viewer_clips._pseudonym("") == "" and viewer_clips._pseudonym(None) == ""
    # And the writer itself no longer names the fields.
    import inspect
    src = inspect.getsource(viewer_clips.poll_and_record)
    assert '"creator":' not in src and '"creator_id":' not in src
    body = _text(PRIVACY_HTML).lower()
    assert "public clip records" in body and "one-way code" in body


def test_the_showcase_drops_a_broadcaster_who_opted_out(tmp_path, monkeypatch):
    """The opt-out was checked only when a channel was added; a clip featured
    on the homepage before the opt-out stayed there. Both the loader and the
    admin feature endpoint now consult the list."""
    import json
    from src.auth import optout
    from src.dashboard import api
    monkeypatch.setattr(optout, "_OPTOUT_FILE", tmp_path / "optout.json")
    monkeypatch.setattr(api, "_SHOWCASE_FILE", tmp_path / "showcase.json")
    (tmp_path / "showcase.json").write_text(json.dumps([
        {"id": "1", "channel": "keeps"}, {"id": "2", "channel": "LeavesNow"}]))
    assert [e["id"] for e in api._load_showcase()] == ["1", "2"]
    optout.opt_out("77", "leavesnow", "LeavesNow")
    assert [e["id"] for e in api._load_showcase()] == ["1"], \
        "an opted-out broadcaster's clip is still on the landing page"
    import inspect
    assert "is_opted_out" in inspect.getsource(api.admin_toggle_showcase), \
        "featuring a clip does not check the opt-out list"


def test_opting_out_stops_monitors_that_are_already_running(monkeypatch):
    """The Terms say an opted-out channel 'will not' be clipped and the FAQ
    says the opt-out 'takes effect immediately across every account'. Only
    adding was blocked; a running monitor kept clipping to the end of the
    stream. Every user's monitor on the channel is stopped, through the same
    path the DELETE endpoint uses, so their tabs drop the row live."""
    import asyncio
    from src.dashboard import api
    sent = []

    async def fake_broadcast(msg, user_id=None):
        sent.append((msg, user_id))

    monkeypatch.setattr(api, "broadcast", fake_broadcast)
    monkeypatch.setattr(api, "_publish_remove_stream", None)
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "release_live_slot", lambda key: None)
    monkeypatch.setattr(api, "_streams", {
        "u1:leaves": {"channel": "leaves", "platform": "twitch"},
        "u2:leaves": {"channel": "leaves", "platform": "twitch"},
        "u2:stays":  {"channel": "stays",  "platform": "twitch"},
    })
    stopped = asyncio.run(api._stop_monitors_for_channel("Leaves"))
    assert stopped == 2
    assert set(api._streams) == {"u2:stays"}
    assert sorted(uid for m, uid in sent if m["event"] == "stream_removed") == ["u1", "u2"]
    import inspect
    assert "_stop_monitors_for_channel" in inspect.getsource(api.optout_confirm_submit)
    assert "already running is stopped" in _text(TOS_HTML)


def test_the_terms_cover_clips_the_user_did_not_create():
    """Highlight clips are other Twitch users' clips. Section 5 used to say
    every clip is created with your account; now it says what a Highlight
    clip is, whose it stays, and that approving keeps a link, not a copy —
    without describing how they are found."""
    body = _text(TOS_HTML)
    assert "Highlight clips are someone else" in body
    assert "created on Twitch by another Twitch user" in body
    assert "keeps a link to it" in body
    for tell in ("audience interest", "viewers clipped", "spike"):
        assert tell not in body.lower(), f"the Terms describe the mechanism: {tell!r}"
    assert "spike in audience interest" not in _text(PRIVACY_HTML).lower(), \
        "the Privacy Policy describes the mechanism"


def test_the_cookie_policy_describes_the_session_cookie_honestly():
    """Starlette's session cookie is SIGNED, not encrypted, and it carries the
    session's contents. The policy claimed an encrypted identifier with no
    personal data in it. The keys the code writes are checked against the
    description so the two cannot drift apart again."""
    from pathlib import Path
    src = Path("src/dashboard/api.py").read_text()
    written = set(re.findall(r'request\.session\["([a-z_]+)"\]\s*=', src))
    assert {"user_id", "username", "avatar_url", "subscription_status"} <= written
    body = _text(COOKIES_HTML)
    assert "encrypted session identifier" not in body
    assert "signed so they cannot be altered" in body
    for what in ("account id", "Twitch username", "avatar URL", "plan status"):
        assert what in body, f"the session cookie's {what} is not disclosed"


def test_the_privacy_policy_lists_the_account_fields_the_code_keeps():
    from pathlib import Path
    src = Path("src/auth/users.py").read_text()
    for key in ("last_login_at", "checkout_started_at", '"ref"'):
        assert key in src, f"{key} is no longer stored — drop it from the policy"
    body = _text(PRIVACY_HTML).lower()
    for phrase in ("when you last signed in", "referral code", "opened the payment page"):
        assert phrase in body, f"the Privacy Policy does not disclose: {phrase!r}"


def test_the_refund_and_notice_clauses_match_what_the_code_can_do():
    """A refund path exists (duplicate-signup remediation) and email is
    optional (the scope can be declined), so 'no refunds' and 'notice to your
    email' were each half-true."""
    from src.billing import stripe_billing
    assert hasattr(stripe_billing, "refund_and_cancel_subscription")
    body = _text(TOS_HTML)
    assert "at our discretion" in body
    assert "where we hold one" in body


# ── the same drift, on every other public surface ────────────────────────────

def _public_surfaces() -> dict:
    """Everything a visitor or customer can read that quotes a plan number."""
    import importlib
    from src.dashboard import api as _api
    from src.dashboard import compare_content as _cc, compare_html as _ch
    from src.dashboard import tutorial_content as _tc
    importlib.reload(_cc); importlib.reload(_ch); importlib.reload(_tc)
    return {
        "landing":  _api.LANDING_HTML,
        "terms":    _api.TOS_HTML,
        "paywall":  _api.PAYWALL_HTML,
        "compare":  _ch.render(),
        "tutorial": " ".join(" ".join(r) for r in _tc.PLAN_ROWS)
                    + " " + _tc.QUICKSTART_LEAD,
    }


# Which plans each surface actually advertises. The paywall is the upgrade
# page and shows only the paid tiers, so checking it against Free's numbers
# would be asking it to quote something it deliberately does not.
_SURFACE_PLANS = {
    "landing":  ("free", "starter", "pro"),
    "terms":    ("free", "starter", "pro"),
    "paywall":  ("starter", "pro"),
    "compare":  ("free", "starter", "pro"),
    "tutorial": ("free", "starter", "pro"),
}


def _visible(html: str) -> str:
    """Rendered words only. The first version of this matched raw HTML, so a
    pixel value in a stylesheet counted as a plan number."""
    html = re.sub(r"<style.*?</style>|<script.*?</script>|<!--.*?-->", " ",
                  html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _quotes(text: str, n: int) -> bool:
    """Whole numbers only. "20" is inside "200-clip queue" and "5" is inside
    "$25", which made the first version of this fail on pages that were
    perfectly correct."""
    return re.search(rf"(?<!\d){n}(?!\d)", text) is not None


# FREE ONLY, and that is a limitation worth stating rather than hiding.
#
# Free is where the advertised numbers live: it is the plan every public page
# describes in full, and both bugs this test exists for were free's. The paid
# tiers are quoted more selectively, and a bare-number sweep cannot tell one
# use of a figure from another — Starter's max_suggested is 25 and Pro's price
# is $25, so demanding every page "follow" a moved 25 fails on pages that are
# entirely correct. The paid tiers are covered by the specific tests below.
@pytest.mark.parametrize("limit,probe", [
    ("max_streams", 8181), ("max_pending", 4242),
    ("max_suggested", 3737), ("max_library_week", 9595),
])
def test_every_public_surface_follows_a_moved_plan_limit(limit, probe):
    """The class of bug, not the instances of it.

    Re-auditing found /compare still advertising "3 crowd suggestions" after
    free moved to 5, and the paywall — the page where somebody decides to pay —
    quoting every figure as a literal and never mentioning the weekly keep cap
    at all. Both were missed twice by reading the legal documents, because the
    documents were right; it was the OTHER pages that had gone stale.

    So this checks no particular sentence. It moves a limit and asserts every
    surface that advertises that plan moves with it. A new page that types its
    numbers fails here on the day it is written.
    """
    plan = "free"
    import importlib
    from src.dashboard import api as _api

    original = PLAN_LIMITS[plan][limit]
    before = {n: _visible(h) for n, h in _public_surfaces().items()}
    PLAN_LIMITS[plan][limit] = probe
    try:
        importlib.reload(_api)
        after = {n: _visible(h) for n, h in _public_surfaces().items()}
    finally:
        # Restore BEFORE re-rendering: compare_content builds its plan lines at
        # import time, so a reload while the probe is still in place bakes the
        # probe into module state and leaks it into every later test.
        PLAN_LIMITS[plan][limit] = original
        importlib.reload(_api)
        _public_surfaces()

    stale = [n for n, plans in _SURFACE_PLANS.items()
             if plan in plans
             and _quotes(before[n], original)
             and not _quotes(after[n], probe)]
    assert not stale, (
        f"{stale} quote {plan}'s {limit} but do not follow it — they type the "
        f"number instead of reading PLAN_LIMITS")


def test_the_compare_page_describes_our_real_plans():
    from src.dashboard.compare_content import HIGHLIGHTZ
    free = PLAN_LIMITS["free"]
    note = next(p.note for p in HIGHLIGHTZ.plans if p.name == "Free")
    assert str(free["max_suggested"]) in note, "the compare page's own numbers are stale"
    assert str(free["max_library_week"]) in note, "it omits the weekly keep limit"


def test_the_upgrade_page_names_what_upgrading_buys():
    """The weekly cap is a main reason to upgrade; the paywall never said so."""
    from src.dashboard.api import PAYWALL_HTML
    st = PLAN_LIMITS["starter"]
    assert f"{st['max_library_week']} clips kept a week" in PAYWALL_HTML
    assert "Unlimited clips kept" in PAYWALL_HTML, "Pro's headline benefit is missing"
    assert "1000000000" not in PAYWALL_HTML, "the sentinel reached the page"


def test_the_privacy_policy_quotes_the_real_chat_sample_size():
    """It said "up to 30" and the worker sliced [-30:]. Two literals that could
    disagree, in a claim about how much of other people's chat we retain."""
    from src.dashboard.api import PRIVACY_HTML
    from src.ingestion.stream_worker import CHAT_SNAPSHOT_MESSAGES
    assert f"up to {CHAT_SNAPSHOT_MESSAGES} of the chat messages" in PRIVACY_HTML
    assert "<!--CHATN-->" not in PRIVACY_HTML, "the placeholder was left unfilled"


def test_the_worker_actually_slices_by_the_constant_the_policy_quotes():
    """Naming the constant is only half of it.

    Mutation testing moved CHAT_SNAPSHOT_MESSAGES from 30 to 50 and nothing
    failed — correctly, because the policy derives from it and both moved
    together. That mutant is equivalent. The failure it could NOT reach is the
    one worth pinning: someone leaving the constant at 30 while the slice goes
    back to a literal. Then the policy quotes a number the code does not use,
    which is the original bug wearing a constant.
    """
    import inspect
    from src.ingestion import stream_worker
    src = inspect.getsource(stream_worker)
    # The chat_snapshot assignment specifically. A broader search matches the
    # sentiment sampler a few hundred lines up, which also reads the last N
    # messages but stores nothing — narrowing this was the difference between
    # a test about retention and a test about any slice in the file.
    m = re.search(r"chat_snapshot=\((.*?)\)", src, re.S)
    assert m, "chat_snapshot is no longer assigned where this test looks"
    assign = m.group(1)
    assert "CHAT_SNAPSHOT_MESSAGES" in assign, \
        "the stored chat sample is sliced by a literal again, not the constant "
    assert not re.search(r"\[-\d+:\]", assign), \
        "a hardcoded slice length is back on the stored chat sample"


def test_the_drift_sweep_cannot_be_fooled_by_a_stylesheet():
    """Guards the guard.

    The first version of the sweep matched raw HTML, so a `padding:20px` in a
    stylesheet read as the page quoting free's 20-clip queue — which is how it
    reported four perfectly correct pages as stale. If _visible stops stripping,
    the sweep goes back to reporting noise and stops being trusted.
    """
    styled = '<style>.x{padding:4242px}</style><p>Twenty clips</p>'
    assert not _quotes(_visible(styled), 4242), \
        "_visible lets stylesheet numbers count as page copy"
    assert "Twenty clips" in _visible(styled), "_visible ate the actual copy"
    # And whole-number matching, or "20" matches inside "200-clip queue".
    assert not _quotes("a 200-clip queue", 20)
    assert _quotes("a 20-clip queue", 20)
