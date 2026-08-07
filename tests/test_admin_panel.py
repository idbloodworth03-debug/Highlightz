"""The admin panel's numbers, and where the old ones were wrong.

THE BUG THIS EXISTS FOR. The header's "Total Clips" was computed in the browser
as `sum(u.clip_count)` over the user list, where `clip_count` is how many rows
that user currently has in `_clips`. That silently excluded:

  * every rejected clip — /clips/{id}/reject DELETES the row
  * every clip that aged out of the queue
  * every clip dropped because the queue was full
  * every clip belonging to an admin account, because the sum filtered admins
    out to keep the user counts clean

So a system that had caught five figures of clips reported a few hundred, and
the figure went DOWN whenever anyone tidied their queue. The accurate numbers
already existed in two places — the persisted clip counter (the same one the
landing page prints) and the stream_stats ledger, which records events and
therefore survives the clip being deleted. Neither was being read.

Membership had the same shape of problem: the table showed the raw stored
`plan`, which disagrees with what the user actually has in four separate cases.
"""

import base64
import json as _j
import time

import pytest
from itsdangerous import TimestampSigner


NOW = time.time()

PEOPLE = {
    # the operator
    "boss":   {"id": "boss", "username": "boss", "is_admin": True,
               "created_at": NOW - 400 * 86400},
    # a paying Pro subscriber. The stripe_customer_id is not decoration: it is
    # what separates revenue from generosity now that an admin can comp a
    # specific tier, so every genuinely paying fixture must carry one exactly
    # as the Stripe webhook would set it.
    "nova":   {"id": "nova", "username": "nova", "subscription_status": "active",
               "plan": "pro", "stripe_customer_id": "cus_nova",
               "created_at": NOW - 60 * 86400},
    # a paying Starter subscriber
    "kat":    {"id": "kat", "username": "kat", "subscription_status": "active",
               "plan": "starter", "stripe_customer_id": "cus_kat",
               "created_at": NOW - 3 * 86400},
    # comped Starter by an admin: active and on a priced tier, but nobody is
    # paying for it. Reads identically to `kat` apart from plan_source.
    "gifted": {"id": "gifted", "username": "gifted", "subscription_status": "active",
               "plan": "starter", "plan_source": "granted",
               "created_at": NOW - 10 * 86400},
    # an admin-granted trial: reads as Pro, pays nothing
    "moon":   {"id": "moon", "username": "moon", "subscription_status": "trialing",
               "created_at": NOW - 2 * 86400},
    # cancelled: stored plan still says pro, but they are on free now
    "drift":  {"id": "drift", "username": "drift", "subscription_status": "inactive",
               "plan": "pro", "created_at": NOW - 200 * 86400},
    # a $15-era subscriber with NO stored plan — grandfathered to pro
    "legacy": {"id": "legacy", "username": "legacy", "subscription_status": "active",
               "stripe_customer_id": "cus_legacy", "created_at": NOW - 500 * 86400},
    # signed up, never subscribed
    "lurker": {"id": "lurker", "username": "lurker", "created_at": NOW - 1 * 86400},
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    from src.stats import stream_stats as ss

    monkeypatch.setattr(user_store, "get_all", lambda: [dict(u) for u in PEOPLE.values()])
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(api, "_CLIP_COUNTER_FILE", tmp_path / "counter.json")
    monkeypatch.setattr(api, "_clip_counter", None, raising=False)

    # The ledger: 100 caught on nova's channel, of which only a handful are
    # still sitting in _clips. This is the gap the old header could not see.
    ss.record(ss.CAUGHT, {"user_id": "nova", "channel": "novafps"})
    for _ in range(60):
        ss.record(ss.APPROVED, {"user_id": "nova", "channel": "novafps"})
    for _ in range(30):
        ss.record(ss.REJECTED, {"user_id": "nova", "channel": "novafps"})
    for _ in range(10):
        ss.record(ss.EXPIRED, {"user_id": "nova", "channel": "novafps"})

    api._clips.clear()
    # Two clips still in storage, one of them owned by the ADMIN — the old sum
    # dropped admin-owned clips entirely.
    api._clips["a"] = {"id": "a", "user_id": "nova", "channel": "novafps", "status": "pending"}
    api._clips["b"] = {"id": "b", "user_id": "boss", "channel": "bossch", "status": "approved"}
    api._streams.clear()
    api._streams["nova:novafps"] = {"user_id": "nova", "channel": "novafps", "status": "live"}
    api._streams["nova:other"] = {"user_id": "nova", "channel": "other", "status": "offline"}

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": uid, "username": uid,
             "is_admin": PEOPLE[uid].get("is_admin", False),
             "subscription_status": "active"}).encode())).decode())
        return c

    c.login = login
    c.api = api
    yield c
    api._clips.clear()
    api._streams.clear()


def _overview(client, counter=None):
    if counter is not None:
        client.api._clip_counter = counter
    return client.login("boss").get("/admin/overview").json()


# ── the counter ──────────────────────────────────────────────────────────────

def test_the_clip_total_is_the_lifetime_counter_not_what_is_left_in_storage(client):
    """The headline figure must be the all-time count. Storage holds 2 clips."""
    d = _overview(client, counter=13464)
    assert d["clips"]["lifetime"] == 13464
    assert d["clips"]["stored"] == 2
    assert d["clips"]["lifetime"] != d["clips"]["stored"], \
        "the header is reporting storage again"


def test_the_lifetime_total_is_the_same_number_the_landing_page_prints(client):
    """Two surfaces quoting different clip totals is worse than either being
    slightly stale, so both read the same persisted counter."""
    client.api._clip_counter = 777
    d = client.login("boss").get("/admin/overview").json()
    assert d["clips"]["lifetime"] == client.api.get_clip_counter() == 777


def test_rejected_and_aged_out_clips_still_count_as_caught(client):
    """A rejected clip is deleted from _clips, so counting rows makes the total
    fall every time someone tidies their queue. The ledger records events."""
    d = _overview(client, counter=1)
    c = d["clips"]
    assert c["kept"] == 60
    assert c["rejected"] == 30
    assert c["expired"] == 10
    # 60 kept of 90 actually reviewed.
    assert c["keep_rate"] == 67, c["keep_rate"]


def test_admin_owned_clips_are_not_dropped_from_the_clip_figures(client):
    """Admins were filtered out to keep the USER counts honest, and the clip
    counts inherited that filter by accident. An admin's clips are real."""
    d = _overview(client, counter=1)
    assert d["clips"]["stored"] == 2, "the admin's stored clip vanished again"
    assert d["clips"]["approved"] == 1 and d["clips"]["pending"] == 1


def test_streams_report_live_separately_from_registered(client):
    d = _overview(client, counter=1)
    assert d["streams"]["registered"] == 2
    assert d["streams"]["live"] == 1


# ── revenue and population ───────────────────────────────────────────────────

def test_mrr_counts_only_money_that_actually_arrives(client):
    """Pro (25) + Starter (10). The trial reads as Pro and the admin reads as
    Pro, and neither of them pays anything."""
    d = _overview(client, counter=1)
    assert d["mrr"] == 35, d["mrr"]
    assert d["users"]["paying"] == 3           # nova, kat and the legacy sub
    assert d["users"]["trialing"] == 1
    assert d["users"]["comped"] == 1, "the comped Starter is being counted as a customer"


def test_a_legacy_subscriber_is_never_priced_at_the_tier_they_were_given(client):
    """ENTITLEMENT is not PRICE. A $15-era subscriber is grandfathered to Pro so
    they keep every feature, but they are not paying $25 and we do not hold
    their real price locally. Billing them at the Pro price here would inflate
    MRR by the difference on every one of them, silently and forever.
    """
    d = _overview(client, counter=1)
    assert d["mrr"] == 35, "the legacy subscriber is being priced at the Pro rate"
    assert d["mrr_unknown"] == 1, "MRR does not admit it is a floor"
    # They still count as a subscriber and still resolve to Pro for features.
    assert d["users"]["paying"] == 3
    assert d["users"]["by_plan"]["pro"] == 3


def test_the_legacy_subscriber_is_counted_as_the_plan_they_actually_have(client):
    """A $15-era subscriber has no stored plan and is grandfathered to Pro.
    Counting them as Free would understate both the population and the revenue.
    """
    d = _overview(client, counter=1)
    # nova (pro), legacy (grandfathered pro), moon (trial resolves to pro)
    assert d["users"]["by_plan"]["pro"] == 3, d["users"]["by_plan"]
    assert d["users"]["by_plan"]["starter"] == 2   # kat pays, gifted is comped
    # drift cancelled (back to free) and lurker never subscribed
    assert d["users"]["by_plan"]["free"] == 2


def test_staff_are_excluded_from_the_population_but_counted_separately(client):
    d = _overview(client, counter=1)
    assert d["users"]["total"] == len(PEOPLE) - 1
    assert d["users"]["admins"] == 1


def test_new_signups_are_windowed(client):
    d = _overview(client, counter=1)
    assert d["users"]["new_7d"] == 3          # kat, moon, lurker
    assert d["users"]["new_30d"] == 4         # ...plus the comped account at 10 days


# ── membership on the user list ──────────────────────────────────────────────

def _users(client):
    return {u["id"]: u for u in client.login("boss").get("/admin/users").json()}


def test_the_user_list_reports_the_resolved_plan_not_the_stored_one(client):
    """Four cases where the stored field lies, and all four have to be right or
    the operator cannot tell who is paying for what."""
    u = _users(client)
    assert u["nova"]["plan"] == "pro" and u["nova"]["plan_label"] == "Pro"
    assert u["kat"]["plan"] == "starter"
    # admin: no subscription at all, gets the full product
    assert u["boss"]["plan"] == "pro"
    # trial: resolves to pro for the duration
    assert u["moon"]["plan"] == "pro"
    # cancelled: stored plan still says "pro", but they are on free
    assert u["drift"].get("subscription_status") == "inactive"
    assert u["drift"]["plan"] == "free", "a cancelled subscriber still shows as Pro"
    # legacy: no stored plan, grandfathered
    assert u["legacy"]["plan"] == "pro"
    assert u["lurker"]["plan"] == "free"


def test_is_paying_separates_customers_from_comped_and_trialling_accounts(client):
    u = _users(client)
    assert u["nova"]["is_paying"] is True
    assert u["kat"]["is_paying"] is True
    assert u["legacy"]["is_paying"] is True
    assert u["gifted"]["is_paying"] is False, "a comped Starter is being counted as revenue"
    assert u["boss"]["is_paying"] is False, "the admin is being counted as revenue"
    assert u["moon"]["is_paying"] is False, "a granted trial is being counted as revenue"
    assert u["drift"]["is_paying"] is False
    assert u["lurker"]["is_paying"] is False


def test_the_price_travels_with_the_plan(client):
    u = _users(client)
    assert u["nova"]["plan_price"] == 25
    assert u["kat"]["plan_price"] == 10
    assert u["lurker"]["plan_price"] == 0


# ── access ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/admin/overview", "/admin/users"])
def test_platform_wide_figures_are_admin_only(client, path):
    """Both endpoints span every user on the system."""
    assert client.login("nova").get(path).status_code == 403


# ── the per-user clip list in the details drawer ─────────────────────────────

def _seed_clips(api, n_pending=2, n_approved=2):
    api._clips.clear()
    # Every PENDING clip is newer than every APPROVED one. A chronological sort
    # puts them all on top, which is the behaviour being replaced.
    for i in range(n_approved):
        api._clips[f"ok{i}"] = {"id": f"ok{i}", "user_id": "nova", "channel": "novafps",
                                "status": "approved", "created_at": 1000 + i,
                                "approved_at": 5000 + i}
    for i in range(n_pending):
        api._clips[f"pd{i}"] = {"id": f"pd{i}", "user_id": "nova", "channel": "novafps",
                                "status": "pending", "created_at": 90000 + i}


def test_approved_clips_lead_the_list_even_when_pending_ones_are_newer(client):
    """The drawer answers "what did this user keep", so the kept clips come
    first. Sorting by capture time buried them under an unreviewed queue."""
    _seed_clips(client.api)
    got = client.login("boss").get("/admin/users/nova/clips").json()
    assert [c["status"] for c in got] == ["approved", "approved", "pending", "pending"]


def test_within_each_group_the_most_recent_decision_is_first(client):
    _seed_clips(client.api)
    got = client.login("boss").get("/admin/users/nova/clips").json()
    approved = [c["id"] for c in got if c["status"] == "approved"]
    assert approved == ["ok1", "ok0"], "approved clips are not newest-first"


def test_the_hundred_cap_cannot_starve_the_approved_clips(client):
    """This is why the ordering is on the SERVER. The list is capped at 100, so
    a user with a full pending queue can have 100 unreviewed clips newer than
    every clip they ever kept — order it in the browser and the panel receives a
    page with no approved clips on it at all.
    """
    _seed_clips(client.api, n_pending=150, n_approved=3)
    got = client.login("boss").get("/admin/users/nova/clips").json()
    assert len(got) == 100
    assert sum(1 for c in got if c["status"] == "approved") == 3, \
        "the cap swallowed the approved clips"
    assert [c["status"] for c in got[:3]] == ["approved"] * 3


def test_a_clip_approved_before_approved_at_existed_still_sorts(client):
    """Clips approved before that field shipped fall back to created_at rather
    than collapsing to 0 and sorting below everything.

    The fixture has to make the fallback CHANGE the order or it proves nothing:
    the unstamped clip is the newer one, so dropping the fallback sends it to
    the bottom instead of the top. An earlier version of this test used an
    unstamped clip that was already last, and the mutation passed.
    """
    client.api._clips.clear()
    client.api._clips["unstamped"] = {"id": "unstamped", "user_id": "nova", "channel": "c",
                                      "status": "approved", "created_at": 9000}
    client.api._clips["stamped"] = {"id": "stamped", "user_id": "nova", "channel": "c",
                                    "status": "approved", "created_at": 100,
                                    "approved_at": 4000}
    got = client.login("boss").get("/admin/users/nova/clips").json()
    assert [c["id"] for c in got] == ["unstamped", "stamped"], \
        "a clip with no approved_at fell to the bottom instead of using its capture time"


def test_the_panel_marks_approved_clips_with_more_than_colour(client):
    """Colour alone fails anyone who cannot distinguish the two rules, so the
    row also carries the status as a word."""
    html = client.api.ADMIN_HTML
    assert ".crow.ok{" in html, "approved rows have no distinct treatment"
    assert "var(--good)" in html[html.index(".crow.ok{"):html.index(".crow.ok{") + 200]
    assert "'Approved'" in html and "class=\"st\"" in html, \
        "the status word is gone, leaving colour as the only signal"
