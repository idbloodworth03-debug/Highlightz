"""Connect the app to the Studio price in Stripe, and offer Studio (not Starter)
in the Customer Portal.

The owner made the Studio product in the Stripe dashboard on 2026-11-02:
prod_VYemn1IbgCQEkz ("Highlightz Clipping Tool", $250.00 USD per month). The
app needs that product's PRICE id in .env as STRIPE_PRICE_ID_STUDIO. This
finds it with the app's own Stripe key, checks it really is $250 a month in
USD, writes it into .env (keeping a backup), and updates every saved portal
configuration that lets customers switch plans so it offers Pro and Studio.

Run on the server, from /opt/highlightz, then restart the app:
    venv/bin/python scripts/setup_studio_price.py           # do it
    venv/bin/python scripts/setup_studio_price.py --check   # only report

Safe to run twice: anything already right is left alone and said so.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STUDIO_PRODUCT = "prod_VYemn1IbgCQEkz"
ENV_KEY = "STRIPE_PRICE_ID_STUDIO"


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


def price_problem(price, expected_cents: int) -> str:
    """Why this price is not Studio's, or "" when it is."""
    rec = g(price, "recurring")
    checks = [
        (g(price, "active") is True, "it is archived"),
        (g(price, "type") == "recurring" and rec is not None, "it is not a subscription price"),
        (g(rec, "interval") == "month" and int(g(rec, "interval_count", 1) or 1) == 1,
         "it is not billed every month"),
        (str(g(price, "currency", "")).lower() == "usd", "it is not in USD"),
        (g(price, "unit_amount") == expected_cents,
         f"it is {g(price, 'unit_amount')} cents, not {expected_cents}"),
    ]
    for ok, why in checks:
        if not ok:
            return why
    return ""


def pick_price(product, prices, expected_cents: int) -> str:
    """The product's Studio price: its default price if that is right, else
    the ONE active price on it that is. Raises SystemExit with the reason
    otherwise; guessing between two $250 prices is not this script's call."""
    default = g(product, "default_price")
    default_id = as_id(default)
    by_id = {as_id(p): p for p in prices}
    if default_id:
        p = by_id.get(default_id) or (default if not isinstance(default, str) else None)
        if p is not None and not price_problem(p, expected_cents):
            return default_id
    good = [as_id(p) for p in prices if not price_problem(p, expected_cents)]
    if len(good) == 1:
        return good[0]
    if not good:
        reasons = "; ".join(f"{as_id(p)}: {price_problem(p, expected_cents)}" for p in prices) or "it has no prices"
        raise SystemExit(f"No ${expected_cents // 100}/month USD price on this product ({reasons}).")
    raise SystemExit(f"More than one ${expected_cents // 100}/month price on this product "
                     f"({', '.join(good)}). Archive the extra one in Stripe, or set "
                     f"{ENV_KEY} in .env by hand.")


def set_env_line(text: str, key: str, value: str) -> tuple[str, bool]:
    """`text` with `key=value` set: the existing line replaced in place, or a
    new line appended. Comments and every other line are kept as they are.
    Returns (new_text, changed)."""
    lines = text.splitlines(keepends=True)
    want = f"{key}={value}"
    for i, line in enumerate(lines):
        bare = line.strip()
        name = bare[len("export "):] if bare.startswith("export ") else bare
        if name.split("=", 1)[0].strip() == key and "=" in name:
            if bare == want:
                return text, False
            ending = "\n" if line.endswith("\n") else ""
            lines[i] = want + ending
            return "".join(lines), True
    sep = "" if (not text or text.endswith("\n")) else "\n"
    return text + sep + want + "\n", True


def write_env(path: pathlib.Path, text: str) -> pathlib.Path:
    """Back the file up beside itself, then replace it in one step with the
    same permissions and owner. Returns the backup's path."""
    st = path.stat()
    backup = path.with_name(f"{path.name}.bak-studio-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, st.st_mode & 0o7777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except (PermissionError, AttributeError):
            pass
        os.replace(tmp, path)
    except Exception:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise
    return backup


def portal_changes(configs, wanted: list[tuple[str, str]]) -> list[tuple[str, dict | None, str]]:
    """For each portal configuration: (id, update params or None, what to say).
    Only configurations that already let customers switch plans are changed;
    one with switching off offers no plans at all, so there is nothing to fix."""
    out = []
    want_products = [{"product": prod, "prices": [price]} for prod, price in wanted]
    for cfg in configs:
        cid = as_id(cfg)
        su = g(g(cfg, "features"), "subscription_update")
        if not g(su, "enabled"):
            out.append((cid, None, "plan switching is off here, so it offers no plans; left alone"))
            continue
        have = [{"product": as_id(g(p, "product")), "prices": [as_id(x) for x in (g(p, "prices") or [])]}
                for p in (g(su, "products") or [])]
        if have == want_products:
            out.append((cid, None, "already offers Pro and Studio"))
            continue
        allowed = list(g(su, "default_allowed_updates") or []) or ["price"]
        params = {"features": {"subscription_update": {
            "enabled": True,
            "default_allowed_updates": allowed,
            "products": want_products,
            "proration_behavior": g(su, "proration_behavior") or "create_prorations",
        }}}
        out.append((cid, params, "will offer Pro and Studio (was: "
                    + (", ".join(p["prices"][0] if p["prices"] else p["product"] for p in have) or "nothing")
                    + ")"))
    return out


def _client():
    from config.settings import settings
    if not settings.stripe_secret_key:
        raise SystemExit("STRIPE_SECRET_KEY is not set in .env, so Stripe cannot be asked.")
    import stripe
    raw = stripe.StripeClient(settings.stripe_secret_key)
    return getattr(raw, "v1", raw)


def main(argv=None, client=None, env_path: pathlib.Path | None = None,
         pro_price: str | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="report only; change nothing")
    ap.add_argument("--product", default=STUDIO_PRODUCT)
    args = ap.parse_args(argv)

    from src.billing.plans import PLAN_LIMITS
    expected = int(PLAN_LIMITS["studio"]["price"]) * 100
    env_path = env_path or (ROOT / ".env")
    if not env_path.exists():
        raise SystemExit(f"No .env at {env_path}. Run this from the app's folder on the server.")
    if pro_price is None:
        from config.settings import settings
        pro_price = settings.stripe_price_id_pro
    client = client or _client()

    try:
        product = client.products.retrieve(args.product)
    except Exception as exc:
        raise SystemExit(f"Stripe could not find {args.product} with this key ({exc}). Is "
                         "STRIPE_SECRET_KEY the live key of the account the product is in?")
    name, desc = g(product, "name", ""), g(product, "description", "")
    print(f"Product   {args.product}: {name!r}" + (f", {desc!r}" if desc else "")
          + ("" if g(product, "active") else "  (ARCHIVED)"))
    if not g(product, "active"):
        raise SystemExit("That product is archived in Stripe. Unarchive it first.")
    prices = list(g(client.prices.list(params={"product": args.product, "active": True, "limit": 100}),
                    "data", []) or [])
    price_id = pick_price(product, prices, expected)
    price = next((p for p in prices if as_id(p) == price_id), None)
    mode = "unknown" if price is None else ("LIVE" if g(price, "livemode") else "TEST")
    print(f"Price     {price_id}: ${expected // 100}.00 USD a month ({mode} mode)")
    if price_id == pro_price:
        raise SystemExit("That is the Pro price. Nothing changed.")

    text = env_path.read_text()
    new_text, changed = set_env_line(text, ENV_KEY, price_id)
    if not changed:
        print(f".env      {ENV_KEY} is already {price_id}")
    elif args.check:
        print(f".env      would set {ENV_KEY}={price_id}")
    else:
        backup = write_env(env_path, new_text)
        print(f".env      set {ENV_KEY}={price_id} (backup: {backup.name})")

    if not pro_price:
        print("Portal    STRIPE_PRICE_ID_PRO is not set, so plan switching was not touched.")
    else:
        pro_product = as_id(g(client.prices.retrieve(pro_price), "product"))
        cfgs = list(g(client.billing_portal.configurations.list(params={"active": True, "limit": 100}),
                      "data", []) or [])
        if not cfgs:
            print("Portal    no saved configuration; the app builds one with Pro and Studio on first use.")
        for cid, params, say in portal_changes(cfgs, [(pro_product, pro_price), (args.product, price_id)]):
            if params and not args.check:
                client.billing_portal.configurations.update(cid, params=params)
                say = say.replace("will offer", "now offers")
            print(f"Portal    {cid}: {say}")

    print("\nChecked only; nothing was changed." if args.check else
          "\nDone. Restart the app so it reads the new price: systemctl restart highlightz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
