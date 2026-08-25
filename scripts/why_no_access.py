"""Why does this person have no access when they have paid?

Answers the question end to end and names the exact step that broke, instead of
leaving you to correlate users.json against the Stripe dashboard by eye.

    /opt/highlightz/venv/bin/python scripts/why_no_access.py            # everyone
    /opt/highlightz/venv/bin/python scripts/why_no_access.py <name>     # one person
    /opt/highlightz/venv/bin/python scripts/why_no_access.py <name> --fix

READ-ONLY WITHOUT --fix. It reports; it changes nothing.

WHAT IT CHECKS, in the order things actually break:

  1. Is the deployed code the code you think it is, and is Stripe configured at
     all? A missing STRIPE_WEBHOOK_SECRET makes the webhook endpoint 503 every
     delivery, which looks exactly like "Stripe never sent anything".
  2. What does the LOCAL account say — status, customer id, plan, trial end.
  3. What does STRIPE say. Asked three ways, because the failure being
     diagnosed is usually that the link between them is missing:
       - by stored customer id (works when the webhook landed)
       - by subscription metadata user_id (works when it did not — this is the
         link Checkout writes at creation time and nothing can lose)
       - by customer email
  4. Where the two disagree, and which step that implicates.

`--fix` applies what Stripe says for that one person, including the trial end
date, and is the same write the webhook would have made.
"""
import argparse
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings              # noqa: E402
from src.auth import users as user_store          # noqa: E402
from src.billing import plans                     # noqa: E402

LIVE = ("active", "trialing", "past_due")


def g(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def as_id(x) -> str:
    if not x:
        return ""
    return x if isinstance(x, str) else str(g(x, "id", "") or "")


def as_list(x) -> list:
    if not x:
        return []
    return x if isinstance(x, list) else list(g(x, "data", []) or [])


def ago(ts) -> str:
    if not ts:
        return "never"
    d = time.time() - float(ts)
    if d < 3600:
        return f"{int(d // 60)}m ago"
    if d < 86400:
        return f"{int(d // 3600)}h ago"
    return f"{int(d // 86400)}d ago"


def when(ts) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))


# ── 1. is the environment what you think it is ───────────────────────────────

def environment_report() -> list[str]:
    problems = []
    print("── environment " + "─" * 62)
    try:
        root = pathlib.Path(__file__).resolve().parent.parent
        rev = subprocess.run(["git", "-C", str(root), "log", "-1",
                              "--format=%h %cr  %s"],
                             capture_output=True, text=True, timeout=10)
        print(f"  deployed commit : {rev.stdout.strip() or '(unknown)'}")
        branch = subprocess.run(["git", "-C", str(root), "rev-parse",
                                 "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, timeout=10)
        print(f"  branch          : {branch.stdout.strip() or '(unknown)'}")
    except Exception as exc:
        print(f"  deployed commit : (could not read git: {exc})")

    # The card-required cutover is only present in newer code. If it is
    # missing, prod is running something older than you think and half of this
    # report's assumptions do not hold.
    import inspect
    from src.billing import stripe_billing
    has_trial = "trial_period_days" in inspect.getsource(
        stripe_billing.create_checkout_url)
    has_selfheal = hasattr(stripe_billing, "subscription_from_checkout_session")
    print(f"  card-up-front trial in checkout : {'yes' if has_trial else 'NO'}")
    print(f"  /billing/success self-heal      : {'yes' if has_selfheal else 'NO'}")
    if not (has_trial and has_selfheal):
        problems.append(
            "PROD IS RUNNING OLDER CODE than the card-required cutover. "
            "Deploy first — several findings below assume the new behaviour.")

    secret = bool(settings.stripe_webhook_secret)
    key = bool(settings.stripe_secret_key)
    print(f"  STRIPE_SECRET_KEY      : {'set' if key else 'MISSING'}")
    print(f"  STRIPE_WEBHOOK_SECRET  : {'set' if secret else 'MISSING'}")
    print(f"  price ids starter/pro  : "
          f"{'set' if settings.stripe_price_id_starter else 'MISSING'}"
          f" / {'set' if settings.stripe_price_id_pro else 'MISSING'}")
    if not key:
        problems.append("STRIPE_SECRET_KEY is unset — nothing can reach Stripe.")
    if not secret:
        problems.append(
            "STRIPE_WEBHOOK_SECRET is unset — /billing/webhook returns 503 to "
            "EVERY delivery, so no subscription event has ever been applied. "
            "This alone explains a paying customer with no access.")
    print()
    return problems


# ── 2/3. local truth vs Stripe truth ─────────────────────────────────────────

def stripe_view(client, user: dict) -> dict:
    """Everything Stripe knows about this person, found three different ways."""
    out = {"by_customer": [], "by_metadata": [], "by_email": [], "error": None}
    uid = user["id"]
    cust = user.get("stripe_customer_id") or ""
    try:
        if cust:
            out["by_customer"] = [
                s for s in as_list(client.subscriptions.list(
                    params={"customer": cust, "status": "all", "limit": 20}))]
        # The link Checkout writes into subscription metadata at creation. It
        # survives a webhook that never fired, which is exactly the case here.
        recent = as_list(client.subscriptions.list(
            params={"status": "all", "limit": 100}))
        out["by_metadata"] = [s for s in recent
                              if str(g(g(s, "metadata"), "user_id", "") or "") == uid]
        email = user.get("email") or ""
        if email:
            custs = as_list(client.customers.list(params={"email": email, "limit": 10}))
            for c in custs:
                out["by_email"] += as_list(client.subscriptions.list(
                    params={"customer": as_id(c), "status": "all", "limit": 20}))
    except Exception as exc:
        out["error"] = str(exc)
    return out


def describe(sub) -> str:
    status = str(g(sub, "status", "") or "?")
    items = as_list(g(sub, "items"))
    price = as_id(g(items[0], "price")) if items else ""
    tier = ("starter" if price == settings.stripe_price_id_starter
            else "pro" if price in (settings.stripe_price_id_pro,
                                    settings.stripe_price_id) else price or "?")
    te = g(sub, "trial_end")
    trial = f", trial ends {when(te)}" if te else ""
    return (f"{as_id(sub)}  status={status}  tier={tier}"
            f"  customer={as_id(g(sub, 'customer'))}{trial}")


def diagnose(user: dict, sv: dict) -> tuple[str, str]:
    """(stage, what to do). The whole point of the script."""
    status = user.get("subscription_status") or "none"
    cust = user.get("stripe_customer_id") or ""
    live = [s for s in (sv["by_customer"] + sv["by_metadata"] + sv["by_email"])
            if str(g(s, "status", "")) in LIVE]
    # De-dupe: the three lookups overlap.
    seen, live_u = set(), []
    for s in live:
        if as_id(s) not in seen:
            seen.add(as_id(s))
            live_u.append(s)

    if sv["error"]:
        return "UNKNOWN", f"Stripe could not be reached: {sv['error']}"

    if user.get("is_admin") or user.get("is_labeler"):
        return "STAFF", "admin/trainer — access does not come from Stripe"

    if not live_u:
        if status in ("active", "trialing") and cust:
            return ("GHOST ACCESS",
                    "the app grants access but Stripe has no live subscription. "
                    "If this is an admin comp that is correct; otherwise their "
                    "subscription ended and we did not notice.")
        if cust:
            return ("CHECKOUT ABANDONED",
                    "they reached Stripe (a customer exists) but never completed "
                    "a subscription. Card declined, or they closed the tab.")
        if user.get("checkout_started_at"):
            return ("LEFT AT THE CARD FORM",
                    "they clicked through to Stripe Checkout "
                    f"({ago(user['checkout_started_at'])}) and never entered a card.")
        return ("NEVER STARTED CHECKOUT",
                "signed up and never clicked through to pay. Nothing is broken.")

    # There IS a live subscription. So why is the app not honouring it?
    best = live_u[0]
    raw = str(g(best, "status", ""))
    if not cust:
        return ("PAID BUT NOT LINKED",
                "Stripe has a live subscription and the account has NO customer "
                "id. The webhook never landed. THIS PERSON HAS PAID AND HAS NO "
                "ACCESS — fix with --fix, or scripts/stripe_relink.py --apply "
                "for everyone at once. Then check the webhook endpoint has "
                "customer.subscription.created enabled.")
    if raw == "trialing" and status != "trialing":
        return ("TRIAL NOT RECORDED",
                f"Stripe says trialing, we say {status!r}. The trial event was "
                "missed or mis-applied.")
    if raw in ("active", "trialing") and status not in ("active", "trialing"):
        return ("PAID BUT LOCKED OUT",
                f"Stripe says {raw!r}, we say {status!r}. THIS PERSON HAS PAID "
                "AND HAS NO ACCESS — fix with --fix.")
    if raw == "past_due":
        return ("CARD FAILING",
                "Stripe is retrying their card. They keep access during dunning.")
    return ("OK", "app and Stripe agree")


def apply_fix(user: dict, sv: dict) -> None:
    live = [s for s in (sv["by_customer"] + sv["by_metadata"] + sv["by_email"])
            if str(g(s, "status", "")) in LIVE]
    if not live:
        print("  --fix: nothing live at Stripe to apply.")
        return
    sub = live[0]
    raw = str(g(sub, "status", ""))
    cust = as_id(g(sub, "customer"))
    status = "trialing" if raw == "trialing" else (
        "active" if raw == "active" else raw)
    try:
        trial_end = int(g(sub, "trial_end") or 0)
    except (TypeError, ValueError):
        trial_end = 0
    items = as_list(g(sub, "items"))
    price = as_id(g(items[0], "price")) if items else ""
    tier = ("starter" if price == settings.stripe_price_id_starter
            else "pro" if price in (settings.stripe_price_id_pro,
                                    settings.stripe_price_id) else None)

    # Exactly the write the webhook would have made, trial end included — so a
    # repaired account is indistinguishable from one that never broke.
    user_store.update_subscription(user["id"], cust, status,
                                   trial_end if status == "trialing" else 0)
    if tier:
        user_store.set_plan(user["id"], tier)
    after = user_store.get_by_id(user["id"])
    print(f"  --fix: applied  status={status}  customer={cust}"
          f"  tier={tier or '(unchanged)'}"
          f"  trial_ends={when(trial_end) if trial_end else '-'}")
    print(f"  --fix: effective plan is now {plans.get_plan(after)}")
    print("  NOTE: their open tab will not know until it reconnects. Tell them "
          "to reload, or wait for the next deploy/restart.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("who", nargs="?", default="",
                    help="username or twitch login (substring, case-insensitive)")
    ap.add_argument("--fix", action="store_true",
                    help="apply what Stripe says for the matched account(s)")
    args = ap.parse_args()

    problems = environment_report()

    users = user_store.get_all()
    if args.who:
        needle = args.who.lower()
        users = [u for u in users
                 if needle in (u.get("username") or "").lower()
                 or needle in (u.get("twitch_login") or "").lower()
                 or needle in (u.get("email") or "").lower()
                 or needle == u.get("id")]
        if not users:
            sys.exit(f"no account matches {args.who!r}")

    client = None
    if settings.stripe_secret_key:
        import stripe
        _raw = stripe.StripeClient(settings.stripe_secret_key)
        client = getattr(_raw, "v1", _raw)

    print("── accounts " + "─" * 65)
    for u in users:
        sv = ({"by_customer": [], "by_metadata": [], "by_email": [],
               "error": "STRIPE_SECRET_KEY unset"} if client is None
              else stripe_view(client, u))
        stage, advice = diagnose(u, sv)
        flag = "  <<<" if stage in ("PAID BUT NOT LINKED", "PAID BUT LOCKED OUT",
                                    "TRIAL NOT RECORDED") else ""
        print(f"\n  {u.get('username', '?')}  ({u.get('twitch_login') or 'no twitch'})")
        print(f"      stage      : {stage}{flag}")
        print(f"      what it is : {advice}")
        print(f"      local      : status={u.get('subscription_status') or 'none'}"
              f"  plan={u.get('plan') or '-'}"
              f"  customer={u.get('stripe_customer_id') or 'NONE'}"
              f"  trial_ends={when(u.get('trial_ends_at'))}")
        print(f"      flags      : grandfathered={bool(u.get('grandfathered'))}"
              f"  pre_card_cutover={bool(u.get('pre_card_cutover'))}"
              f"  joined={when(u.get('created_at'))}")
        print(f"      get_plan   : {plans.get_plan(u)}")
        # What Checkout would actually offer them RIGHT NOW. The single most
        # useful line when somebody "stopped at the paywall": 0 here means they
        # were shown a bill instead of the free week the site promised.
        try:
            from src.dashboard.api import _checkout_trial_days
            days = _checkout_trial_days(u)
            from src.auth import trial_ledger as _tl
            burned = bool(u.get("twitch_id")) and _tl.has_used_trial(
                "twitch", str(u["twitch_id"]))
            note = ""
            if days == 0:
                if u.get("pre_card_cutover"):
                    note = "  (pre-cutover account — bills immediately by design)"
                elif burned:
                    note = "  <<< LEDGER SAYS THEY USED A WEEK"
            print(f"      checkout   : {days} free day(s) offered{note}")
            if days == 0 and burned and not u.get("pre_card_cutover") \
                    and not u.get("stripe_customer_id") and not u.get("trial_ends_at"):
                print("                   this is the old-backfill bug: a deploy")
                print("                   burned their week. Restart to repair, or")
                print("                   run prune_wrongly_burned_trials().")
        except Exception as exc:
            print(f"      checkout   : could not evaluate ({exc})")
        allsubs = {as_id(s): s for s in
                   sv["by_customer"] + sv["by_metadata"] + sv["by_email"]}
        if sv["error"]:
            print(f"      stripe     : ERROR {sv['error']}")
        elif not allsubs:
            print("      stripe     : no subscription found (by customer, "
                  "metadata or email)")
        else:
            for i, s in enumerate(allsubs.values()):
                print(f"      stripe     : {describe(s)}" if i == 0
                      else f"                   {describe(s)}")
        if args.fix:
            apply_fix(u, sv)

    if problems:
        print("\n── environment problems " + "─" * 53)
        for p in problems:
            print(f"  * {p}")
    print()


if __name__ == "__main__":
    main()
