"""scripts/setup_hours_price.py and the hours checkout using the owner's price.

Owner, 2026-11-20, adding "1 Hour of Highlightz" ($1.00, one-off) in Stripe.
Held here: the script finds the price itself, writes .env only when Stripe
confirms a live $1.00 one-off USD price, says NOTHING WAS CHANGED whenever it
refuses, and with several candidates gives one finished command each; the
checkout uses the price only when Stripe confirms it, and otherwise sends its
own $1 line, so a wrong price can never take money the server will not credit.
"""

import importlib.util
import pathlib

import pytest

from config.settings import settings
from src.billing import stripe_billing

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "setup_hours_price.py"
spec = importlib.util.spec_from_file_location("setup_hours_price", _PATH)
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


def price(pid, amount=100, kind="one_time", currency="usd", active=True, product="prod_hour", live=True):
    return {"id": pid, "unit_amount": amount, "type": kind, "currency": currency, "active": active,
            "product": product, "livemode": live}


class Fake:
    def __init__(self, prices, products=None):
        self._prices = prices
        self._products = products or {"prod_hour": {"id": "prod_hour", "name": "1 Hour of Highlightz",
                                                    "description": "1 hour extra", "active": True}}
        fake = self

        class _Prices:
            @staticmethod
            def list(params):
                rows = [p for p in fake._prices if p["active"] and p["type"] == params.get("type", p["type"])]
                return {"data": rows, "has_more": False}

            @staticmethod
            def retrieve(pid):
                return next(p for p in fake._prices if p["id"] == pid)

        class _Products:
            @staticmethod
            def retrieve(pid):
                return fake._products[pid]
        self.prices, self.products = _Prices, _Products


@pytest.fixture
def env(tmp_path):
    f = tmp_path / ".env"
    f.write_text("A=1\nSTRIPE_SECRET_KEY=sk_live_x\n")
    return f


def run(env, fake, *args, capsys=None):
    code = S.main(list(args), client=fake, env_path=env)
    return code, (capsys.readouterr().out if capsys else "")


def test_one_dollar_price_is_found_and_written(env, capsys):
    code, out = run(env, Fake([price("price_AAAAAAhour01"), price("price_BBBBBBpro", amount=2500, kind="recurring")]),
                    capsys=capsys)
    assert code == 0 and "STRIPE_PRICE_ID_HOURS=price_AAAAAAhour01\n" in env.read_text()
    assert "A=1\n" in env.read_text() and "STRIPE_SECRET_KEY=sk_live_x" in env.read_text()
    assert "1 Hour of Highlightz" in out and "price_AAAAAAhour01" not in out, "a full Stripe id was printed"
    assert list(env.parent.glob(".env.bak-hours-*")), "no backup kept"


def test_check_changes_nothing(env, capsys):
    before = env.read_text()
    code, out = run(env, Fake([price("price_AAAAAAhour01")]), "--check", capsys=capsys)
    assert code == 0 and env.read_text() == before and S.NOTHING in out


@pytest.mark.parametrize("bad", [
    {"amount": 200}, {"currency": "eur"}, {"active": False},
])
def test_no_usable_price_refuses_and_says_so(env, capsys, bad):
    before = env.read_text()
    code, out = run(env, Fake([price("price_AAAAAAhour01", **bad)]), capsys=capsys)
    assert code == 1 and env.read_text() == before and S.NOTHING in out


def test_an_archived_product_is_not_used(env, capsys):
    fake = Fake([price("price_AAAAAAhour01")],
                {"prod_hour": {"id": "prod_hour", "name": "1 Hour", "active": False}})
    code, out = run(env, fake, capsys=capsys)
    assert code == 1 and S.NOTHING in out and "STRIPE_PRICE_ID_HOURS" not in env.read_text()


def test_the_one_named_hour_wins_among_several_dollar_prices(env, capsys):
    fake = Fake([price("price_AAAAAAhour01"), price("price_CCCCCCsticker", product="prod_other")],
                {"prod_hour": {"id": "prod_hour", "name": "1 Hour of Highlightz", "active": True},
                 "prod_other": {"id": "prod_other", "name": "Sticker", "active": True}})
    code, _ = run(env, fake, capsys=capsys)
    assert code == 0 and "STRIPE_PRICE_ID_HOURS=price_AAAAAAhour01" in env.read_text()


def test_two_candidates_give_a_finished_command_each_and_change_nothing(env, capsys):
    before = env.read_text()
    fake = Fake([price("price_AAAAAAhour01"), price("price_DDDDDDhour02", product="prod_hour2")],
                {"prod_hour": {"id": "prod_hour", "name": "1 Hour of Highlightz", "active": True},
                 "prod_hour2": {"id": "prod_hour2", "name": "Extra hour", "active": True}})
    code, out = run(env, fake, capsys=capsys)
    assert code == 1 and env.read_text() == before and S.NOTHING in out
    cmds = [ln.strip() for ln in out.splitlines() if "setup_hours_price.py --price" in ln]
    assert len(cmds) == 2 and all(c.startswith("cd /opt/highlightz && ") for c in cmds)
    assert all("<" not in c and "price_" not in c for c in cmds), "a placeholder or a full id"
    # And each of those commands works.
    tail = cmds[1].split("--price ")[1].split()[0]
    code, _ = run(env, fake, "--price", tail, capsys=capsys)
    assert code == 0 and "STRIPE_PRICE_ID_HOURS=price_DDDDDDhour02" in env.read_text()


def test_stripe_unreachable_changes_nothing(env, capsys):
    class Broken:
        class prices:
            @staticmethod
            def list(params):
                raise RuntimeError("network")
    before = env.read_text()
    code, out = run(env, Broken(), capsys=capsys)
    assert code == 1 and env.read_text() == before and S.NOTHING in out


# ── the checkout ────────────────────────────────────────────────────────────

def _checkout(monkeypatch, fake_price):
    got = {}

    class _Sessions:
        @staticmethod
        def create(params):
            got.update(params)
            return type("S", (), {"url": "https://checkout.example/z", "id": "cs_z"})()

    class _Prices:
        @staticmethod
        def retrieve(pid):
            if fake_price is None:
                raise RuntimeError("no such price")
            return fake_price
    fake = type("C", (), {})()
    fake.v1 = type("V1", (), {})()
    fake.v1.checkout = type("Co", (), {"sessions": _Sessions})()
    fake.v1.prices = _Prices
    monkeypatch.setattr(stripe_billing, "_client", lambda: fake)
    monkeypatch.setattr(stripe_billing, "_hours_price_seen", {})
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_stub")
    stripe_billing.create_hours_checkout({"id": "f", "email": ""}, 10)
    return got["line_items"][0]


def test_the_checkout_uses_the_owners_price_when_stripe_confirms_it(monkeypatch):
    monkeypatch.setattr(settings, "stripe_price_id_hours", "price_AAAAAAhour01")
    line = _checkout(monkeypatch, price("price_AAAAAAhour01"))
    assert line == {"quantity": 10, "price": "price_AAAAAAhour01"}


@pytest.mark.parametrize("bad", [price("p", amount=200), price("p", kind="recurring"), price("p", active=False), None])
def test_a_wrong_or_unreadable_price_falls_back_to_our_own_dollar(monkeypatch, bad):
    monkeypatch.setattr(settings, "stripe_price_id_hours", "price_AAAAAAhour01")
    line = _checkout(monkeypatch, bad)
    assert "price" not in line and line["price_data"]["unit_amount"] == 100 and line["quantity"] == 10


def test_unset_means_our_own_dollar_line(monkeypatch):
    monkeypatch.setattr(settings, "stripe_price_id_hours", "")
    line = _checkout(monkeypatch, price("x"))
    assert line["price_data"]["unit_amount"] == 100
