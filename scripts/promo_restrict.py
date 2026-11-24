"""Make the sale's code work only on the plans the sale names (owner,
2026-11-24: "make CLIPPER work on Pro only").

Run on the server, from /opt/highlightz:
    venv/bin/python scripts/promo_restrict.py --check   # only report
    venv/bin/python scripts/promo_restrict.py           # do it

A Stripe coupon cannot be changed to cover fewer products once it exists, so
this makes a NEW coupon like the old one (same discount, same duration) that
applies only to the sale's plans (HOLIDAY_SALE_PLANS: Pro), switches the old
promotion code off and gives the new coupon a promotion code with the same
name (HOLIDAY_SALE_CODE: CLIPPER). Its limits come across: an expiry, the
first-order-only rule, a minimum order, and whatever is left of a
redemption limit. The old code and coupon stay in Stripe, switched off, so
its history is kept.

Stops at the first thing that is not right with NOTHING WAS CHANGED. If the
new code cannot be made, the old one is switched back on. Safe to run twice:
a code that already works on only those plans is left alone. Prints the code
and the tails of Stripe ids only.
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("promo_check", ROOT / "scripts" / "promo_check.py")
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)
g, as_id, tail = C.g, C.as_id, C.tail

REFUSED = "\nNOTHING WAS CHANGED."


def coupon_params(old, products: list[str], code: str, now: float, names: str = "Pro") -> dict:
    """A new coupon exactly like `old`, applying to `products` only."""
    params: dict = {"duration": g(old, "duration") or "once",
                    "applies_to": {"products": list(products)},
                    "name": (str(g(old, "name") or code) + f" ({names} only)")[:40],
                    "metadata": {"replaces": as_id(old)}}
    if g(old, "percent_off") is not None:
        params["percent_off"] = g(old, "percent_off")
    else:
        params["amount_off"] = g(old, "amount_off")
        params["currency"] = g(old, "currency")
    if params["duration"] == "repeating":
        params["duration_in_months"] = g(old, "duration_in_months")
    if g(old, "redeem_by") and int(g(old, "redeem_by")) > now:
        params["redeem_by"] = int(g(old, "redeem_by"))
    if g(old, "max_redemptions"):
        left = int(g(old, "max_redemptions")) - int(g(old, "times_redeemed") or 0)
        params["max_redemptions"] = max(1, left)
    return params


def promo_params(old, code: str, now: float) -> dict:
    """The new promotion code's limits, carried over from the old one."""
    params: dict = {"code": code, "metadata": {"replaces": as_id(old)}}
    if g(old, "expires_at") and int(g(old, "expires_at")) > now:
        params["expires_at"] = int(g(old, "expires_at"))
    if g(old, "max_redemptions"):
        left = int(g(old, "max_redemptions")) - int(g(old, "times_redeemed") or 0)
        params["max_redemptions"] = max(1, left)
    if as_id(g(old, "customer")):
        params["customer"] = as_id(g(old, "customer"))
    r = g(old, "restrictions") or {}
    keep = {k: g(r, k) for k in ("first_time_transaction", "minimum_amount", "minimum_amount_currency")
            if g(r, k)}
    if keep:
        params["restrictions"] = keep
    return params


def make_promo(client, coupon_id: str, params: dict):
    """Newer Stripe API versions name the coupon under `promotion`; older ones
    take `coupon`. Try the new shape, then the old."""
    try:
        return client.promotion_codes.create(
            params=dict(params, promotion={"type": "coupon", "coupon": coupon_id}))
    except Exception as first:
        if "promotion" not in str(first).lower():
            raise
        return client.promotion_codes.create(params=dict(params, coupon=coupon_id))


def main(argv=None, client=None, now: float | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="report only; change nothing")
    args = ap.parse_args(argv)

    from config.settings import settings
    code = (settings.holiday_sale_code or "").strip().upper()
    pct = int(settings.holiday_sale_percent or 0)
    if not code:
        raise SystemExit("HOLIDAY_SALE_CODE is empty, so there is no code to restrict." + REFUSED)
    client = client or C._client()
    now = now if now is not None else datetime.now(timezone.utc).timestamp()

    sale, others = C.products(client)
    if not sale or not all(sale.values()):
        raise SystemExit("Could not find the Stripe product of "
                         + (", ".join(k for k, v in sale.items() if not v) or "the sale's plans")
                         + " from its price in .env." + REFUSED)
    names = " and ".join(sale)
    live = [p for p in (g(client.promotion_codes.list(params={"code": code, "active": True,
                                                                "limit": 20}), "data", []) or [])
            if g(p, "active")]
    if not live:
        raise SystemExit(f"Stripe has no active promotion code called {code}. Make it first: "
                         f"Coupon {pct}% off, Duration Once, Product: {names}." + REFUSED)
    if len(live) > 1:
        raise SystemExit(f"{len(live)} active promotion codes are called {code} "
                         f"({', '.join(tail(p) for p in live)}). Switch the extra ones off "
                         "in Stripe first." + REFUSED)
    old = live[0]
    coupon = C.coupon_of(client, old)
    if coupon is None:
        raise SystemExit(f"Could not read the coupon behind {code}." + REFUSED)
    print(f"{code}      promotion code {tail(old)}, coupon {tail(coupon)}, used "
          f"{int(g(old, 'times_redeemed') or 0)} times")

    bad = C.problems(old, coupon, pct, sale, now, others)
    plan_bad = [b for b in bad if "works on every plan" in b or "also works on" in b]
    if not bad:
        print(f"Already right: {code} works on {names} only.")
        return 0
    if len(plan_bad) != len(bad):
        for b in bad:
            print("PROBLEM: " + b + ".")
        raise SystemExit(f"{code} is wrong in other ways too; fix those in Stripe first, so "
                         "they are not copied onto the new code." + REFUSED)

    c_params = coupon_params(coupon, list(sale.values()), code, now, names)
    p_params = promo_params(old, code, now)
    if args.check:
        print(f"Would make a coupon like {tail(coupon)} for {names} only, switch "
              f"{tail(old)} off, and give the new coupon the code {code}.")
        print("\nChecked only; nothing was changed.")
        return 0

    made = client.coupons.create(params=c_params)
    print(f"Coupon    made {tail(made)}: like the old one, {names} only")
    client.promotion_codes.update(as_id(old), params={"active": False})
    try:
        new = make_promo(client, as_id(made), p_params)
    except Exception as exc:
        try:
            client.promotion_codes.update(as_id(old), params={"active": True})
            client.coupons.delete(as_id(made))
        except Exception as back:
            raise SystemExit(f"Stripe refused the new code ({exc}) AND the old one could not be "
                             f"switched back on ({back}). {code} IS OFF: switch promotion code "
                             f"{as_id(old)} back on in the Stripe dashboard now.")
        raise SystemExit(f"Stripe refused the new code ({str(exc)[:160]}). The old {code} is "
                         "back on, exactly as it was." + REFUSED)
    print(f"Code      {code} is now {tail(new)}; the old {tail(old)} is switched off")

    after = C.problems(new, C.coupon_of(client, new), pct, sale, now, others)
    if after:
        for b in after:
            print("PROBLEM: " + b + ".")
        return 1
    print(f"\nDone: {code} takes {pct}% off the first payment of {names} only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
