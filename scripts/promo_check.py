"""Is the holiday sale's code in Stripe what the landing page says it is?
Read-only: asks Stripe, changes nothing.

    venv/bin/python scripts/promo_check.py

The landing page (src/dashboard/holiday_sale.py) promises HOLIDAY_SALE_PERCENT
off the first payment of the plans in HOLIDAY_SALE_PLANS (Pro only since
2026-11-24) with the code HOLIDAY_SALE_CODE. The discount itself is a Stripe
Promotion Code, so the page can only be as true as that code: this checks it
is active, takes that percentage, applies once (the first payment only), works
on those plans and on no other plan on sale, and has not run out or expired.
scripts/promo_restrict.py fixes the plans it works on.

Prints the code and the tails of Stripe ids only.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def g(obj, key, default=None):
    """Field access for dicts AND StripeObjects (which are not dicts)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return getattr(obj, key, default)
    except Exception:
        return default


def as_id(x) -> str:
    if not x:
        return ""
    return x if isinstance(x, str) else str(g(x, "id", "") or "")


def tail(x) -> str:
    s = as_id(x)
    return "..." + s[-6:] if s else "?"


def _client():
    from config.settings import settings
    if not settings.stripe_secret_key:
        raise SystemExit("STRIPE_SECRET_KEY is not set in .env, so Stripe cannot be asked.")
    import stripe
    raw = stripe.StripeClient(settings.stripe_secret_key)
    return getattr(raw, "v1", raw)


def _when(ts) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def problems(promo, coupon, pct: int, plan_products: dict[str, str], now: float,
             others: dict[str, str] | None = None) -> list[str]:
    """Every way this code is not what the page promises. Empty when it is.
    `plan_products` are the plans the sale names (HOLIDAY_SALE_PLANS), as
    plan -> Stripe product; `others` the plans on sale that it leaves out,
    which the code must NOT work on (Pro only, 2026-11-24)."""
    out = []
    if not g(promo, "active"):
        out.append("the promotion code is switched off in Stripe")
    if g(promo, "expires_at") and int(g(promo, "expires_at")) <= now:
        out.append("the promotion code expired " + _when(g(promo, "expires_at")))
    mx, used = g(promo, "max_redemptions"), int(g(promo, "times_redeemed") or 0)
    if mx and used >= int(mx):
        out.append(f"the promotion code is used up ({used} of {mx})")
    if coupon is None:
        out.append("Stripe did not say which coupon it gives")
        return out
    if g(coupon, "valid") is False:
        out.append("its coupon is no longer valid")
    if g(coupon, "percent_off") is None:
        out.append(f"its coupon takes a fixed amount off, not {pct}%")
    elif float(g(coupon, "percent_off")) != float(pct):
        out.append(f"its coupon takes {g(coupon, 'percent_off'):g}% off, the page says {pct}%")
    dur = g(coupon, "duration")
    if dur != "once":
        months = g(coupon, "duration_in_months")
        what = f"for {months} months" if dur == "repeating" else "forever" if dur == "forever" else str(dur)
        out.append(f"its coupon applies {what}, the page says the first payment only")
    if g(coupon, "redeem_by") and int(g(coupon, "redeem_by")) <= now:
        out.append("its coupon could only be redeemed until " + _when(g(coupon, "redeem_by")))
    limited = list(g(g(coupon, "applies_to"), "products", []) or [])
    others = {k: v for k, v in (others or {}).items() if v}
    if limited:
        for plan, product in plan_products.items():
            if product and product not in limited:
                out.append(f"its coupon is limited to other products, not {plan}")
        for plan, product in others.items():
            if product in limited:
                out.append(f"its coupon also works on {plan}, the page says "
                           f"{' and '.join(plan_products) or 'other plans'} only")
    elif others:
        out.append(f"its coupon works on every plan, {' and '.join(others)} too; the page says "
                   f"{' and '.join(plan_products) or 'other plans'} only "
                   "(scripts/promo_restrict.py fixes that)")
    restr = g(promo, "restrictions")
    if g(restr, "minimum_amount"):
        out.append(f"the code needs a minimum order of {int(g(restr, 'minimum_amount')) / 100:g} "
                   f"{str(g(restr, 'minimum_amount_currency') or '').upper()}")
    return out


def coupon_of(client, promo):
    """The promotion code's coupon, with applies_to (newer Stripe API
    versions keep it under `promotion`)."""
    coupon = g(promo, "coupon") or g(g(promo, "promotion"), "coupon")
    if isinstance(coupon, str) or (coupon is not None and g(coupon, "applies_to") is None):
        try:
            return client.coupons.retrieve(as_id(coupon), params={"expand": ["applies_to"]})
        except Exception as exc:
            print(f"Could not read the coupon ({type(exc).__name__}).")
            return None if isinstance(coupon, str) else coupon
    return coupon


def products(client) -> tuple[dict[str, str], dict[str, str]]:
    """(the sale's plans, the plans on sale it leaves out), each label ->
    Stripe product id, from the configured prices."""
    from config.settings import settings
    from src.billing.plans import PLAN_LIMITS, SELLABLE_PLANS
    from src.dashboard.holiday_sale import sale_plans
    wanted = sale_plans()
    sale, rest = {}, {}
    for key in SELLABLE_PLANS:
        price_id = getattr(settings, f"stripe_price_id_{key}", "")
        if not price_id:
            continue
        try:
            product = as_id(g(client.prices.retrieve(price_id), "product"))
        except Exception:
            product = ""
        (sale if key in wanted else rest)[PLAN_LIMITS[key]["label"]] = product
    return sale, rest


def main(argv=None, client=None, now: float | None = None) -> int:
    from config.settings import settings
    code = (settings.holiday_sale_code or "").strip().upper()
    pct = int(settings.holiday_sale_percent or 0)
    if not code:
        print("HOLIDAY_SALE_CODE is empty, so the landing page shows no sale. Nothing to check.")
        return 0
    client = client or _client()
    now = now if now is not None else datetime.now(timezone.utc).timestamp()

    page = client.promotion_codes.list(params={"code": code, "limit": 20})
    found = list(g(page, "data", []) or [])
    if not found:
        print(f"PROBLEM: Stripe has no promotion code called {code}. Checkout will refuse it.")
        print(f"In Stripe: Product catalogue > Coupons > New: {pct}% off, Duration Once; "
              f"then add a promotion code named {code}.")
        return 1
    live = [p for p in found if g(p, "active")]
    promo = live[0] if live else found[0]
    if len(live) > 1:
        print(f"Note: {len(live)} active codes are called {code}; checking {tail(promo)}.")

    coupon = coupon_of(client, promo)
    plan_products, others = products(client)

    pct_off = g(coupon, "percent_off")
    used, mx = int(g(promo, "times_redeemed") or 0), g(promo, "max_redemptions")
    print(f"{code} in Stripe (promotion code {tail(promo)}, coupon {tail(coupon)}): "
          + ("active" if g(promo, "active") else "SWITCHED OFF"))
    print("  Takes:   " + (f"{pct_off:g}% off" if pct_off is not None else "a fixed amount off")
          + ", " + str(g(coupon, "duration") or "?"))
    print(f"  Used:    {used} time{'s' if used != 1 else ''}"
          + (f" of {mx}" if mx else ", no limit")
          + (", expires " + _when(g(promo, "expires_at")) if g(promo, "expires_at") else ", no expiry"))

    bad = problems(promo, coupon, pct, plan_products, now, others)
    if bad:
        for b in bad:
            print("PROBLEM: " + b + ".")
        return 1
    from src.billing.plans import period
    plans = " and ".join(f"{p} (a {period(p.lower())})" for p in plan_products) or "the paid plans"
    print(f"OK: {code} takes {pct}% off the first payment of {plans}"
          + (f" and not {' or '.join(others)}" if others else "") + ", as the landing page says.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
