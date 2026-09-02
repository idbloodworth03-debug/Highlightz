"""The weekly library cap: how many clips a plan lets you KEEP in seven days.

A DIFFERENT LIMIT FROM max_pending, and the distinction is the point. That one
caps how many undecided clips may WAIT in review; this caps how many may enter
the LIBRARY. A user can hit either first and hitting one says nothing about the
other, so every test here holds the queue well clear of its own ceiling.

WHAT THE CAP MUST NOT DO is most of what is below. Refusing an approval is easy;
refusing it without losing the clip, without blocking a re-approval, without
counting clips the user has since deleted, and without ever firing on Pro is
where this can quietly go wrong. A cap that eats a clip somebody wanted reads as
the product breaking, not as a limit.
"""

import base64
import json as _j
import time

import pytest
from itsdangerous import TimestampSigner
from starlette.testclient import TestClient

from src.billing.plans import PLAN_LIMITS, UNLIMITED_PENDING
from src.dashboard import api

WEEK = api.LIBRARY_WEEK_SECS
FREE_CAP = PLAN_LIMITS["free"]["max_library_week"]


@pytest.fixture()
def client(monkeypatch):
    from src.profiles import manager as pm, training_log
    from src.stats import stream_stats as ss

    async def _noop(*a, **k): return None
    monkeypatch.setattr(api, "broadcast", _noop)
    monkeypatch.setattr(ss, "record", lambda kind, clip: None)
    monkeypatch.setattr(training_log, "log_outcome", lambda clip, label: None)

    class _Profile:
        def record_clip(self, approved, signals): pass
        def to_dict(self): return {}

    class _PM:
        async def load(self, ch): return _Profile()
        async def save(self, p): return None
    monkeypatch.setattr(pm, "get_profile_manager", lambda uid: _PM())

    api._clips.clear()
    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": "punter", "username": "punter",
         "is_admin": False, "subscription_status": "active"}).encode())).decode())
    yield c
    api._clips.clear()


def _as_plan(monkeypatch, plan: str, *, is_admin: bool = False):
    """Resolve the signed-in user to one plan, without a Stripe round trip."""
    user = {"id": "punter", "username": "punter", "is_admin": is_admin,
            "subscription_status": "active" if plan in ("starter", "pro") else "none",
            "plan": plan if plan in ("starter", "pro") else None}
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: user)


def _seed(n: int, *, status="pending", approved_at=None, prefix="c"):
    for i in range(n):
        cid = f"{prefix}{i}"
        api._clips[cid] = {
            "id": cid, "user_id": "punter", "platform": "twitch",
            "channel": "aceu", "status": status, "created_at": time.time(),
            "clip_title": "a moment", "trigger_score": 50.0,
            "trigger_signals": [], "twitch_url": f"https://clips.twitch.tv/{cid}",
            **({"approved_at": approved_at} if approved_at is not None else {}),
        }


# ── the cap itself ───────────────────────────────────────────────────────────

def test_a_free_user_may_keep_exactly_the_weekly_allowance(client, monkeypatch):
    _as_plan(monkeypatch, "free")
    _seed(FREE_CAP + 1)
    for i in range(FREE_CAP):
        r = client.post(f"/clips/c{i}/approve")
        assert r.status_code == 200, f"blocked at {i}, before the allowance ran out"
    assert api.library_room("punter") == (FREE_CAP, FREE_CAP)


def test_the_one_past_the_allowance_is_refused(client, monkeypatch):
    _as_plan(monkeypatch, "free")
    _seed(FREE_CAP + 1)
    for i in range(FREE_CAP):
        client.post(f"/clips/c{i}/approve")
    r = client.post(f"/clips/c{FREE_CAP}/approve")
    assert r.status_code == 403
    body = r.json()["detail"]
    assert str(FREE_CAP) in body, "the message does not say what the limit is"
    assert "pgrade" in body, "the refusal does not offer the way out"


def test_the_refused_clip_is_still_there_to_approve_later(client, monkeypatch):
    """THE ONE THAT MATTERS MOST. A cap that consumes the clip it refuses is a
    bug wearing a limit's clothes: the user loses something they explicitly
    asked to keep, and no upgrade gets it back."""
    _as_plan(monkeypatch, "free")
    _seed(FREE_CAP + 1)
    for i in range(FREE_CAP):
        client.post(f"/clips/c{i}/approve")
    blocked = f"c{FREE_CAP}"
    client.post(f"/clips/{blocked}/approve")
    assert api._clips[blocked]["status"] == "pending", "the refused clip was consumed"

    # And upgrading immediately unblocks it — no waiting for the week.
    _as_plan(monkeypatch, "starter")
    assert client.post(f"/clips/{blocked}/approve").status_code == 200


def test_re_approving_a_clip_already_kept_is_never_refused(client, monkeypatch):
    """It is already counted, so the naive check would let a clip block itself:
    at exactly the cap, a double-click on an approved clip would report the
    library as full."""
    _as_plan(monkeypatch, "free")
    _seed(FREE_CAP)
    for i in range(FREE_CAP):
        client.post(f"/clips/c{i}/approve")
    assert client.post("/clips/c0/approve").status_code == 200


# ── what counts, and what does not ───────────────────────────────────────────

def test_clips_kept_last_week_do_not_count_against_this_week(client, monkeypatch):
    """A rolling window, so an old library does not permanently lock a user
    out of a limit that is supposed to be per-week."""
    _as_plan(monkeypatch, "free")
    _seed(FREE_CAP, status="approved", approved_at=time.time() - WEEK - 60,
          prefix="old")
    assert api.library_room("punter") == (0, FREE_CAP)
    _seed(1, prefix="new")
    assert client.post("/clips/new0/approve").status_code == 200


def test_a_clip_kept_just_inside_the_window_still_counts(client, monkeypatch):
    """The boundary in the other direction — otherwise the window has an
    off-by-one that hands back the whole allowance a day early."""
    _as_plan(monkeypatch, "free")
    _seed(FREE_CAP, status="approved", approved_at=time.time() - WEEK + 3600,
          prefix="recent")
    assert api.library_room("punter") == (FREE_CAP, FREE_CAP)
    _seed(1, prefix="new")
    assert client.post("/clips/new0/approve").status_code == 403


def test_deleting_a_clip_gives_the_slot_back(client, monkeypatch):
    """The cap is on what you STORE. Counting approvals from the ledger instead
    would be a truer rate limit, but it would tell a user with an empty library
    that they are out of room."""
    _as_plan(monkeypatch, "free")
    _seed(FREE_CAP + 1)
    for i in range(FREE_CAP):
        client.post(f"/clips/c{i}/approve")
    assert client.post(f"/clips/c{FREE_CAP}/approve").status_code == 403
    del api._clips["c0"]
    assert client.post(f"/clips/c{FREE_CAP}/approve").status_code == 200


def test_pending_clips_are_not_counted(client, monkeypatch):
    """The two caps are separate budgets. A queue full of undecided clips must
    not consume the allowance for keeping any of them."""
    _as_plan(monkeypatch, "free")
    _seed(FREE_CAP + 5)
    assert api.library_room("punter") == (0, FREE_CAP)


def test_another_users_library_is_not_counted(client, monkeypatch):
    _as_plan(monkeypatch, "free")
    _seed(FREE_CAP, status="approved", approved_at=time.time(), prefix="mine")
    for i in range(10):
        api._clips[f"theirs{i}"] = {"id": f"theirs{i}", "user_id": "someone_else",
                                    "status": "approved", "approved_at": time.time()}
    assert api.library_room("punter") == (FREE_CAP, FREE_CAP)


# ── the ladder ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("plan,expected", [
    ("free", 30), ("starter", 100), ("pro", UNLIMITED_PENDING)])
def test_each_plan_carries_its_own_allowance(plan, expected):
    assert PLAN_LIMITS[plan]["max_library_week"] == expected


def test_starter_is_a_real_step_up_from_free():
    """The upgrade has to buy something, or the cap is just an annoyance."""
    assert (PLAN_LIMITS["starter"]["max_library_week"]
            > PLAN_LIMITS["free"]["max_library_week"])


def test_pro_is_never_refused(client, monkeypatch):
    _as_plan(monkeypatch, "pro")
    _seed(FREE_CAP + 5)
    for i in range(FREE_CAP + 5):
        assert client.post(f"/clips/c{i}/approve").status_code == 200


def test_an_admin_is_never_refused(client, monkeypatch):
    """Staff work through a real queue to check the product; a ceiling there
    blocks the exact thing the account exists for."""
    _as_plan(monkeypatch, "free", is_admin=True)
    _seed(FREE_CAP + 3)
    for i in range(FREE_CAP + 3):
        assert client.post(f"/clips/c{i}/approve").status_code == 200


def test_the_dashboard_is_told_the_ceiling(client, monkeypatch):
    """The meter counts what was kept from the clips the browser already holds,
    but the CAP has to travel — without it the UI cannot draw the limit at all,
    and the user only meets it by being refused."""
    _as_plan(monkeypatch, "free")
    limits = client.get("/me").json()["plan_limits"]
    assert limits["max_library_week"] == FREE_CAP


# ── the surfaces that advertise it ───────────────────────────────────────────

def _plan_limit(plan, key, value):
    """Move a limit so a derivation test can tell derived from typed."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        d = PLAN_LIMITS[plan]
        before = d[key]
        d[key] = value
        try:
            yield
        finally:
            d[key] = before
    return _cm()



def test_the_pricing_columns_state_the_weekly_allowance():
    from src.dashboard.api import _pricing
    html = _pricing()
    assert f"{FREE_CAP} kept a week" in html
    assert (f"<span>Clips kept per week</span><b>{PLAN_LIMITS['starter']['max_library_week']}</b>"
            in html)
    # The sentinel is a real number in JSON and would print in full.
    assert "1000000000" not in html, "the unlimited sentinel reached the page"
    assert "<span>Clips kept per week</span><b>Unlimited</b>" in html, \
        "Pro does not advertise having no limit"



def test_the_pricing_columns_derive_the_number():
    """`str(30) in html` passes whether the 30 was read or typed."""
    from src.dashboard.api import _pricing
    with _plan_limit("free", "max_library_week", 4242):
        assert "4242 kept a week" in _pricing(), "the pricing lead types the weekly allowance"
    with _plan_limit("starter", "max_library_week", 5151):
        assert "<span>Clips kept per week</span><b>5151</b>" in _pricing()



def test_the_pricing_columns_show_the_weekly_cap_as_a_row():
    """It used to say the paid plans answer ONE question, how many channels.
    The weekly allowance is the second axis and the one that decides whether
    free is enough, so it is a row of its own in both columns."""
    from src.dashboard.api import _pricing
    html = _pricing()
    assert "answer one question" not in html
    assert html.count("<span>Clips kept per week</span>") == 2


@pytest.mark.parametrize("surface", ["terms", "tutorial"])
def test_every_plan_surface_mentions_the_weekly_cap(surface):
    if surface == "terms":
        from src.dashboard.api import _tos_plans as fn
        text = fn()
    else:
        from src.dashboard.tutorial_content import PLAN_ROWS
        text = " ".join(" ".join(r) for r in PLAN_ROWS)
    assert str(FREE_CAP) in text, f"the {surface} does not quote the weekly cap"
    assert "1000000000" not in text, f"the {surface} printed the sentinel"


def test_the_tutorial_table_has_a_row_for_it():
    from src.dashboard.tutorial_content import PLAN_ROWS
    row = next((r for r in PLAN_ROWS if "kept per week" in r[0]), None)
    assert row, "the plan table has no weekly-keep row"
    assert row[1:] == (str(FREE_CAP),
                       str(PLAN_LIMITS["starter"]["max_library_week"]),
                       "Unlimited")


def test_staff_stay_unlimited_even_if_pro_ever_gets_a_ceiling():
    """Why limits_for() overrides the weekly cap for admins at all.

    Today the override looks redundant: get_plan resolves an admin to "pro",
    and Pro has no library ceiling, so an admin is unlimited either way — which
    is precisely why mutation testing could delete the override and every other
    test here still passed. The override exists for the configuration where Pro
    is NOT unlimited, so that is the configuration this asserts.
    """
    from src.billing.plans import limits_for
    admin = {"id": "a1", "is_admin": True, "subscription_status": "none"}
    with _plan_limit("pro", "max_library_week", 100):
        assert limits_for(admin)["max_library_week"] >= UNLIMITED_PENDING, \
            "staff inherit Pro's ceiling instead of being exempt from it"
        # And the exemption is specific to staff, not a blanket unlock.
        punter = {"id": "u1", "is_admin": False, "subscription_status": "active",
                  "plan": "pro"}
        assert limits_for(punter)["max_library_week"] == 100
