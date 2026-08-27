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
        "suggested", "clipper_count", "suggested_views",
        "chat_snapshot",          # disclosed as "chat samples"
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
