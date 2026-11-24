"""Make Studio a yearly subscription at the same $250 (owner, 2026-11-24:
"change the 250$ one to be a yearly subscription now too instead ... keep the
same price").

Run on the server, from /opt/highlightz, then restart the app:
    venv/bin/python scripts/setup_studio_yearly.py --check   # only report
    venv/bin/python scripts/setup_studio_yearly.py           # do it

In this order, stopping at the first thing that is not right, with NOTHING
WAS CHANGED:
  1. STRIPE_PRICE_ID_STUDIO in .env must be Studio's $250 price in Stripe
     (the monthly one, or the yearly one from an earlier run);
  2. the Studio product's active $250-a-year USD price is found, or made:
     a Stripe price cannot be edited, so yearly is a new price on the same
     product, and the monthly one is left exactly as it is;
  3. .env is written in one step, with a backup beside it:
       STRIPE_PRICE_ID_STUDIO=<the yearly price>
       STUDIO_INTERVAL=year                      (every page then says "a year")
       STRIPE_PRICE_ID_STUDIO_MONTHLY=<the monthly price>
     the last so anyone already paying monthly is still Studio;
  4. every saved Customer Portal configuration that lets people switch plans
     offers Pro (monthly) and Studio (yearly).

Nobody's subscription is touched: anyone on Studio monthly keeps paying
monthly until they change plan. Safe to run twice.
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("setup_studio_price", ROOT / "scripts" / "setup_studio_price.py")
_S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_S)
g, as_id, set_env_line, write_env, portal_changes = (
    _S.g, _S.as_id, _S.set_env_line, _S.write_env, _S.portal_changes)

REFUSED = "\nNOTHING WAS CHANGED."


def price_problem(price, cents: int, interval: str) -> str:
    """Why this is not Studio's $250 price billed every `interval`, or ""."""
    rec = g(price, "recurring")
    checks = [
        (g(price, "active") is True, "it is archived"),
        (g(price, "type") == "recurring" and rec is not None, "it is not a subscription price"),
        (g(rec, "interval") == interval and int(g(rec, "interval_count", 1) or 1) == 1,
         f"it is not billed every {interval}"),
        (str(g(price, "currency", "")).lower() == "usd", "it is not in USD"),
        (g(price, "unit_amount") == cents, f"it is {g(price, 'unit_amount')} cents, not {cents}"),
    ]
    return next((why for ok, why in checks if not ok), "")


def env_values(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        bare = line.strip()
        bare = bare[len("export "):] if bare.startswith("export ") else bare
        if "=" in bare and not bare.startswith("#"):
            k, v = bare.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _client():
    from config.settings import settings
    if not settings.stripe_secret_key:
        raise SystemExit("STRIPE_SECRET_KEY is not set in .env, so Stripe cannot be asked." + REFUSED)
    import stripe
    raw = stripe.StripeClient(settings.stripe_secret_key)
    return getattr(raw, "v1", raw)


def main(argv=None, client=None, env_path: pathlib.Path | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="report only; change nothing")
    args = ap.parse_args(argv)

    from src.billing.plans import PLAN_LIMITS
    cents = int(PLAN_LIMITS["studio"]["price"]) * 100
    env_path = env_path or (ROOT / ".env")
    if not env_path.exists():
        raise SystemExit(f"No .env at {env_path}. Run this from the app's folder on the server." + REFUSED)
    text = env_path.read_text()
    env = env_values(text)
    current = env.get("STRIPE_PRICE_ID_STUDIO", "")
    pro_price = env.get("STRIPE_PRICE_ID_PRO", "")
    if not current:
        raise SystemExit("STRIPE_PRICE_ID_STUDIO is not set in .env, so there is no Studio price "
                         "to start from. Run scripts/setup_studio_price.py first." + REFUSED)
    client = client or _client()

    # 1. What Studio is now.
    try:
        now_price = client.prices.retrieve(current)
    except Exception as exc:
        raise SystemExit(f"Stripe has no price {current} ({type(exc).__name__}). Is STRIPE_SECRET_KEY "
                         "the key of the account Studio is in?" + REFUSED)
    product = as_id(g(now_price, "product"))
    if not product:
        raise SystemExit(f"Stripe did not say which product {current} belongs to." + REFUSED)
    if price_problem(now_price, cents, "year") == "":
        yearly, monthly = current, env.get("STRIPE_PRICE_ID_STUDIO_MONTHLY", "")
        print(f"Studio    {current} is already ${cents // 100} a year.")
    else:
        why = price_problem(now_price, cents, "month")
        if why:
            raise SystemExit(f"STRIPE_PRICE_ID_STUDIO ({current}) is not Studio's ${cents // 100} "
                             f"monthly price: {why}." + REFUSED)
        monthly, yearly = current, ""
        print(f"Studio    now {current}: ${cents // 100} a month, on product {product}")
    if pro_price and current == pro_price:
        raise SystemExit("STRIPE_PRICE_ID_STUDIO is the Pro price." + REFUSED)

    # 2. The yearly price: found on the product, or made.
    if not yearly:
        prices = list(g(client.prices.list(params={"product": product, "active": True, "limit": 100}),
                        "data", []) or [])
        good = [as_id(p) for p in prices if not price_problem(p, cents, "year")]
        if len(good) > 1:
            raise SystemExit(f"More than one ${cents // 100}-a-year price on {product} "
                             f"({', '.join(good)}). Archive the extra one in Stripe." + REFUSED)
        if good:
            yearly = good[0]
            print(f"Yearly    found {yearly}: ${cents // 100} a year")
        elif args.check:
            print(f"Yearly    would make a ${cents // 100}-a-year USD price on {product}")
        else:
            made = client.prices.create(params={
                "product": product, "unit_amount": cents, "currency": "usd",
                "recurring": {"interval": "year", "interval_count": 1},
                "nickname": "Studio yearly"})
            yearly = as_id(made)
            why = price_problem(made, cents, "year")
            if not yearly or why:
                raise SystemExit(f"Stripe made a price that is not right ({why or 'no id'}). "
                                 "Check it in the dashboard." + REFUSED)
            print(f"Yearly    made {yearly}: ${cents // 100} a year")

    # Who is on monthly now, so the owner knows who keeps it.
    if monthly:
        try:
            subs = list(g(client.subscriptions.list(params={"price": monthly, "status": "active",
                                                            "limit": 100}), "data", []) or [])
            n = len(subs)
            print(f"Monthly   {n} active subscription{'s' if n != 1 else ''} on Studio monthly "
                  "keep it, and stay Studio.")
        except Exception as exc:
            print(f"Monthly   could not count Studio monthly subscriptions ({type(exc).__name__}).")

    # 3. .env, in one write.
    if not yearly:
        print("\nChecked only; nothing was changed.")
        return 0
    new = text
    for key, value in (("STRIPE_PRICE_ID_STUDIO", yearly), ("STUDIO_INTERVAL", "year"),
                       ("STRIPE_PRICE_ID_STUDIO_MONTHLY", monthly)):
        if not value:
            continue
        new, changed = set_env_line(new, key, value)
        if changed:
            print(f".env      {'would set' if args.check else 'sets'} {key}={value}")
    if new == text:
        print(".env      already right")
    elif not args.check:
        backup = write_env(env_path, new)
        print(f".env      written (backup: {backup.name})")

    # 4. The portal offers Pro monthly and Studio yearly.
    if not pro_price:
        print("Portal    STRIPE_PRICE_ID_PRO is not set, so plan switching was not touched.")
    else:
        pro_product = as_id(g(client.prices.retrieve(pro_price), "product"))
        cfgs = list(g(client.billing_portal.configurations.list(params={"active": True, "limit": 100}),
                      "data", []) or [])
        if not cfgs:
            print("Portal    no saved configuration; the app builds one with Pro and Studio on first use.")
        for cid, params, say in portal_changes(cfgs, [(pro_product, pro_price), (product, yearly)]):
            if params and not args.check:
                client.billing_portal.configurations.update(cid, params=params)
                say = say.replace("will offer", "now offers")
            print(f"Portal    {cid}: {say}")

    print("\nChecked only; nothing was changed." if args.check else
          "\nDone. Restart the app so every page says $250 a year: systemctl restart highlightz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
