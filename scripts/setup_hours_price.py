"""Connect the watching-hours checkout to the owner's "1 Hour of Highlightz"
product in Stripe. Run on the server, from /opt/highlightz:

    venv/bin/python scripts/setup_hours_price.py            # do it
    venv/bin/python scripts/setup_hours_price.py --check    # only report

Owner, 2026-11-20, with a screenshot of the product being added in Stripe:
"lets go back to the hours change i am making here is what I have in stripe".
The checkout works without it (it sends its own $1 line); with it, Stripe's
page shows the owner's name, description and image for the hour.

WHAT IT DOES. Asks Stripe, with the app's own key, for every active one-off
USD price of exactly $1.00 (watch_hours.PRICE_CENTS). One found: that is it.
Several: the ones on a product whose name says "hour"; if that still leaves
more than one, it lists them with the finished command for each and changes
nothing. The chosen price is checked again (active, one-off, USD, $1.00, on an
active product) and written to .env as STRIPE_PRICE_ID_HOURS, keeping a
backup. Any refusal says NOTHING WAS CHANGED. Never takes an id typed off a
screenshot unless Stripe confirms it, which is the point: ids read off a
dashboard have already been wrong once (setup_studio_price.py).

Restart the site afterwards; the checkout reads the setting at start, and
re-checks the price with Stripe at most once an hour.
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ENV_KEY = "STRIPE_PRICE_ID_HOURS"
NOTHING = "NOTHING WAS CHANGED."

_spec = importlib.util.spec_from_file_location("setup_studio_price", ROOT / "scripts" / "setup_studio_price.py")
_studio = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_studio)
g, as_id, set_env_line = _studio.g, _studio.as_id, _studio.set_env_line


def problem(price, product) -> str:
    """Why this price cannot be the watching hour's, or ""."""
    from src.billing.stripe_billing import hours_price_problem
    why = hours_price_problem(price)
    if why:
        return why
    if product is not None and not g(product, "active"):
        return "its product is archived"
    return ""


def one_off_dollar_prices(client) -> list:
    """Every active one-off USD price of exactly PRICE_CENTS, all pages."""
    from src.billing import watch_hours
    found, after = [], None
    for _ in range(50):
        params = {"active": True, "type": "one_time", "currency": "usd", "limit": 100,
                  **({"starting_after": after} if after else {})}
        page = client.prices.list(params=params)
        data = list(g(page, "data", []) or [])
        found += [p for p in data if g(p, "unit_amount") == watch_hours.PRICE_CENTS]
        if not g(page, "has_more") or not data:
            break
        after = as_id(data[-1])
    return found


def choose(prices, products: dict, wanted: str = "") -> tuple[str, list[str]]:
    """(the price to use, or "" and the lines saying why not)."""
    usable = [p for p in prices if not problem(p, products.get(as_id(g(p, "product"))))]
    if wanted:
        # The full id, or the end of it as this script prints it (pasted
        # output shows only the end of a Stripe id).
        hit = [p for p in usable if as_id(p) == wanted or (len(wanted) >= 6 and as_id(p).endswith(wanted))]
        if len(hit) == 1:
            return as_id(hit[0]), []
        if hit:
            return "", [f"More than one price ends in {wanted}; give more of it."]
        return "", [f"No live $1.00 one-off USD price in this account ends in {wanted}."]
    if not usable:
        return "", ["No active one-off $1.00 USD price in this Stripe account. Finish adding the",
                    "product in Stripe (One-off, $1.00, USD), then run this again."]
    if len(usable) == 1:
        return as_id(usable[0]), []
    named = [p for p in usable
             if "hour" in str(g(products.get(as_id(g(p, "product"))), "name", "") or "").lower()]
    if len(named) == 1:
        return as_id(named[0]), []
    pool = named or usable
    ids = [as_id(p) for p in pool]
    n = 6
    while n < max(len(i) for i in ids) and len({i[-n:] for i in ids}) < len(ids):
        n += 2
    lines = ["More than one active $1.00 one-off price could be it. Pick yours and paste its line:"]
    for p in pool:
        name = str(g(products.get(as_id(g(p, "product"))), "name", "") or "?")
        lines.append(f"  {name!r}:")
        lines.append(f"    cd /opt/highlightz && venv/bin/python scripts/setup_hours_price.py --price "
                     f"{as_id(p)[-n:]} && systemctl restart highlightz")
    return "", lines


def main(argv=None, client=None, env_path: pathlib.Path | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="report only; change nothing")
    ap.add_argument("--price", default="", help="one of the prices this script listed")
    args = ap.parse_args(argv)

    env_path = env_path or (ROOT / ".env")
    if not env_path.exists():
        print(f"No .env at {env_path}. Run this from /opt/highlightz on the server. {NOTHING}")
        return 1
    try:
        client = client or _studio._client()
        prices = one_off_dollar_prices(client)
        products = {}
        for pid in {as_id(g(p, "product")) for p in prices}:
            try:
                products[pid] = client.products.retrieve(pid)
            except Exception:
                products[pid] = None
    except SystemExit as exc:
        print(f"{exc} {NOTHING}")
        return 1
    except Exception as exc:
        print(f"Stripe could not be asked ({type(exc).__name__}: {str(exc)[:160]}). {NOTHING}")
        return 1

    price_id, why = choose(prices, products, args.price.strip())
    if not price_id:
        for line in why:
            print(line)
        print(NOTHING)
        return 1
    price = next(p for p in prices if as_id(p) == price_id)
    product = products.get(as_id(g(price, "product")))
    mode = "LIVE" if g(price, "livemode") else "TEST"
    print(f"Product   {g(product, 'name', '?')!r}" + (f", {g(product, 'description')!r}"
                                                      if g(product, "description") else ""))
    print(f"Price     ...{price_id[-6:]}: $1.00 USD, one-off ({mode} mode)")
    if mode == "TEST":
        print("          This is a TEST-mode price: the server's key is a test key.")

    text = env_path.read_text()
    new_text, changed = set_env_line(text, ENV_KEY, price_id)
    if not changed:
        print(f".env      {ENV_KEY} is already set to it. Nothing to change.")
    elif args.check:
        print(f".env      would set {ENV_KEY} to it. --check: {NOTHING}")
    else:
        backup = _studio.write_env(env_path, new_text)
        backup = backup.rename(backup.with_name(backup.name.replace("bak-studio", "bak-hours")))
        print(f".env      set {ENV_KEY} (backup: {backup.name}). Restart the site to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
