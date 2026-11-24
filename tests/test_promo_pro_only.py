"""CLIPPER works on Pro only (owner, 2026-11-24: "make CLIPPER work on Pro
only").

What these defend:
  1. the landing page names Pro, says Studio is not part of the sale, and
     puts no sale price on Studio (HOLIDAY_SALE_PLANS, default "pro");
  2. scripts/promo_check.py reports a code that also works on Studio;
  3. scripts/promo_restrict.py rebuilds the code as Pro only, carrying its
     limits over, and refuses (or puts the old code back) with NOTHING WAS
     CHANGED;
  4. a pre-applied code Stripe will not put on a plan opens the ordinary
     Checkout with the code box, never an error.
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import re
from datetime import datetime, timezone
from html import unescape

import pytest

from src.dashboard import api, holiday_sale as hs

NOW = datetime(2026, 11, 24, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def sale(monkeypatch):
    def set_(plans="pro"):
        monkeypatch.setattr(api.settings, "holiday_sale_code", "CLIPPER")
        monkeypatch.setattr(api.settings, "holiday_sale_percent", 50)
        monkeypatch.setattr(api.settings, "holiday_sale_last_day", "2099-12-31")
        monkeypatch.setattr(api.settings, "holiday_sale_plans", plans)
        return api.render_landing()
    return set_


def _visible(html: str) -> str:
    html = re.sub(r"<script.*?</script>|<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


# ── 1. the page ──────────────────────────────────────────────────────────────

def test_pro_only_is_the_default():
    assert api.settings.holiday_sale_plans == "pro"


def test_the_page_names_pro_and_leaves_studio_out(sale):
    html = sale()
    vis = _visible(html)
    assert "50% off your first month of Pro with code CLIPPER" in vis      # ribbon, pill, end
    assert "Pro: 50% off first month" in vis                                 # the phone ribbon
    assert "your first month of Pro Use code CLIPPER" in vis                 # the tag
    assert "Pro is $12.50 for your first month instead of $25." in vis
    assert vis.count("The code is for Pro only; Studio is not part of the sale.") == 2  # card + FAQ
    assert "For a new Pro subscription" in vis and "Get Pro for $12.50" in vis
    assert "$125" not in vis
    blocks = re.findall(r'<div class="plan[^"]*">.*?<ul class="plan-facts">', html, re.S)
    assert ["hz-plan" in b for b in blocks] == [False, True, False]


def test_both_plans_when_both_are_named(sale):
    vis = _visible(sale("pro, Studio"))
    assert "not part of the sale" not in vis and "Studio $125" in vis
    assert "50% off your first month with code CLIPPER" in vis


def test_no_plans_means_no_sale(sale):
    html = sale("nonsense")
    assert 'class="hz-' not in html
    assert hs.sale_plans() == ()


# ── 2. the check ─────────────────────────────────────────────────────────────

def _load(name):
    p = pathlib.Path(__file__).resolve().parent.parent / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_check_reports_a_code_that_also_works_on_studio(sale, capsys, monkeypatch):
    sale()
    monkeypatch.setattr(api.settings, "stripe_price_id_pro", "price_pro")
    monkeypatch.setattr(api.settings, "stripe_price_id_studio", "price_studio")
    fake = Fake(applies_to=[])
    assert _load("promo_check").main([], client=fake, now=NOW) == 1
    out = capsys.readouterr().out
    assert "PROBLEM: its coupon works on every plan, Studio too; the page says Pro only" in out


# ── 3. the fix ───────────────────────────────────────────────────────────────

class Fake:
    """Just enough of Stripe: one active CLIPPER on a coupon for everything."""

    def __init__(self, applies_to=(), promo_create_fails=None, old_api=False, percent=50.0,
                 actives=1, restore_fails=False):
        self.calls = []
        self.promo = {"id": "promo_OLD123", "active": True, "code": "CLIPPER", "times_redeemed": 7,
                      "max_redemptions": 100, "expires_at": int(NOW) + 86400 * 60,
                      "customer": None,
                      "restrictions": {"first_time_transaction": True, "minimum_amount": None},
                      "promotion": {"type": "coupon", "coupon": "cpn_OLD456"}}
        self.store = {"cpn_OLD456": {"id": "cpn_OLD456", "name": "Holiday", "valid": True,
                                     "percent_off": percent, "duration": "once",
                                     "applies_to": {"products": list(applies_to)},
                                     "times_redeemed": 7}}
        self.promos = [self.promo] + [dict(self.promo, id=f"promo_X{i}") for i in range(actives - 1)]
        outer = self

        class Prices:
            def retrieve(self, pid):
                return {"id": pid, "product": "prod_" + pid}

        class PromotionCodes:
            def list(self, params):
                return {"data": [p for p in outer.promos if p["active"]]}

            def update(self, pid, params):
                outer.calls.append(("promo.update", pid, params))
                if restore_fails and params.get("active") is True:
                    raise RuntimeError("Stripe is down")
                for p in outer.promos:
                    if p["id"] == pid:
                        p.update(params)

            def create(self, params):
                outer.calls.append(("promo.create", params))
                if promo_create_fails:
                    raise RuntimeError(promo_create_fails)
                if old_api and "promotion" in params:
                    raise RuntimeError("Received unknown parameter: promotion")
                cid = params.get("coupon") or params["promotion"]["coupon"]
                new = {"id": "promo_NEW789", "active": True, "code": params["code"],
                       "times_redeemed": 0, "max_redemptions": params.get("max_redemptions"),
                       "expires_at": params.get("expires_at"),
                       "restrictions": params.get("restrictions", {}),
                       "promotion": {"type": "coupon", "coupon": cid}}
                outer.promos.append(new)
                return new

        class Coupons:
            def retrieve(self, cid, params=None):
                return outer.store[cid]

            def create(self, params):
                outer.calls.append(("coupon.create", params))
                c = dict(params, id="cpn_NEW000", valid=True)
                outer.store[c["id"]] = c
                return c

            def delete(self, cid):
                outer.calls.append(("coupon.delete", cid))
                outer.store.pop(cid, None)

        self.prices, self.promotion_codes, self.coupons = Prices(), PromotionCodes(), Coupons()


make = Fake


@pytest.fixture
def stripe_prices(monkeypatch, sale):
    sale()
    monkeypatch.setattr(api.settings, "stripe_price_id_pro", "price_pro")
    monkeypatch.setattr(api.settings, "stripe_price_id_studio", "price_studio")


def test_it_rebuilds_the_code_for_pro_only(stripe_prices, capsys):
    R = _load("promo_restrict")
    f = make()
    assert R.main([], client=f, now=NOW) == 0
    kinds = [c[0] for c in f.calls]
    assert kinds == ["coupon.create", "promo.update", "promo.create"]
    coupon = f.calls[0][1]
    assert coupon["applies_to"] == {"products": ["prod_price_pro"]}
    assert coupon["percent_off"] == 50.0 and coupon["duration"] == "once"
    assert f.calls[1] == ("promo.update", "promo_OLD123", {"active": False})
    promo = f.calls[2][1]
    assert promo["code"] == "CLIPPER" and promo["promotion"] == {"type": "coupon", "coupon": "cpn_NEW000"}
    assert promo["max_redemptions"] == 93, "what was left of the limit"
    assert promo["expires_at"] == int(NOW) + 86400 * 60
    assert promo["restrictions"] == {"first_time_transaction": True}
    out = capsys.readouterr().out
    assert "Done: CLIPPER takes 50% off the first payment of Pro only." in out
    assert "promo_OLD123" not in out and "...OLD123" in out, "a whole Stripe id was printed"


def test_a_second_run_changes_nothing(stripe_prices, capsys):
    R = _load("promo_restrict")
    f = make(applies_to=["prod_price_pro"])
    assert R.main([], client=f, now=NOW) == 0
    assert f.calls == [] and "Already right" in capsys.readouterr().out


def test_check_changes_nothing(stripe_prices, capsys):
    R = _load("promo_restrict")
    f = make()
    assert R.main(["--check"], client=f, now=NOW) == 0
    assert f.calls == [] and "nothing was changed" in capsys.readouterr().out


def test_older_stripe_takes_the_coupon_parameter(stripe_prices):
    R = _load("promo_restrict")
    f = make(old_api=True)
    assert R.main([], client=f, now=NOW) == 0
    assert f.calls[-1][1]["coupon"] == "cpn_NEW000"


def test_a_refused_new_code_puts_the_old_one_back(stripe_prices):
    R = _load("promo_restrict")
    f = make(promo_create_fails="code already exists")
    with pytest.raises(SystemExit) as e:
        R.main([], client=f, now=NOW)
    assert "NOTHING WAS CHANGED." in str(e.value) and "back on" in str(e.value)
    assert ("promo.update", "promo_OLD123", {"active": True}) in f.calls
    assert ("coupon.delete", "cpn_NEW000") in f.calls
    assert f.promo["active"] is True


def test_if_it_cannot_put_it_back_it_says_so_loudly(stripe_prices):
    R = _load("promo_restrict")
    f = make(promo_create_fails="boom", restore_fails=True)
    with pytest.raises(SystemExit) as e:
        R.main([], client=f, now=NOW)
    msg = str(e.value)
    assert "CLIPPER IS OFF" in msg and "promo_OLD123" in msg and "NOTHING WAS CHANGED" not in msg


@pytest.mark.parametrize("kw,word", [
    (dict(percent=25.0), "wrong in other ways too"),
    (dict(actives=2), "2 active promotion codes"),
])
def test_it_refuses_with_nothing_changed(stripe_prices, kw, word):
    R = _load("promo_restrict")
    f = make(**kw)
    with pytest.raises(SystemExit) as e:
        R.main([], client=f, now=NOW)
    assert word in str(e.value) and "NOTHING WAS CHANGED." in str(e.value)
    assert f.calls == []


def test_no_active_code_is_refused(stripe_prices):
    R = _load("promo_restrict")
    f = make()
    f.promo["active"] = False
    with pytest.raises(SystemExit) as e:
        R.main([], client=f, now=NOW)
    assert "no active promotion code called CLIPPER" in str(e.value)


# ── 4. Checkout never errors on a code that does not fit the plan ────────────

def test_a_refused_offer_code_opens_the_plain_checkout(monkeypatch):
    import stripe
    from src.billing import stripe_billing as sb
    sent = []

    class Sessions:
        def create(self, params):
            sent.append(dict(params))
            if "discounts" in params:
                raise stripe.InvalidRequestError("This coupon cannot be applied", "discounts")
            return type("S", (), {"url": "https://checkout.stripe.com/c/plain"})()

    class Client:
        checkout = type("K", (), {"sessions": Sessions()})()
    monkeypatch.setattr(sb, "_client", lambda: Client())
    monkeypatch.setattr(sb, "_active_promo_id", lambda c, code: "promo_NEW789")
    url = asyncio.run(sb.create_checkout_url("u1", "u1", "price_studio", promo_code="CLIPPER"))
    assert url == "https://checkout.stripe.com/c/plain"
    assert "discounts" in sent[0] and "allow_promotion_codes" not in sent[0]
    assert sent[1]["allow_promotion_codes"] is True and "discounts" not in sent[1]


def test_other_checkout_errors_still_raise(monkeypatch):
    import stripe
    from src.billing import stripe_billing as sb

    class Sessions:
        def create(self, params):
            raise stripe.InvalidRequestError("No such price", "line_items")

    class Client:
        checkout = type("K", (), {"sessions": Sessions()})()
    monkeypatch.setattr(sb, "_client", lambda: Client())
    with pytest.raises(stripe.InvalidRequestError):
        asyncio.run(sb.create_checkout_url("u1", "u1", "price_x"))
