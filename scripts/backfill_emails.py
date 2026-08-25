"""Attach an email address to every account we can, and say who is left.

    /opt/highlightz/venv/bin/python scripts/backfill_emails.py           # report
    /opt/highlightz/venv/bin/python scripts/backfill_emails.py --apply   # write

DRY RUN BY DEFAULT. Without --apply it prints what it would do and writes
nothing.

WHAT CAN AND CANNOT BE BACKFILLED, because the answer is not symmetric:

  STRIPE — yes, fully. Every paying customer has a billing email on their
  Stripe customer, and we can read it whenever we like. Anyone who has ever
  subscribed is reachable right now, including people whose email we never
  stored because they paid before set_email existed. Found two ways: by the
  customer id on the account, and by walking subscriptions and matching their
  metadata user_id — the second catches accounts whose customer id was never
  written back because a webhook did not land.

  TWITCH — no, not for anybody already signed up. A token's scopes are fixed
  when it is issued and refreshing returns the same set, so every token stored
  before user:read:email was requested will return no email however many times
  we ask. Those users hand theirs over the next time they sign in and approve
  the new consent screen, and not before.

This script does not assume that second paragraph — it ASKS Twitch what each
stored token is actually allowed to do, and reports the real number. If any
token does carry the scope, the email is fetched and stored like any other.
"""
import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings              # noqa: E402
from src.auth import users as user_store          # noqa: E402


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


# ── Stripe ───────────────────────────────────────────────────────────────────

def stripe_emails() -> dict[str, str]:
    """{user_id: email} for everyone Stripe knows about.

    Walks subscriptions rather than customers so the metadata user_id is in
    hand — that link is written at checkout and survives a webhook that never
    landed, which is exactly the account most likely to be missing an email.
    """
    if not settings.stripe_secret_key:
        print("  STRIPE_SECRET_KEY unset — skipping the Stripe pass entirely.")
        return {}
    import stripe
    _raw = stripe.StripeClient(settings.stripe_secret_key)
    client = getattr(_raw, "v1", _raw)

    by_customer: dict[str, str] = {}
    out: dict[str, str] = {}
    try:
        subs = as_list(client.subscriptions.list(params={"status": "all", "limit": 100}))
    except Exception as exc:
        print(f"  could not list subscriptions: {exc}")
        return {}

    for s in subs:
        uid = str(g(g(s, "metadata"), "user_id", "") or "")
        cust = as_id(g(s, "customer"))
        if not cust:
            continue
        if cust not in by_customer:
            try:
                c = client.customers.retrieve(cust)
                em = (g(c, "email") or "").strip().lower()
            except Exception:
                em = ""
            by_customer[cust] = em
        if uid and by_customer[cust]:
            out.setdefault(uid, by_customer[cust])

    # Second pass: accounts that already hold a customer id we did not reach
    # through subscription metadata (a cancelled customer with no subscription
    # left to walk, for instance).
    for u in user_store.get_all():
        cust = u.get("stripe_customer_id")
        if not cust or u["id"] in out:
            continue
        if cust not in by_customer:
            try:
                c = client.customers.retrieve(cust)
                by_customer[cust] = (g(c, "email") or "").strip().lower()
            except Exception:
                by_customer[cust] = ""
        if by_customer[cust]:
            out[u["id"]] = by_customer[cust]
    return out


# ── Twitch ───────────────────────────────────────────────────────────────────

async def twitch_emails(users: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """({user_id: email}, {user_id: why not}) — asked, never assumed."""
    from src.auth import twitch_oauth
    found: dict[str, str] = {}
    why: dict[str, str] = {}
    for u in users:
        if not u.get("twitch_id"):
            why[u["id"]] = "no Twitch account linked"
            continue
        try:
            token = await user_store.get_valid_twitch_token(u["id"])
        except Exception:
            token = None
        if not token:
            why[u["id"]] = "no usable Twitch token — they must sign in again"
            continue
        scopes = await twitch_oauth.token_scopes(token)
        if scopes is None:
            why[u["id"]] = "Twitch could not be reached (not a verdict)"
            continue
        if "user:read:email" not in scopes:
            why[u["id"]] = "token predates the email scope — needs a re-login"
            continue
        try:
            info = await twitch_oauth.get_user(token)
        except Exception as exc:
            why[u["id"]] = f"Helix lookup failed: {exc}"
            continue
        if info.get("email"):
            found[u["id"]] = info["email"]
        else:
            why[u["id"]] = "scope present but Twitch returned no email"
    return found, why


# ── report ───────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the emails found")
    ap.add_argument("--skip-twitch", action="store_true",
                    help="Stripe only; skips one API call per account")
    args = ap.parse_args()

    users = user_store.get_all()
    print(f"{len(users)} account(s)\n")

    print("── Stripe " + "─" * 66)
    s_mail = stripe_emails()
    print(f"  {len(s_mail)} account(s) have a billing email on Stripe\n")

    t_mail: dict[str, str] = {}
    reasons: dict[str, str] = {}
    if args.skip_twitch:
        print("── Twitch (skipped) " + "─" * 56 + "\n")
    else:
        print("── Twitch " + "─" * 66)
        t_mail, reasons = asyncio.run(twitch_emails(users))
        print(f"  {len(t_mail)} account(s) have a token that can return an email")
        if not t_mail:
            print("  This is the expected result the first time: tokens issued before")
            print("  user:read:email was requested cannot gain it by refreshing.")
        tally: dict[str, int] = {}
        for r in reasons.values():
            tally[r] = tally.get(r, 0) + 1
        for r, n in sorted(tally.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>3}  {r}")
        print()

    print("── what would change " + "─" * 55)
    planned: list[tuple[dict, str, str]] = []
    for u in users:
        have = u.get("email")
        # Stripe first: a billing address beats a Twitch account address, the
        # same precedence set_email enforces.
        new = s_mail.get(u["id"]) or t_mail.get(u["id"])
        src = "stripe" if s_mail.get(u["id"]) else "twitch"
        if not new or have == new:
            continue
        planned.append((u, new, src))
        was = have or "(none)"
        print(f"  {u.get('username','?'):<20} {was:<32} -> {new}  [{src}]")
    if not planned:
        print("  nothing to change — every reachable email is already stored")

    with_email = sum(1 for u in users if u.get("email"))
    after = len({u["id"] for u in users if u.get("email")}
                | {u["id"] for u, _, _ in planned})
    print(f"\n  coverage: {with_email}/{len(users)} now"
          f"  ->  {after}/{len(users)} after")
    left = [u for u in users
            if not u.get("email") and u["id"] not in {p[0]["id"] for p in planned}]
    if left:
        print(f"\n  {len(left)} account(s) would still have no email. For each, the")
        print("  address arrives when they next sign in and approve the new")
        print("  Twitch consent screen — there is no way to fetch it before then:")
        for u in left[:20]:
            print(f"    {u.get('username','?'):<20} "
                  f"{reasons.get(u['id'], 'not checked')}")
        if len(left) > 20:
            print(f"    … and {len(left) - 20} more")

    if not args.apply:
        print(f"\n{len(planned)} account(s) would change. Re-run with --apply to write.")
        return

    for u, email, src in planned:
        user_store.set_email(u["id"], email, source=src)
    print(f"\napplied to {len(planned)} account(s).")


if __name__ == "__main__":
    main()
