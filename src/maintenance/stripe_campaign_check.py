"""
Is Stripe ready to take payments for streamer campaigns?

    venv/bin/python -m src.maintenance.stripe_campaign_check
    venv/bin/python -m src.maintenance.stripe_campaign_check --add-events

Owner, 2026-10-23: "i need to set it up with stripe now". Read-only unless
--add-events is given. It never prints a key.

What a campaign payment needs (src/campaigns/requests.py, stripe_billing):

  keys        STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET: the same pair the
              subscriptions use, nothing new. Says whether the key is live.
  account     able to take card payments (charges_enabled) and to pay out.
  webhook     the endpoint at https://highlightz.app/billing/webhook,
              enabled, and sent checkout.session.completed and
              checkout.session.async_payment_succeeded. Without them a
              payment still publishes when the streamer comes back from
              Stripe, but not when they close the tab first.
  switches    CAMPAIGN_REQUESTS_ENABLED, CAMPAIGNS_ENABLED, the fee, and the
              owner's emails (a new request, a payment).

--add-events adds the two checkout events to that endpoint and keeps every
event it already has. Same endpoint, same URL, same signing secret, so
STRIPE_WEBHOOK_SECRET stays right. NEVER make a second endpoint for
campaigns: it would sign with its own secret, and this server checks one.
"""

from __future__ import annotations

import sys

from config.settings import settings

WEBHOOK_URL = "https://highlightz.app/billing/webhook"
NEEDED = ("checkout.session.completed", "checkout.session.async_payment_succeeded")
# The events the subscriptions already depend on (_process_stripe_event).
SUBSCRIPTION = ("customer.subscription.created", "customer.subscription.updated",
                "customer.subscription.deleted")


def _get(o, k, default=None):
    """A field of a Stripe object or a dict: in stripe-python 15 they are
    not dicts (see handle_webhook_event)."""
    if isinstance(o, dict):
        return o.get(k, default)
    v = getattr(o, k, default)
    return default if v is None else v


def _same_url(url: str) -> bool:
    u = str(url or "").strip().rstrip("/").lower()
    return u in (WEBHOOK_URL, WEBHOOK_URL.replace("https://", "https://www."))


def key_mode(key: str) -> str:
    """"live", "test", "restricted live/test", or "" for no key. Read from
    the prefix only; the key itself is never shown."""
    key = key or ""
    for prefix, mode in (("sk_live_", "live"), ("sk_test_", "test"),
                         ("rk_live_", "restricted live"), ("rk_test_", "restricted test")):
        if key.startswith(prefix):
            return mode
    return "unknown" if key else ""


def missing_events(endpoint) -> list[str]:
    events = list(_get(endpoint, "enabled_events", []) or [])
    if "*" in events:
        return []
    return [e for e in NEEDED if e not in events]


def check(client, add_events: bool = False, out=print) -> int:
    """The report. 0 when campaign payments are ready, 1 when something is
    missing. `client` is a stripe.StripeClient (or a stand-in in tests)."""
    problems: list[str] = []
    out("== keys ==")
    mode = key_mode(settings.stripe_secret_key)
    if not mode:
        out("   MISSING  STRIPE_SECRET_KEY is not set in .env")
        problems.append("set STRIPE_SECRET_KEY")
    else:
        out(f"   ok       STRIPE_SECRET_KEY is set ({mode} mode)")
        if "test" in mode:
            out("   NOTE     test mode: real cards are not charged. Use a live key to take real payments.")
        if mode.startswith("restricted"):
            out("   NOTE     a restricted key needs write access to Checkout Sessions and read access to "
                "webhook endpoints and the account")
    if (settings.stripe_webhook_secret or "").startswith("whsec_"):
        out("   ok       STRIPE_WEBHOOK_SECRET is set")
    else:
        out("   MISSING  STRIPE_WEBHOOK_SECRET is not set (or does not start whsec_)")
        problems.append("set STRIPE_WEBHOOK_SECRET to the endpoint's signing secret")
    if not mode:
        out("\nWithout a secret key nothing else can be checked.")
        return _verdict(problems, out)

    out("\n== account ==")
    try:
        acct = client.v1.accounts.retrieve_current()
        charges, payouts = bool(_get(acct, "charges_enabled")), bool(_get(acct, "payouts_enabled"))
        name = _get(_get(acct, "business_profile", {}) or {}, "name") or _get(acct, "email") or "?"
        out(f"   {'ok      ' if charges else 'MISSING '} card payments {'on' if charges else 'OFF'} "
            f"({name}, {str(_get(acct, 'default_currency', '?')).upper()})")
        out(f"   {'ok      ' if payouts else 'NOTE    '} payouts to your bank {'on' if payouts else 'off'}")
        if not charges:
            problems.append("finish the Stripe account so it can take card payments")
        desc = _get(_get(_get(acct, "settings", {}) or {}, "payments", {}) or {}, "statement_descriptor")
        if desc:
            out(f"   ok       card statements read \"{desc}\"")
    except Exception as exc:
        out(f"   NOTE     could not read the account ({type(exc).__name__}); "
            "a restricted key may not be allowed to")

    out("\n== webhook ==")
    try:
        endpoints = list(_get(client.v1.webhook_endpoints.list(params={"limit": 100}), "data", []) or [])
    except Exception as exc:
        out(f"   MISSING  could not list webhook endpoints ({type(exc).__name__}: {str(exc)[:120]})")
        problems.append("let the key read webhook endpoints, or check the endpoint by hand")
        endpoints = None
    if endpoints is not None:
        ours = [e for e in endpoints if _same_url(_get(e, "url"))]
        for e in endpoints:
            if not _same_url(_get(e, "url")):
                out(f"   other    {_get(e, 'url')} ({_get(e, 'status')})")
        if not ours:
            out(f"   MISSING  no endpoint points at {WEBHOOK_URL}")
            problems.append(f"add {WEBHOOK_URL} as an endpoint (subscriptions need it too)")
        for e in ours:
            eid, status = _get(e, "id"), _get(e, "status")
            events = list(_get(e, "enabled_events", []) or [])
            out(f"   found    {eid} {_get(e, 'url')} ({status}, {'all events' if '*' in events else str(len(events)) + ' events'})")
            if status != "enabled":
                out("   MISSING  it is disabled: Stripe sends it nothing")
                problems.append(f"enable webhook endpoint {eid}")
            sub_missing = [s for s in SUBSCRIPTION if "*" not in events and s not in events]
            if sub_missing:
                out("   NOTE     subscriptions also need: " + ", ".join(sub_missing))
            gap = missing_events(e)
            if not gap:
                out("   ok       sends " + " and ".join(NEEDED))
                continue
            if not add_events:
                out("   MISSING  not sent: " + ", ".join(gap))
                problems.append("add " + ", ".join(gap) + " to the endpoint (run again with --add-events)")
                continue
            try:
                client.v1.webhook_endpoints.update(eid, params={"enabled_events": sorted(set(events) | set(gap))})
                out("   ADDED    " + ", ".join(gap) + " (every event it had is kept; same signing secret)")
            except Exception as exc:
                out(f"   MISSING  could not add them ({type(exc).__name__}: {str(exc)[:120]})")
                problems.append("add " + ", ".join(gap) + " to the endpoint in the Stripe dashboard")

    out("\n== switches ==")
    from src import alerts
    from src.campaigns import requests as creq
    p = creq.pricing()
    fee = ([f"${p['flat_fee']:,.2f} per campaign"] if p["flat_fee"] else []) + \
          ([f"{p['fee_pct']:g}% of the budget"] if p["fee_pct"] else [])
    out(f"   {'on ' if settings.campaign_requests_enabled else 'off'}      CAMPAIGN_REQUESTS_ENABLED"
        + ("" if settings.campaign_requests_enabled else " (only admins can ask and pay until it is on)"))
    out(f"   {'on ' if settings.campaigns_enabled else 'off'}      CAMPAIGNS_ENABLED"
        + ("" if settings.campaigns_enabled else " (clippers cannot see a paid campaign until it is on)"))
    out(f"   fee      {' plus '.join(fee) or 'none'}; budgets ${p['min_budget']:,.2f} to ${p['max_budget']:,.2f}")
    out(f"   {'ok      ' if alerts.configured() else 'NOTE    '} emails to you on a new request and a payment"
        + ("" if alerts.configured() else ": not configured (RESEND_API_KEY + ALERT_EMAIL_TO)"))
    if not settings.campaigns_enabled and settings.campaign_requests_enabled:
        problems.append("turn on CAMPAIGNS_ENABLED, or paid campaigns reach no clipper")
    return _verdict(problems, out)


def _verdict(problems: list[str], out) -> int:
    if not problems:
        out("\nReady: campaign payments can be taken.")
        return 0
    out("\nTo do:")
    for i, p in enumerate(problems, 1):
        out(f"   {i}. {p}")
    return 1


def main() -> int:
    args = sys.argv[1:]
    if any(a not in ("--add-events",) for a in args):
        print(__doc__.strip().splitlines()[0])
        print("\nusage: python -m src.maintenance.stripe_campaign_check [--add-events]")
        return 2
    if not settings.stripe_secret_key:
        return check(None)
    import stripe
    return check(stripe.StripeClient(settings.stripe_secret_key), add_events="--add-events" in args)


if __name__ == "__main__":
    raise SystemExit(main())
