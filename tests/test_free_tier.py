"""The free tier, exercised through the real app rather than through helpers.

The growth plan depends on one thing being true: someone can sign in with no
card and use the product. Before this existed, AuthMiddleware redirected every
non-subscriber to /billing/paywall, so a signup without payment saw nothing at
all. These tests go through the middleware on purpose — asserting on plans.py
alone would not have caught that.
"""

import base64
import json as _j

import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    from src.stats import stream_stats

    people = {
        "free_user":  {"id": "free_user", "username": "freddy",
                       "subscription_status": "none"},
        "lapsed":     {"id": "lapsed", "username": "larry",
                       "subscription_status": "inactive", "plan": "pro"},
        "pro_user":   {"id": "pro_user", "username": "patty",
                       "subscription_status": "active", "plan": "pro"},
        "legacy":     {"id": "legacy", "username": "lena",
                       "subscription_status": "active"},   # $15-era, no plan
    }
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(user_store, "get_all", lambda: list(people.values()))

    async def _noop(*a, **k): return None
    monkeypatch.setattr(api, "broadcast", _noop)

    # Module-level state. The tests that add streams and clips have to start
    # from empty and leave nothing behind, or they pass alone and fail in the
    # suite — which is exactly how the first version of them behaved.
    monkeypatch.setattr(stream_stats, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)
    monkeypatch.setattr(api, "increment_clip_counter", lambda n=1: None)
    monkeypatch.setattr(api, "is_opted_out", lambda ch: False, raising=False)
    api._clips.clear()
    api._streams.clear()

    c = TestClient(api.app)

    def login(uid):
        from itsdangerous import TimestampSigner
        c.cookies.clear()
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        data = base64.b64encode(_j.dumps({
            "auth": True, "user_id": uid, "username": people[uid]["username"],
            "subscription_status": people[uid]["subscription_status"],
        }).encode())
        c.cookies.set("session", signer.sign(data).decode())
        return c

    c.login = login
    c.api = api
    c.stats = stream_stats
    yield c
    api._clips.clear()
    api._streams.clear()


def test_a_user_with_no_subscription_reaches_the_dashboard(client):
    """THE test. This 302'd to /billing/paywall before the free tier."""
    c = client.login("free_user")
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 200, f"free user was bounced: {r.status_code} {r.headers.get('location')}"


def test_a_free_user_can_read_their_own_data(client):
    c = client.login("free_user")
    for path in ("/me", "/clips", "/streams", "/profiles"):
        assert c.get(path).status_code == 200, f"{path} refused a free user"


def test_me_reports_the_free_plan_and_its_limits(client):
    c = client.login("free_user")
    me = c.get("/me").json()
    assert me["plan"] == "free"
    assert me["plan_label"] == "Free"
    assert me["plan_limits"]["max_streams"] == 1
    assert me["plan_limits"]["vod"] is False
    assert me["plan_limits"]["uploads"] is False


def test_free_users_are_still_refused_the_paid_features(client):
    """Open the door, keep the rooms locked. These are the two features that
    cost real CPU and real disk."""
    c = client.login("free_user")
    # A VALID body on purpose: FastAPI validates before the handler runs, so a
    # malformed payload returns 422 and never reaches the plan gate — the test
    # would pass without the gate existing at all.
    r = c.post("/vod/analyze",
               json={"vod_url": "https://www.twitch.tv/videos/123456789"})
    assert r.status_code == 403, f"free user was not refused the VOD scanner: {r.status_code}"
    assert c.get("/uploads").status_code in (403, 503)


def test_a_lapsed_subscriber_lands_on_free_rather_than_a_wall(client):
    c = client.login("lapsed")
    assert c.get("/", follow_redirects=False).status_code == 200
    me = c.get("/me").json()
    assert me["plan"] == "free", "a stored plan outlived the subscription"


def test_a_legacy_subscriber_is_still_grandfathered(client):
    """The $15-era customers have no `plan` field. A pricing change must never
    strip features from someone who already paid for full access."""
    c = client.login("legacy")
    me = c.get("/me").json()
    assert me["plan"] == "pro"
    assert me["plan_limits"]["vod"] is True


def test_a_paying_user_is_unaffected(client):
    c = client.login("pro_user")
    me = c.get("/me").json()
    assert me["plan"] == "pro"
    assert me["plan_limits"]["max_streams"] == 10


def test_being_signed_out_is_still_refused(client):
    """Opening the paywall must not have opened the door."""
    from fastapi.testclient import TestClient
    from src.dashboard import api
    anon = TestClient(api.app)
    r = anon.get("/me", headers={"accept": "application/json"})
    assert r.status_code == 401


# ── the tier as the user experiences it ──────────────────────────────────────

def test_the_one_stream_allowance_is_enforced_and_explains_itself(client):
    """Hitting the limit is the moment upgrading means something concrete, so
    the refusal names the next tier instead of just saying no."""
    c = client.login("free_user")
    first = c.post("/streams", json={"channel": "chan1", "platform": "twitch",
                                     "preset": "default"})
    assert first.status_code in (200, 201), first.text
    second = c.post("/streams", json={"channel": "chan2", "platform": "twitch",
                                      "preset": "default"})
    assert second.status_code == 429, second.text
    assert "Starter" in second.text and "$10" in second.text


def test_the_pending_cap_drops_the_new_clip_and_says_so(client, monkeypatch):
    """The free tier's real constraint. A full queue must REFUSE the new clip
    rather than evict an older one — the user chose to keep those — and the
    miss has to reach them, or the cap just looks like the detector going
    quiet.
    """
    import asyncio, time
    api, stream_stats = client.api, client.stats

    events = []

    async def _bc(msg, user_id=None):
        events.append(msg.get("event"))

    monkeypatch.setattr(api, "broadcast", _bc)

    async def fill():
        for i in range(20):
            await api.notify_clip_ready({
                "id": f"c{i}", "user_id": "free_user", "channel": "novafps",
                "status": "pending",
                # Spaced past the 45s dedup window, or the second clip onward is
                # discarded as a duplicate and the cap is never reached — which
                # is exactly how a first draft of this test "passed".
                "created_at": time.time() + i * 120,
                "twitch_url": f"https://clips.twitch.tv/{i}", "trigger_score": 80})

    asyncio.run(fill())
    held = [c for c in api._clips.values() if c.get("user_id") == "free_user"]
    assert len(held) == 15, f"the free queue did not stop at 15: {len(held)}"
    assert events.count("clip_ready") == 15
    assert events.count("clip_missed") == 5, "the user was never told about the misses"
    rows = [r for r in stream_stats.all_rows() if r["user_id"] == "free_user"]
    assert sum(r["caught"] for r in rows) == 15
    assert sum(r["missed"] for r in rows) == 5


def test_the_account_screen_never_tells_a_free_user_they_have_nothing():
    """Free is a PLAN, not the absence of one.

    The status row said "No subscription" in the dim/inactive colour for
    everyone on the free tier, so a new signup opened Account and the most
    prominent line told them they had nothing — which reads as the product
    being broken rather than as the tier working. The wording predates the free
    tier, when no subscription really did mean no access.
    """
    import re
    from src.dashboard import aurora_html
    src = aurora_html.DASHBOARD_HTML
    m = re.search(r"const subLabel\s*=(.*?);\n", src, re.S)
    assert m, "the subscription label is gone"
    labels = m.group(1)
    assert "No subscription" not in labels, \
        "a free user is being told they have no subscription again"
    assert "Free plan" in labels, "the free tier is not named as a plan"
    # A lapsed subscriber still has the product; the label has to say where
    # they landed rather than leaving them to guess.
    assert labels.count("on Free") >= 2, "lapsed states do not say they are on free"

    # And it must not be painted in the warning colour. Only a real billing
    # problem earns that.
    m2 = re.search(r"const subColor\s*=(.*?);\n", src, re.S)
    assert m2 and "var(--fg-3)" not in m2.group(1), \
        "the free plan is being shown in the inactive colour again"


def test_the_membership_line_is_written_for_one_stream():
    """The free tier is the only plan with a singular allowance, and it is the
    plan the most people see."""
    from src.dashboard import aurora_html
    assert "max_streams===1?'':'s'" in aurora_html.DASHBOARD_HTML.replace(" ", ""), \
        "the stream count is not pluralised, so free reads 'Up to 1 streams'"
