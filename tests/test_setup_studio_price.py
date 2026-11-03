"""scripts/setup_studio_price.py: finds Studio's $250 price on the owner's
product, writes it into .env, and makes the Customer Portal offer Pro and
Studio. Runs against a fake Stripe here; the real run is on the server."""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "setup_studio_price.py"
spec = importlib.util.spec_from_file_location("setup_studio_price", _PATH)
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

PROD = "prod_VYemn1IbgCQEkz"     # the owner's Studio product, from the dashboard


def price(pid, cents=25000, interval="month", currency="usd", active=True, product=PROD, live=True):
    return {"id": pid, "active": active, "type": "recurring", "unit_amount": cents, "currency": currency,
            "recurring": {"interval": interval, "interval_count": 1}, "product": product, "livemode": live}


class _Obj(dict):
    pass


class FakeStripe:
    def __init__(self, prices, configs, default="price_S"):
        self._prices = prices
        self._configs = configs
        self.updates = []
        outer = self

        class Products:
            def retrieve(self, pid):
                if pid != PROD:
                    raise RuntimeError("No such product")
                return {"id": PROD, "name": "Highlightz Clipping Tool", "description": "$250 Elite Tier",
                        "active": True, "default_price": default}

        class Prices:
            def list(self, params):
                if "product" in params:
                    return {"data": [p for p in outer._prices if p["product"] == params["product"]]}
                # the account-wide search: Stripe filters by these, so the fake does too
                assert params["type"] == "recurring" and params["recurring"] == {"interval": "month"}
                return {"data": [p for p in outer._prices if p["active"] and p["currency"] == "usd"],
                        "has_more": False}

            def retrieve(self, pid):
                return {"price_pro": {"id": "price_pro", "product": "prod_P"}}[pid]

        class Configs:
            def list(self, params):
                return {"data": outer._configs}

            def update(self, cid, params):
                outer.updates.append((cid, params))

        class Portal:
            configurations = Configs()

        self.products, self.prices, self.billing_portal = Products(), Prices(), Portal()


def cfg(cid, products, enabled=True):
    return {"id": cid, "features": {"subscription_update": {
        "enabled": enabled, "default_allowed_updates": ["price"], "proration_behavior": "always_invoice",
        "products": [{"product": p, "prices": [x]} for p, x in products]}}}


# ── picking the price ────────────────────────────────────────────────────────

def test_the_default_250_monthly_price_is_picked():
    assert S.pick_price({"default_price": "price_S"}, [price("price_S")], 25000) == "price_S"


@pytest.mark.parametrize("bad, why", [
    (dict(cents=2500), "not 25000"), (dict(interval="year"), "every month"),
    (dict(currency="eur"), "USD"), (dict(active=False), "archived")])
def test_a_price_that_is_not_250_a_month_in_usd_is_refused(bad, why):
    with pytest.raises(SystemExit) as e:
        S.pick_price({"default_price": "price_S"}, [price("price_S", **bad)], 25000)
    assert why in str(e.value)


def test_a_wrong_default_falls_back_to_the_one_right_price():
    prices = [price("price_old", cents=20000), price("price_S")]
    assert S.pick_price({"default_price": "price_old"}, prices, 25000) == "price_S"


def test_two_right_prices_is_not_guessed_between():
    with pytest.raises(SystemExit) as e:
        S.pick_price({"default_price": None}, [price("price_a"), price("price_b")], 25000)
    assert "More than one" in str(e.value)


# ── .env ─────────────────────────────────────────────────────────────────────

def test_env_line_is_replaced_in_place_and_nothing_else_moves():
    text = "A=1\n# STRIPE_PRICE_ID_STUDIO=commented\nSTRIPE_PRICE_ID_STUDIO=\nB=2\n"
    out, changed = S.set_env_line(text, "STRIPE_PRICE_ID_STUDIO", "price_S")
    assert changed and out == "A=1\n# STRIPE_PRICE_ID_STUDIO=commented\nSTRIPE_PRICE_ID_STUDIO=price_S\nB=2\n"


def test_env_line_is_appended_when_missing_even_without_a_final_newline():
    out, changed = S.set_env_line("A=1", "STRIPE_PRICE_ID_STUDIO", "price_S")
    assert changed and out == "A=1\nSTRIPE_PRICE_ID_STUDIO=price_S\n"
    assert S.set_env_line(out, "STRIPE_PRICE_ID_STUDIO", "price_S") == (out, False)


# ── the whole run ────────────────────────────────────────────────────────────

def _run(tmp_path, argv, configs, env="STRIPE_SECRET_KEY=sk\nSTRIPE_PRICE_ID_PRO=price_pro\n"):
    env_path = tmp_path / ".env"
    env_path.write_text(env)
    env_path.chmod(0o600)
    fake = FakeStripe([price("price_S")], configs)
    S.main(argv, client=fake, env_path=env_path, pro_price="price_pro")
    return env_path, fake


def test_a_run_sets_the_price_backs_up_env_and_fixes_the_portal(tmp_path, capsys):
    configs = [cfg("bpc_old", [("prod_St", "price_starter"), ("prod_P", "price_pro")]),
               cfg("bpc_off", [], enabled=False),
               cfg("bpc_ok", [("prod_P", "price_pro"), (PROD, "price_S")])]
    env_path, fake = _run(tmp_path, [], configs)
    assert env_path.read_text().endswith("STRIPE_PRICE_ID_STUDIO=price_S\n")
    assert "STRIPE_SECRET_KEY=sk\n" in env_path.read_text()
    assert oct(env_path.stat().st_mode & 0o777) == "0o600"
    backups = list(tmp_path.glob(".env.bak-studio-*"))
    assert len(backups) == 1 and "STUDIO" not in backups[0].read_text()
    assert [c for c, _ in fake.updates] == ["bpc_old"]
    su = fake.updates[0][1]["features"]["subscription_update"]
    assert su["products"] == [{"product": "prod_P", "prices": ["price_pro"]},
                              {"product": PROD, "prices": ["price_S"]}]
    assert su["proration_behavior"] == "always_invoice"
    out = capsys.readouterr().out
    assert "Highlightz Clipping Tool" in out and "LIVE" in out and "left alone" in out


def test_check_changes_nothing(tmp_path, capsys):
    configs = [cfg("bpc_old", [("prod_St", "price_starter"), ("prod_P", "price_pro")])]
    env_path, fake = _run(tmp_path, ["--check"], configs)
    assert "STUDIO" not in env_path.read_text() and fake.updates == []
    assert not list(tmp_path.glob(".env.bak-studio-*"))
    assert "would set STRIPE_PRICE_ID_STUDIO=price_S" in capsys.readouterr().out


def test_a_second_run_is_a_no_op(tmp_path):
    configs = [cfg("bpc_ok", [("prod_P", "price_pro"), (PROD, "price_S")])]
    env_path, fake = _run(tmp_path, [], configs, env="STRIPE_PRICE_ID_STUDIO=price_S\n")
    assert fake.updates == [] and not list(tmp_path.glob(".env.bak-studio-*"))


def test_a_key_from_another_stripe_account_says_so(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("A=1\n")
    with pytest.raises(SystemExit) as e:
        S.main(["--product", "prod_other"], client=FakeStripe([], []), env_path=env_path, pro_price="price_pro")
    assert "live key" in str(e.value) and env_path.read_text() == "A=1\n"


def test_the_script_targets_the_owners_product():
    assert S.STUDIO_PRODUCT == "prod_VYemn1IbgCQEkz"


# Both ids came off screenshots, where capital I and lowercase l look alike.
MISREAD = PROD.replace("1Ib", "1lb")


def test_a_misread_product_id_still_finds_the_250_price(tmp_path, capsys):
    env_path = tmp_path / ".env"
    env_path.write_text("A=1\n")
    configs = [cfg("bpc_old", [("prod_St", "price_starter"), ("prod_P", "price_pro")])]
    fake = FakeStripe([price("price_S"), price("price_pro_x", cents=2500, product="prod_P")], configs)
    S.main(["--product", MISREAD], client=fake, env_path=env_path, pro_price="price_pro")
    assert env_path.read_text() == "A=1\nSTRIPE_PRICE_ID_STUDIO=price_S\n"
    assert fake.updates[0][1]["features"]["subscription_update"]["products"][1] == {
        "product": PROD, "prices": ["price_S"]}
    assert "looking for the account's $250/month price" in capsys.readouterr().out


def test_a_misread_id_with_two_250_prices_in_the_account_changes_nothing(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("A=1\n")
    fake = FakeStripe([price("price_a"), price("price_b", product="prod_other")], [])
    with pytest.raises(SystemExit) as e:
        S.main(["--product", MISREAD], client=fake, env_path=env_path, pro_price="price_pro")
    assert "more than one" in str(e.value) and env_path.read_text() == "A=1\n"
