"""scripts/unit_costs.py: what each account costs, free against paying.

Owner, 2026-11-18: "figure out my per person cost currently and do it free vs
paying users so i can see my margins". Held here: an account is in exactly
one group, by the admin page's own rule, so the revenue matches the admin
page; the bill is shared by channels live now and adds back up to the bill;
the margins are the subtraction they say they are; and nothing private is
printed.
"""

import importlib.util
import json
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "unit_costs.py"
spec = importlib.util.spec_from_file_location("unit_costs", _PATH)
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)

NOW = 1_800_000_000.0


def _u(uid, **kw):
    base = {"id": uid, "username": "name-" + uid, "email": uid + "@example.com",
            "subscription_status": "none"}
    base.update(kw)
    return base


USERS = [
    _u("boss", is_admin=True, subscription_status="active"),
    _u("pro1", subscription_status="active", plan="pro", stripe_customer_id="cus_SECRET1"),
    _u("pro2", subscription_status="active", plan="pro", stripe_customer_id="cus_SECRET2"),
    _u("start1", subscription_status="active", plan="starter", stripe_customer_id="cus_SECRET3"),
    _u("old15", subscription_status="active", plan="pro", stripe_customer_id="cus_SECRET4",
       monthly_price=15.0, stripe_price_id="price_the_old_fifteen"),
    _u("gift", subscription_status="active", plan="pro", plan_source="granted",
       stripe_customer_id="cus_SECRET5"),
    _u("nocard", subscription_status="active", plan="pro"),
    _u("trial", subscription_status="trialing", plan="pro", trial_ends_at=NOW + 86400),
    _u("failing", subscription_status="past_due", plan="pro", stripe_customer_id="cus_SECRET6"),
    _u("free1"), _u("free2"), _u("free3", subscription_status="canceled", stripe_customer_id="cus_SECRET7"),
    _u("idle"),
]


@pytest.fixture(autouse=True)
def _legacy_price(monkeypatch):
    """The old $15 price is known by its Stripe price id, as on the admin page."""
    from config.settings import settings
    monkeypatch.setattr(settings, "stripe_price_id", "price_the_old_fifteen")


@pytest.fixture
def store(tmp_path):
    live = (["pro1:a", "pro1:b", "pro1:c", "pro1:d", "pro2:e", "start1:f", "old15:g",
             "free1:h", "free2:i", "trial:j", "boss:k", "boss:l"])
    (tmp_path / "headroom.json").write_text(json.dumps({"live": len(live), "at": NOW - 12, "keys": live}))
    streams = [{"channel": k.split(":")[1], "user_id": k.split(":")[0], "status": "starting"} for k in live]
    streams += [{"channel": "z1", "user_id": "free3", "status": "paused"},
                {"channel": "z2", "user_id": "pro2", "status": "offline"}, "junk", {"channel": "x"}]
    (tmp_path / "streams.json").write_text(json.dumps(streams))
    rows = ([{"ts": NOW - 3600, "event": "caught", "user_id": "pro1", "channel": "a"}] * 30
            + [{"ts": NOW - 3600, "event": "caught", "user_id": "free1", "channel": "h"}] * 10
            + [{"ts": NOW - 40 * 86400, "event": "caught", "user_id": "free1", "channel": "h"}] * 500
            + [{"ts": NOW - 60, "event": "approved", "user_id": "free1", "channel": "h"}] * 7)
    (tmp_path / "stream_stats.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    (tmp_path / "clips.json").write_text(json.dumps([{"id": "c1", "user_id": "pro1"},
                                                     {"id": "c2", "user_id": "free1"}]))
    (tmp_path / "clipfiles").mkdir()
    (tmp_path / "clipfiles" / "c1.mp4").write_bytes(b"x" * 3000)
    (tmp_path / "clipfiles" / "c2.mp4").write_bytes(b"x" * 1000)
    (tmp_path / "clipfiles" / "orphan.mp4").write_bytes(b"x" * 9000)
    return tmp_path


def _data(store, server=120.0):
    live, age = U.live_now(store, NOW)
    on, paused = U.listed(store)
    data = U.build(USERS, live, on, paused, U.caught(store, 30, NOW), U.video_held(store),
                   pct=3.6, fixed=0.30)
    U.share_out(data, server)
    return data, age


# ── who is in which group ────────────────────────────────────────────────────

def test_every_account_is_in_exactly_one_group_by_the_admin_pages_rule():
    got = {u["id"]: U.segment_of(u) for u in USERS}
    assert got == {"boss": "staff", "pro1": "paying", "pro2": "paying", "start1": "paying",
                   "old15": "paying", "gift": "comped", "nocard": "comped", "trial": "trialing",
                   "failing": "grace", "free1": "free", "free2": "free", "free3": "free", "idle": "free"}
    assert set(got.values()) <= set(U.SEGMENTS)
    # The rule is the admin page's, line for line, so the two revenues agree.
    api = pathlib.Path("src/dashboard/api.py").read_text()
    assert 'if not u.get("stripe_customer_id") or u.get("plan_source") == "granted":' in api
    src = _PATH.read_text()
    assert 'if not user.get("stripe_customer_id") or user.get("plan_source") == "granted":' in src


def test_a_paying_account_is_priced_at_what_stripe_charges_not_at_its_tier(store):
    data, _ = _data(store)
    price = {r["name"]: r["price"] for r in data["rows"]}
    assert price["name-pro1"] == 25.0 and price["name-start1"] == 10.0
    assert price["name-old15"] == 15.0, "a $15-era subscriber was counted at the Pro price"
    assert price["name-gift"] == 0.0 and price["name-trial"] == 0.0 and price["name-boss"] == 0.0
    assert data["groups"]["paying"]["revenue"] == 75.0


def test_the_card_fee_is_a_cut_of_each_charge_and_nothing_on_nothing():
    assert U.stripe_fee(25.0, 3.6, 0.30) == pytest.approx(1.20)
    assert U.stripe_fee(0.0, 3.6, 0.30) == 0.0


# ── what they use ────────────────────────────────────────────────────────────

def test_who_is_live_comes_from_the_sites_own_note_not_from_saved_statuses(store):
    live, age = U.live_now(store, NOW)
    assert live == {"pro1": 4, "pro2": 1, "start1": 1, "old15": 1, "free1": 1, "free2": 1,
                    "trial": 1, "boss": 2}
    assert age == 12.0
    # streams.json says "starting" for every one of them: only listing is read from it.
    on, paused = U.listed(store)
    assert on["pro2"] == 2 and paused == {"free3": 1} and "x" not in on
    assert U.live_now(store / "nowhere", NOW) == ({}, None)


def test_only_clips_caught_in_the_window_count_and_only_video_with_an_owner(store):
    assert U.caught(store, 30, NOW) == {"pro1": 30, "free1": 10}
    assert U.video_held(store) == {"pro1": 3000, "free1": 1000}


# ── the bill, shared out ─────────────────────────────────────────────────────

def test_the_bill_is_shared_by_channels_live_and_adds_back_up_to_the_bill(store):
    data, _ = _data(store, server=120.0)                       # 12 live: $10 a channel
    g = data["groups"]
    assert data["basis"] == "live" and data["basis_total"] == 12
    assert g["paying"]["cost"] == pytest.approx(70.0)          # 7 channels
    assert g["free"]["cost"] == pytest.approx(20.0)            # 2
    assert g["trialing"]["cost"] == pytest.approx(10.0) and g["staff"]["cost"] == pytest.approx(20.0)
    assert g["comped"]["cost"] == 0.0 and g["grace"]["cost"] == 0.0
    assert sum(x["cost"] for x in g.values()) == pytest.approx(120.0)
    assert sum(r["cost"] for r in data["rows"]) == pytest.approx(120.0)
    assert g["free"]["accounts"] == 4 and g["free"]["live_accounts"] == 2 and g["free"]["using"] == 2


def test_with_nothing_live_it_falls_back_to_channels_listed_and_says_so(store):
    (store / "headroom.json").write_text(json.dumps({"live": 0, "at": NOW - 5, "keys": []}))
    data, age = _data(store, server=130.0)
    assert data["basis"] == "listed" and data["basis_total"] == 13
    text = "\n".join(U.report(data, server=130.0, other=0.0, pct=3.6, fixed=0.3,
                              server_given=True, age=age, days=30))
    assert "channels LISTED, because nothing is live right now" in text


def test_with_nothing_at_all_nobody_is_charged_and_nothing_divides_by_zero(tmp_path):
    data = U.build(USERS, {}, {}, {}, {}, {}, pct=3.6, fixed=0.3)
    U.share_out(data, 100.0)
    assert all(r["cost"] == 0.0 for r in data["rows"])
    text = "\n".join(U.report(data, server=100.0, other=0.0, pct=3.6, fixed=0.3,
                              server_given=False, age=None, days=30))
    assert "no channel is live or listed" in text and "n/a" in text


# ── what the owner reads ─────────────────────────────────────────────────────

def _report(store, capsys, *argv):
    assert U.main(list(argv), store=store, users=USERS, now=NOW) == 0
    return capsys.readouterr().out


def test_the_margins_are_the_subtraction_they_say_they_are(store, capsys):
    out = _report(store, capsys, "--server=120")
    line = {ln.strip().split("  ")[0]: ln for ln in out.splitlines() if ln.startswith("  ")}
    # Free: 4 accounts, $20 of the bill. Paying: 4 accounts, $70 + fees, $75 in.
    fees = 3 * 0 + (25 * .036 + .3) * 2 + (10 * .036 + .3) + (15 * .036 + .3)
    assert "$20.00" in line["their share of the bill, a month"] and "$70.00" in line["their share of the bill, a month"]
    assert f"${fees:,.2f}" in line["card processing, a month"]
    free_each, paying_each = (float(x.strip("$")) for x in line["COST PER ACCOUNT"].split()[-2:])
    assert free_each == 5.0 and paying_each == pytest.approx((70 + fees) / 4, abs=0.006)
    assert "$10.00" in line["cost per account that is live now"]          # a free account, live
    assert "$18.75" in line["PAID PER ACCOUNT"]
    margin = 75 - fees - 70
    assert f"${margin:,.2f}" in line["MARGIN"] and "-$20.00" in line["MARGIN"]
    assert "Each paying account also carries $5.00 a month" in out
    # The whole site: what comes in, less the card cut, less the whole bill.
    assert f"LEFT OVER                         : -${abs(75 - fees - 120):,.2f}" in out
    assert "One channel live costs about $10.00 a month" in out
    # The worst an account can do to its plan, at today's $10 a channel.
    worst = out[out.index("IF AN ACCOUNT USED EVERYTHING ITS PLAN ALLOWS"):out.index("THE ACCOUNTS THAT COST")]
    row = {ln.split()[0]: ln.split() for ln in worst.splitlines() if ln.startswith("  ") and ln.split()[0] != "plan"}
    assert row["Free"] == ["Free", "1", "$0.00", "$10.00", "-$10.00"]
    assert row["Pro"] == ["Pro", "10", "$25.00", "$101.20", "-$76.20"]
    assert row["Studio"][:3] == ["Studio", "30", "$250.00"]


def test_the_plans_are_set_side_by_side(store, capsys):
    out = _report(store, capsys, "--server=120")
    plans = out[out.index("THE PAYING ACCOUNTS, BY PLAN"):out.index("THE WHOLE SITE")]
    pro = next(ln for ln in plans.splitlines() if ln.strip().startswith("Pro"))
    # Two Pro accounts, five channels live between them: $50 of server, $2.40 of fees, $50 in.
    assert pro.split()[:3] == ["Pro", "2", "2.5"] and "$25.00" in pro and "$26.20" in pro and "-$1.20" in pro
    assert "Starter" in plans and "Legacy" in plans and "Studio" not in plans


def test_an_assumed_bill_is_called_assumed_and_a_given_one_is_not(store, capsys):
    out = _report(store, capsys)
    assert "$112.00 a month   <- ASSUMED" in out and "tell me the figure" in out
    out = _report(store, capsys, "--server=96", "--other=24")
    assert "ASSUMED" not in out and "$96.00 a month" in out and "other monthly costs    : $24.00" in out
    assert "server and other costs            : -$120.00" in out


def test_a_stale_note_of_who_is_live_is_said_loudly(store, capsys):
    (store / "headroom.json").write_text(json.dumps({"live": 1, "at": NOW - 3600, "keys": ["pro1:a"]}))
    assert "THE LIVE NOTE IS 60 MINUTES OLD" in _report(store, capsys)


def test_the_heaviest_accounts_are_named_and_nothing_else_about_them_is(store, capsys):
    out = _report(store, capsys, "--server=120")
    top = out[out.index("THE ACCOUNTS THAT COST THE MOST"):]
    first = top.splitlines()[3]
    assert first.split()[:3] == ["name-pro1", "Pro", "4"] and "$41.20" in first and "-$16.20" in first
    assert "name-boss" in top and "Staff" in top
    for private in ("@example.com", "cus_SECRET", "pro1:a"):
        assert private not in out
    # An account's id never appears except inside its own name ("name-<id>" here).
    for uid in ("pro1", "pro2", "start1", "old15", "nocard", "free1", "free2", "free3"):
        assert uid not in out.replace("name-" + uid, ""), uid


@pytest.mark.parametrize("argv, says", [
    (["--server=lots"], "that is not a number"),
    (["--server=-5"], "cannot be negative"),
    (["--bill=5"], "not something this takes"),
])
def test_a_slip_in_the_arguments_works_nothing_out(store, capsys, argv, says):
    assert U.main(argv, store=store, users=USERS, now=NOW) == 1
    out = capsys.readouterr().out
    assert says in out and "NOTHING WAS WORKED OUT" in out


def test_it_reads_and_never_writes():
    src = _PATH.read_text()
    for writer in (".write_text(", ".write_bytes(", "open(store", '"w"', "unlink(", "os.replace("):
        assert writer not in src.replace('open(store / "stream_stats.jsonl", encoding="utf-8", errors="replace")', ""), writer
    # An address is never read, and the Stripe id only asked "is there one".
    assert '"email"' not in src and "['email']" not in src
    assert src.count("stripe_customer_id") == 1 and 'user.get("stripe_customer_id")' in src
