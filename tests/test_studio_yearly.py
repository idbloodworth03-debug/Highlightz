"""Studio is a yearly subscription at the same $250 (owner, 2026-11-24:
"change the 250$ one to be a yearly subscription now too instead so lets make
the changes for that as well but keep the same price").

What these defend:
  1. the period is ONE setting (STUDIO_INTERVAL), written by the setup script
     with the yearly price; until it is "year", Studio is the monthly plan it
     always was, so the words and the charge cannot disagree;
  2. every page that prints Studio's price says "a year" once it is yearly,
     and Pro still says "a month";
  3. whoever is already on Studio monthly stays Studio, and is not told they
     pay yearly;
  4. no Studio checkout or switch is opened on a price that bills a different
     period from the one the pages say;
  5. the holiday sale says Studio's discount is on its first year;
  6. scripts/setup_studio_yearly.py makes the yearly price, writes .env once,
     points the portal at it, and refuses with NOTHING WAS CHANGED.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

from src.billing import plans, stripe_billing
from src.dashboard import api
from tests.test_studio_plan import world  # noqa: F401  (the fixture)


@pytest.fixture
def yearly(monkeypatch):
    monkeypatch.setattr(api.settings, "studio_interval", "year")
    stripe_billing._period_seen.clear()
    yield
    stripe_billing._period_seen.clear()


# ── 1. one setting ───────────────────────────────────────────────────────────

def test_studio_is_monthly_until_the_setting_says_year(monkeypatch):
    assert api.settings.studio_interval == "month"
    assert plans.period("studio") == "month" and plans.price_text("studio") == "$250/month"
    monkeypatch.setattr(api.settings, "studio_interval", "year")
    assert plans.period("studio") == "year"
    assert [plans.price_text("studio", s) for s in ("slash", "words", "short")] == \
        ["$250/year", "$250 a year", "$250/yr"]
    for other in ("pro", "free", "starter"):
        assert plans.period(other) == "month"
    assert plans.price_text("pro") == "$25/month"
    monkeypatch.setattr(api.settings, "studio_interval", "fortnight")
    assert plans.period("studio") == "month", "a typo must not change what the pages say"


def test_the_same_price(yearly):
    assert plans.PLAN_LIMITS["studio"]["price"] == 250


def test_mrr_counts_a_year_as_a_twelfth(yearly):
    assert plans.billed_monthly({"subscription_status": "active", "plan": "studio"}) == 20.83
    assert plans.billed_monthly({"subscription_status": "active", "plan": "pro"}) == 25.0
    # Stripe's own figure wins whenever it is recorded.
    assert plans.billed_monthly({"plan": "studio", "monthly_price": 250.0}) == 250.0
    year = {"items": {"data": [{"quantity": 1, "price": {
        "unit_amount": 25000, "recurring": {"interval": "year", "interval_count": 1}}}]}}
    assert stripe_billing.monthly_amount(year) == 20.83


# ── 2. every page ────────────────────────────────────────────────────────────

def _text(html: str) -> str:
    from html import unescape
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def test_every_page_says_a_year_for_studio_and_a_month_for_pro(yearly):
    pricing = _text(api._pricing())
    assert "Studio $250 per year" in pricing and "Pro $25 per month" in pricing
    assert "Pro is $25 a month and Studio is $250 a year" in _text(api._faq())
    assert "Studio is $250/year for everything in Pro" in api._tos_plans()
    assert "$250 /yr" in _text(api._paywall_plans()) and "$25 /mo" in _text(api._paywall_plans())
    # The tutorial's table is built at import, when the setting is already read.
    from src.dashboard import tutorial_content
    assert ("Price", "$0, no card", "$25/mo", "$250/yr") in tutorial_content._plan_rows()
    # The compare page's plans and FAQ are built at import, when the setting
    # is already read: checked at the builder and in the source.
    from src.dashboard import compare_content
    assert [p.price for p in compare_content._our_plans()][1:] == ["$25/mo", "$250/yr"]
    assert '"watches ten channels that do not have to be yours. Studio, at "\n     + _price_text("studio", "words")' \
        in pathlib.Path(compare_content.__file__).read_text()
    for page in (pricing, api._tos_plans(), api._paywall_plans()):
        assert "$250/month" not in page and "$250 a month" not in page


def test_the_landing_json_ld_follows_too():
    """The offer in the landing page's JSON-LD is rewritten at import from the
    one literal below; if the literal moves, the rewrite silently misses."""
    src = pathlib.Path(api.__file__).read_text()
    assert src.count("then Pro $25/month or Studio $250/month.") == 2   # the page and the rewrite
    assert "or Studio " + plans.price_text("studio") + "." in api.LANDING_HTML


def test_the_ai_summaries_say_it(yearly):
    import asyncio

    def text(r):
        return r if isinstance(r, str) else r.body.decode()
    body = text(asyncio.run(api.llms_txt()))
    assert "Studio — $250/year." in body
    full = text(asyncio.run(api.llms_full_txt()))
    assert "| $25/month | $250/year |" in full


def test_the_dashboard_reads_the_period_from_the_server(yearly):
    from src.dashboard.aurora_html import DASHBOARD_HTML as d
    assert "$250/month" not in d and "Studio is $250 for" not in d
    assert "(me.prices||{}).studio" in d and "nxt.price_text" in d
    assert "lost.next_price_text" in d and "next_price_text:(data.next_plan||{}).price_text" in d
    tier = api._next_tier({"subscription_status": "active", "plan": "pro"})
    assert tier["price_text"] == "$250 a year" and tier["per"] == "year"
    assert {p: api._plan_price_text(p) for p in api._sellable()} == \
        {"pro": "$25/month", "studio": "$250/year"}


def test_me_carries_the_prices(world, yearly):
    me = world.as_user("pro1").get("/me").json()
    assert me["prices"] == {"pro": "$25/month", "studio": "$250/year"}
    assert me["next_plan"]["price_text"] == "$250 a year"


def test_the_upgrade_refusals_name_the_period(yearly):
    src = pathlib.Path(api.__file__).read_text()
    assert "({price_text(nxt, 'short')}) gives you" in src
    assert "It is {price_text(nxt, 'words')} " in src
    assert '"next_price_text": price_text(nxt, "words") if nxt else ""' in src


# ── 3. Studio monthly stays Studio ───────────────────────────────────────────

def test_the_monthly_price_is_still_studio(monkeypatch):
    monkeypatch.setattr(stripe_billing.settings, "stripe_price_id_studio", "price_year")
    monkeypatch.setattr(stripe_billing.settings, "stripe_price_id_studio_monthly", "price_month")
    assert stripe_billing.plan_for_price("price_year") == "studio"
    assert stripe_billing.plan_for_price("price_month") == "studio"
    assert stripe_billing.price_id_for("studio") == "price_year", "the monthly one is not sold"
    monkeypatch.setattr(stripe_billing.settings, "stripe_price_id_studio_monthly", "")
    assert stripe_billing.plan_for_price("") is None


def test_the_switch_page_says_what_changes(yearly, monkeypatch):
    up = _text(api._plan_change_html("pro", "studio", "t"))
    assert "Pro · $25/month" in up and "Studio · $250/year" in up
    assert "charges the card on your subscription today for a year of Studio" in up
    assert "renews every year from today, at $250/year" in up
    down = _text(api._plan_change_html("studio", "pro", "t"))
    assert "Studio · $250/year" in down and "unused part of your Studio year becomes a credit" in down
    # Somebody still on Studio monthly is shown what they actually pay.
    monkeypatch.setattr(api.settings, "stripe_price_id_studio_monthly", "price_month")
    legacy = _text(api._plan_change_html("studio", "pro", "t", {"stripe_price_id": "price_month"}))
    assert "Studio · $250/month" in legacy and "From your next renewal it is $25/month" in legacy


def test_monthly_studio_reads_as_before():
    up = _text(api._plan_change_html("pro", "studio", "t"))
    assert "Studio · $250/month" in up and "for the difference" in up


# ── 4. never sell a different period ─────────────────────────────────────────

class _Prices:
    def __init__(self, interval):
        self.interval, self.asked = interval, 0

    def retrieve(self, pid):
        self.asked += 1
        if self.interval is None:
            raise RuntimeError("Stripe is down")
        return {"id": pid, "active": True, "recurring": {"interval": self.interval, "interval_count": 1}}


def _stripe(monkeypatch, interval):
    prices = _Prices(interval)

    class V1:
        pass
    v1 = V1()
    v1.prices = prices

    class Client:
        pass
    c = Client()
    c.v1 = v1
    monkeypatch.setattr(stripe_billing, "_client", lambda: c)
    monkeypatch.setattr(stripe_billing.settings, "stripe_secret_key", "sk_test")
    return prices


def test_a_monthly_plan_is_never_asked(monkeypatch):
    prices = _stripe(monkeypatch, "month")
    assert stripe_billing.price_period_problem("pro", "price_pro") == ""
    assert stripe_billing.price_period_problem("studio", "price_studio") == ""
    assert prices.asked == 0


def test_a_yearly_studio_on_a_monthly_price_is_refused(yearly, monkeypatch):
    prices = _stripe(monkeypatch, "month")
    why = stripe_billing.price_period_problem("studio", "price_studio")
    assert "bills every month, the site says every year" in why
    stripe_billing.price_period_problem("studio", "price_studio")
    assert prices.asked == 1, "asked again inside the hour"


def test_a_yearly_price_sells_and_a_stripe_outage_does_not_block(yearly, monkeypatch):
    _stripe(monkeypatch, "year")
    assert stripe_billing.price_period_problem("studio", "price_y") == ""
    _stripe(monkeypatch, None)
    assert stripe_billing.price_period_problem("studio", "price_z") == ""


def test_checkout_and_the_switch_refuse_a_wrong_period(world, yearly, monkeypatch):
    _stripe(monkeypatch, "month")
    r = world.as_user("free1").get("/billing/checkout?plan=studio", follow_redirects=False)
    assert r.headers["location"] == "/billing/paywall?unavailable=studio" and world.checkouts == []
    c = world.as_user("pro1")
    page = c.get("/billing/change?plan=studio").text
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    r = c.post("/billing/change", data={"plan": "studio", "token": token}, follow_redirects=False)
    assert r.headers["location"] == "/billing/paywall?unavailable=studio" and world.changes == []
    # Pro is untouched by it.
    world.as_user("free1").get("/billing/checkout?plan=pro", follow_redirects=False)
    assert world.checkouts == ["price_pro"]


def test_checkout_sells_studio_on_a_yearly_price(world, yearly, monkeypatch):
    _stripe(monkeypatch, "year")
    r = world.as_user("free1").get("/billing/checkout?plan=studio", follow_redirects=False)
    assert r.headers["location"].startswith("https://checkout.stripe.com/")
    assert world.checkouts == ["price_studio"]


# ── 5. the holiday sale ──────────────────────────────────────────────────────

def test_the_sale_says_studios_discount_is_its_first_year(yearly, monkeypatch):
    from src.dashboard import holiday_sale as hs
    monkeypatch.setattr(api.settings, "holiday_sale_code", "CLIPPER")
    monkeypatch.setattr(api.settings, "holiday_sale_percent", 50)
    monkeypatch.setattr(api.settings, "holiday_sale_last_day", "2099-12-31")
    # Pro only by default (2026-11-24); with Studio in it, its year is named.
    monkeypatch.setattr(api.settings, "holiday_sale_plans", "pro,studio")
    html = api.render_landing()
    vis = _text(re.sub(r"<script.*?</script>|<style>.*?</style>", "", html, flags=re.S))
    assert "Pro is $12.50 for your first month instead of $25, Studio $125 for your first year " \
           "instead of $250." in vis
    assert "$125 your first year with code CLIPPER" in vis
    assert "$12.50 your first month with code CLIPPER" in vis
    assert "your first month of Pro or first year of Studio" in vis
    assert "50% off your first payment with code" in vis
    assert "50% off your first month with code" not in vis
    s = hs.current()
    assert [p["per"] for p in s["plans"]] == ["month", "year"]


# ── 6. the setup script ──────────────────────────────────────────────────────

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "setup_studio_yearly.py"


def _script():
    spec = importlib.util.spec_from_file_location("setup_studio_yearly", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _price(pid, interval="month", cents=25000, product="prod_S", active=True):
    return {"id": pid, "active": active, "type": "recurring", "unit_amount": cents, "currency": "usd",
            "recurring": {"interval": interval, "interval_count": 1}, "product": product}


class FakeStripe:
    def __init__(self, prices, configs=None, subs=2):
        self.by_id = {p["id"]: p for p in prices}
        self.by_id["price_pro"] = _price("price_pro", cents=2500, product="prod_P")
        self.made, self.updates, self.subs = [], [], subs
        outer = self

        class Prices:
            def retrieve(self, pid):
                if pid not in outer.by_id:
                    raise RuntimeError("No such price")
                return outer.by_id[pid]

            def list(self, params):
                return {"data": [p for p in outer.by_id.values()
                                 if p["product"] == params["product"] and p["active"]]}

            def create(self, params):
                p = _price("price_new_year", interval=params["recurring"]["interval"],
                           cents=params["unit_amount"], product=params["product"])
                outer.made.append(params)
                outer.by_id[p["id"]] = p
                return p

        class Subs:
            def list(self, params):
                return {"data": [{"id": f"sub_{i}"} for i in range(outer.subs)]}

        class Configs:
            def list(self, params):
                return {"data": configs or []}

            def update(self, cid, params):
                outer.updates.append((cid, params))

        class Portal:
            configurations = Configs()

        self.prices, self.subscriptions, self.billing_portal = Prices(), Subs(), Portal()


ENV = "STRIPE_SECRET_KEY=sk_live_x\nSTRIPE_PRICE_ID_PRO=price_pro\nSTRIPE_PRICE_ID_STUDIO=price_month\n"
SWITCHING = [{"id": "bpc_1", "features": {"subscription_update": {
    "enabled": True, "default_allowed_updates": ["price"], "proration_behavior": "create_prorations",
    "products": [{"product": "prod_P", "prices": ["price_pro"]},
                 {"product": "prod_S", "prices": ["price_month"]}]}}}]


def _env(tmp_path, text=ENV):
    f = tmp_path / ".env"
    f.write_text(text)
    return f


def test_it_makes_the_yearly_price_and_writes_env_once(tmp_path, capsys):
    S = _script()
    env = _env(tmp_path)
    fake = FakeStripe([_price("price_month")], SWITCHING)
    assert S.main([], client=fake, env_path=env) == 0
    assert fake.made == [{"product": "prod_S", "unit_amount": 25000, "currency": "usd",
                          "recurring": {"interval": "year", "interval_count": 1},
                          "nickname": "Studio yearly"}]
    text = env.read_text()
    assert "STRIPE_PRICE_ID_STUDIO=price_new_year\n" in text
    assert "STUDIO_INTERVAL=year\n" in text and "STRIPE_PRICE_ID_STUDIO_MONTHLY=price_month\n" in text
    assert "STRIPE_SECRET_KEY=sk_live_x\nSTRIPE_PRICE_ID_PRO=price_pro\n" in text
    assert len(list(tmp_path.glob(".env.bak-studio-*"))) == 1
    assert fake.updates[0][1]["features"]["subscription_update"]["products"] == [
        {"product": "prod_P", "prices": ["price_pro"]}, {"product": "prod_S", "prices": ["price_new_year"]}]
    out = capsys.readouterr().out
    assert "2 active subscriptions on Studio monthly keep it" in out and "Done." in out


def test_a_second_run_changes_nothing(tmp_path, capsys):
    S = _script()
    env = _env(tmp_path)
    fake = FakeStripe([_price("price_month")], SWITCHING)
    S.main([], client=fake, env_path=env)
    first = env.read_text()
    SWITCHING_NOW = [{"id": "bpc_1", "features": {"subscription_update": dict(
        fake.updates[0][1]["features"]["subscription_update"])}}]
    fake2 = FakeStripe([_price("price_month"), _price("price_new_year", "year")], SWITCHING_NOW)
    assert S.main([], client=fake2, env_path=env) == 0
    assert env.read_text() == first and fake2.made == [] and fake2.updates == []
    assert len(list(tmp_path.glob(".env.bak-studio-*"))) == 1
    assert ".env      already right" in capsys.readouterr().out


def test_an_existing_yearly_price_is_used_not_made_again(tmp_path):
    S = _script()
    env = _env(tmp_path)
    fake = FakeStripe([_price("price_month"), _price("price_have", "year")])
    S.main([], client=fake, env_path=env)
    assert fake.made == [] and "STRIPE_PRICE_ID_STUDIO=price_have\n" in env.read_text()


def test_check_changes_nothing(tmp_path, capsys):
    S = _script()
    env = _env(tmp_path)
    fake = FakeStripe([_price("price_month")], SWITCHING)
    assert S.main(["--check"], client=fake, env_path=env) == 0
    assert env.read_text() == ENV and fake.made == [] and fake.updates == []
    assert not list(tmp_path.glob(".env.bak-*"))
    assert "would make a $250-a-year USD price" in capsys.readouterr().out


@pytest.mark.parametrize("prices,word", [
    ([_price("price_month", cents=24900)], "249"),
    ([_price("price_month", active=False)], "archived"),
    ([], "Stripe has no price price_month"),
    ([_price("price_month"), _price("y1", "year"), _price("y2", "year")], "More than one"),
])
def test_it_refuses_with_nothing_changed(tmp_path, prices, word):
    S = _script()
    env = _env(tmp_path)
    fake = FakeStripe(prices, SWITCHING)
    with pytest.raises(SystemExit) as e:
        S.main([], client=fake, env_path=env)
    assert word in str(e.value) and "NOTHING WAS CHANGED." in str(e.value)
    assert env.read_text() == ENV and fake.made == [] and fake.updates == []


def test_it_refuses_without_a_studio_price(tmp_path):
    S = _script()
    env = _env(tmp_path, "STRIPE_PRICE_ID_PRO=price_pro\n")
    with pytest.raises(SystemExit) as e:
        S.main([], client=FakeStripe([]), env_path=env)
    assert "NOTHING WAS CHANGED." in str(e.value)
