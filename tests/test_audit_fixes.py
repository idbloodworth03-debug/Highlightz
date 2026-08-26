"""Findings 1–5 from the site audit, held down.

Each of these was a real defect found by auditing rather than by a failing test,
which means nothing was stopping them coming back. The docstrings say what the
defect WAS, because in every case the wrong version looked perfectly reasonable.
"""

import re

import pytest

from src.billing.plans import get_plan, limits_for


# ── 1. the lapse message named a plan the user does not get ──────────────────

def test_the_lapse_message_matches_the_plan_they_actually_land_on():
    """It said "You are on the free plan now — one stream" to everyone. Only a
    grandfathered account lands on free; everyone else lands on `locked` with
    ZERO streams, so the product was promising a stream it had just taken
    away, at the moment somebody was deciding whether to come back."""
    from src.dashboard.api import _lapse_message

    free = _lapse_message("free")
    assert "free plan" in free and "one stream" in free

    locked = _lapse_message("locked")
    assert "free plan" not in locked, \
        "a locked user is still told they are on the free plan"
    assert "one stream" not in locked, \
        "a locked user is still promised a stream they do not have"
    assert "clips are still" in locked, "the one true reassurance was dropped"


def test_the_claim_the_message_makes_is_true_for_each_plan():
    """Checks the copy against the code rather than against itself."""
    from src.dashboard.api import _lapse_message
    grandfathered = {"subscription_status": "canceled", "grandfathered": True}
    assert get_plan(grandfathered) == "free"
    assert limits_for(grandfathered)["max_streams"] == 1
    assert "one stream" in _lapse_message(get_plan(grandfathered))

    # These two used to land on `locked` and the message had to promise them
    # NOTHING. With the free tier reopened they land where the grandfathered
    # account above does, so the same promise is now true for them — and the
    # test that matters is that the copy tracks the code rather than that it
    # says any particular thing.
    for label, user in (
        ("post-cutover", {"subscription_status": "canceled"}),
        ("pre-card-cutover", {"subscription_status": "canceled",
                              "pre_card_cutover": True}),
    ):
        assert get_plan(user) == "free", label
        assert limits_for(user)["max_streams"] == 1, label
        assert "one stream" in _lapse_message(get_plan(user)), label


def test_an_unknown_plan_promises_nothing_rather_than_guessing():
    """Failing toward a promise is the expensive direction in billing copy."""
    from src.dashboard.api import _lapse_message
    assert "free plan" not in _lapse_message("something_new")


@pytest.mark.parametrize("fn_name", ["_process_stripe_event", "reconcile_one_user"])
def test_neither_lapse_path_writes_its_own_copy(fn_name):
    """There were two copies of this sentence, disagreeing with each other and
    with the code. Two copies is how it went wrong."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(getattr(api, fn_name))
    assert "_lapse_message(" in src, f"{fn_name} still writes its own lapse copy"
    assert "You are on the free" not in src


def _capture_lapse_broadcast(monkeypatch, api, run):
    said = []

    async def _bcast(msg, user_id=None):
        said.append(msg)
    monkeypatch.setattr(api, "broadcast", _bcast)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "_enforce_stream_limit", _noop, raising=False)
    run()
    return [m for m in said if m.get("event") == "subscription_expired"]


def test_the_webhook_lapse_tells_a_lapsing_user_the_truth(tmp_path, monkeypatch):
    """DRIVEN, not grepped. The source check above passes if somebody writes
    `_lapse_message("free")` — which is the original bug with an extra function
    call in front of it, and mutation testing walked straight through it.

    The expected answer flipped when the free tier reopened: a lapse lands on
    free again, so the message SHOULD now promise the one stream. What is being
    tested is unchanged — the copy has to be derived from the plan the user
    really lands on, not assumed."""
    import asyncio, time as _t
    from src.dashboard import api
    from src.auth import users as user_store, trial_ledger
    from src.billing import stripe_billing as sb

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(trial_ledger, "_LEDGER_FILE", tmp_path / "trials.json")
    monkeypatch.setattr(api, "_stripe_processed", {})

    async def _others(*a, **k):
        return False
    monkeypatch.setattr(sb, "has_other_live_subscription", _others)

    u = user_store.upsert_twitch_user("tw_lapse", "nova", "nova")
    user_store.update_subscription(u["id"], "cus_l", "active")
    assert get_plan(user_store.get_by_id(u["id"])) != "free"

    ev = {"id": "evt_lapse", "type": "customer.subscription.deleted",
          "data": {"object": {"id": "sub_1", "customer": "cus_l", "status": "canceled",
                              "metadata": {"user_id": u["id"]}}}}
    msgs = _capture_lapse_broadcast(
        monkeypatch, api,
        lambda: asyncio.run(api._process_stripe_event(ev, _t.time(), ev["id"])))

    assert msgs, "a lapse fired no notice at all"
    text = msgs[0]["message"]
    landed = get_plan(user_store.get_by_id(u["id"]))
    assert landed == "free"
    assert "free plan" in text, f"a free user was not told where they landed: {text!r}"
    assert "one stream" in text, f"a free user was not told what they keep: {text!r}"
    assert text == api._lapse_message(landed), \
        "the broadcast copy is not the copy for the plan they resolved to"


def test_the_webhook_lapse_still_says_free_to_someone_who_gets_free(tmp_path, monkeypatch):
    """The other direction. Fixing the lie must not remove the true version —
    a grandfathered account really does keep one stream, and that is the most
    reassuring thing we can tell them."""
    import asyncio, time as _t
    from src.dashboard import api
    from src.auth import users as user_store, trial_ledger
    from src.billing import stripe_billing as sb

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(trial_ledger, "_LEDGER_FILE", tmp_path / "trials.json")
    monkeypatch.setattr(api, "_stripe_processed", {})

    async def _others(*a, **k):
        return False
    monkeypatch.setattr(sb, "has_other_live_subscription", _others)

    u = user_store.upsert_twitch_user("tw_legacy", "old", "old")
    rows = user_store._load()
    for r in rows:
        if r["id"] == u["id"]:
            r["grandfathered"] = True
    user_store._save(rows)
    user_store.update_subscription(u["id"], "cus_g", "active")

    ev = {"id": "evt_g", "type": "customer.subscription.deleted",
          "data": {"object": {"id": "sub_2", "customer": "cus_g", "status": "canceled",
                              "metadata": {"user_id": u["id"]}}}}
    msgs = _capture_lapse_broadcast(
        monkeypatch, api,
        lambda: asyncio.run(api._process_stripe_event(ev, _t.time(), ev["id"])))

    assert get_plan(user_store.get_by_id(u["id"])) == "free"
    assert "one stream" in msgs[0]["message"]


def test_the_reconcile_lapse_tells_a_lapsing_user_the_truth(monkeypatch):
    """Same assertion against the other path, which had its own copy."""
    import asyncio
    from src.dashboard import api
    from src.auth import users as user_store
    from src.billing import stripe_billing as sb

    monkeypatch.setattr(user_store, "update_subscription", lambda *a, **k: None)
    monkeypatch.setattr(user_store, "set_plan", lambda *a: None)
    monkeypatch.setattr(user_store, "get_by_id",
                        lambda uid: {"id": uid, "subscription_status": "canceled"})

    async def _truth(cust):
        return {"status": "inactive", "plan": None, "raw": "canceled",
                "trial_end": 0, "subscription": ""}
    monkeypatch.setattr(sb, "authoritative_subscription", _truth)

    user = {"id": "u9", "subscription_status": "active", "plan": "pro",
            "stripe_customer_id": "cus_x"}
    msgs = _capture_lapse_broadcast(
        monkeypatch, api, lambda: asyncio.run(api.reconcile_one_user(user)))
    assert msgs, "reconcile downgraded somebody silently"
    # Flipped with the free tier, same as the webhook path above: this account
    # lands on free, so promising the one stream is now the TRUE version.
    assert get_plan({"subscription_status": "canceled"}) == "free"
    assert msgs[0]["message"] == api._lapse_message("free"), \
        "reconcile is not using the copy for the plan the user resolved to"


# ── 2. the sign-in page contradicted its own badge ───────────────────────────

def test_the_sign_in_page_does_not_contradict_its_own_badge():
    """The original defect: two lines apart it read "card required" and "Paid
    plans are optional". Both halves have moved since — the card is no longer
    required and paid plans genuinely ARE optional — so what survives is the
    rule, not either sentence: the badge and the prose below it must agree.

    "Paid plans are optional" is allowed again precisely because it is true
    again. What is banned is the badge claiming a card while the prose says the
    plans can be skipped, in either direction."""
    from src.dashboard.api import LOGIN_HTML as html
    low = html.lower()
    demands_card = bool(re.search(r"(?<!no )card required", low))
    says_optional = "optional" in low or "no card" in low
    assert not (demands_card and says_optional), \
        "the sign-in page demands a card and calls the plans optional"
    # And it still says what it costs — the one thing a visitor is looking for.
    assert "$10/month" in html


# ── 3. the tutorial page overflowed sideways ─────────────────────────────────

def test_the_tutorial_nav_collapses_before_it_overflows():
    """Measured: the nav needs 818px with its links shown, and the rule hid
    them only below 700 — so between 800 and 940 the Get started button hung
    38px off the right edge of a tablet. The nav is a copy of the landing
    page's, so it takes the landing page's breakpoint."""
    from src.dashboard.tutorial_html import _CSS
    from src.dashboard.api import LANDING_HTML

    m = re.search(r"@media\(max-width:(\d+)px\)\{\s*\.nav-links\{display:none\}", _CSS)
    assert m, "the tutorial nav no longer collapses at all"
    tut_bp = int(m.group(1))
    assert tut_bp >= 820, f"the nav collapses at {tut_bp}px but needs 818px of room"

    land = re.search(r"@media\(max-width:(\d+)px\)\{\s*\.nav-links\{display:none\}",
                     LANDING_HTML)
    assert land and int(land.group(1)) == tut_bp, \
        "the shared header's two copies disagree about when to collapse"


@pytest.mark.parametrize("selector", [r"\.faq-a", r"\.tut-steps li"])
def test_long_urls_can_break_wherever_the_page_quotes_them(selector):
    """The page quotes a full Twitch VOD link in bold — 317px of text with no
    break opportunity, wider than the column on any phone at 375px or below.

    BOTH selectors, because the first attempt at this fix put the rule on
    .faq-a alone and the page still overflowed: the offending element is a
    STEP <li>. The FAQ quotes the same URL, so it keeps the rule too."""
    from src.dashboard.tutorial_html import _CSS
    m = re.search(selector + r"\{([^}]*)\}", _CSS)
    assert m, f"{selector} rule is gone"
    assert "overflow-wrap:anywhere" in m.group(1), \
        f"a quoted URL in {selector} will push the page sideways again"


def test_the_sign_in_card_is_not_a_fixed_width():
    """It was a flat 360px, so it hung 20px off a 320px screen — and the
    overflow landed on the one button the page exists for."""
    from src.dashboard.api import LOGIN_HTML
    m = re.search(r"\.card\{([^}]*)\}", LOGIN_HTML)
    assert m, ".card rule is gone"
    # Parsed into declarations, not substring-matched: "width:360px" is a
    # substring of "max-width:360px", so the naive check passed against the
    # FIXED css and would have passed against the broken one too.
    decls = dict(d.split(":", 1) for d in m.group(1).split(";") if ":" in d)
    assert decls.get("width") == "100%", f"width is {decls.get('width')!r}"
    assert decls.get("max-width") == "360px"


def test_the_faq_really_does_contain_an_unbreakable_url():
    """Guards the reason for the rule above. If the URL is ever removed the
    rule looks arbitrary and someone deletes it."""
    from src.dashboard import tutorial_content as C
    assert any("twitch.tv/videos/" in a for _, a in C.FAQ)


# ── 4. the cookie table overflowed on a phone ────────────────────────────────

def test_the_legal_tables_scroll_instead_of_pushing_the_page():
    """Four columns with a long Purpose cell need 376px. There is no wrapper
    element in the shared legal markup, so the table itself becomes the scroll
    box — and only at narrow widths, because as a block it stops filling its
    column."""
    from src.dashboard.api import COOKIES_HTML
    m = re.search(r"@media\(max-width:(\d+)px\)\{\s*table\{([^}]*)\}", COOKIES_HTML)
    assert m, "the legal tables no longer scroll on a phone"
    assert "overflow-x:auto" in m.group(2)
    assert "display:block" in m.group(2)
    # Not applied at desktop width, where a block table under-fills its column.
    assert int(m.group(1)) <= 700
    # The rule has to reach the page that actually carries a table.
    assert "<table>" in COOKIES_HTML


# ── 5. one screen was missing its reconnect listener ─────────────────────────

def test_every_mount_fetching_screen_re_pulls_on_reconnect():
    """Contract rule 3. Feedback was the only screen without the listener: the
    nav badge lit on reconnect because loadFbUnread is in refetchAll, while the
    thread on screen stayed stale — told you have a reply by a badge, on the
    screen that is supposed to be showing it."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    missing = []
    for m in re.finditer(r"function (\w+Screen|ClipEditor)\s*\(", h):
        name = m.group(1)
        nxt = h.find("\nfunction ", m.start() + 10)
        body = h[m.start():nxt if nxt > 0 else len(h)]
        # Screens that GET state of their own on mount, as opposed to screens
        # handed everything as props (which refetchAll already covers).
        if not re.search(r"fetch\('/[^']*'\)", body):
            continue
        if "hz_refetch" not in body:
            missing.append(name)
    assert not missing, f"screens that go stale after a reconnect: {missing}"


def test_the_feedback_screen_removes_both_listeners():
    """Adding a listener without removing it leaks one per mount, and this
    screen is opened and closed constantly."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    i = h.index("const loadThreads = useCallback")
    body = h[i:h.index("const [category", i)]
    assert body.count("addEventListener") == body.count("removeEventListener") == 2
