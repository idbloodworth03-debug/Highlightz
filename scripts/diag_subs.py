"""Why does the admin panel say nobody is subscribed?

Prints only billing-relevant fields — no tokens, no emails, no secrets — and
shows which branch of the counter each account falls into.
"""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.auth import users as user_store
from src.billing import plans

users = user_store.get_all()
print(f"users.json: {user_store._USERS_FILE}")
print(f"{len(users)} account(s)\n")

hdr = f"{'username':<18}{'status':<12}{'plan':<9}{'source':<9}{'cust':<6}{'gf':<4}{'admin':<6}{'->get_plan':<11}{'counted as'}"
print(hdr)
print("-" * len(hdr))

paying = trialing = comped = 0
for u in users:
    status = u.get("subscription_status") or "(none)"
    is_admin = bool(u.get("is_admin"))
    cust = "yes" if u.get("stripe_customer_id") else "NO"
    src = u.get("plan_source") or "-"
    eff = plans.get_plan(u)

    if is_admin:
        bucket = "EXCLUDED (admin)"
    elif status == "trialing":
        bucket = "trialing"; trialing += 1
    elif status == "active":
        if not u.get("stripe_customer_id") or src == "granted":
            bucket = "comped (no stripe customer)"; comped += 1
        else:
            bucket = "PAYING"; paying += 1
    else:
        bucket = f"not counted (status={status})"

    print(f"{(u.get('username') or '?')[:17]:<18}{status:<12}"
          f"{str(u.get('plan') or '-'):<9}{src:<9}{cust:<6}"
          f"{('yes' if u.get('grandfathered') else '-'):<4}"
          f"{('yes' if is_admin else '-'):<6}{eff:<11}{bucket}")

print()
print(f"panel would show:  Paying={paying}  Trialing={trialing}  Comped={comped}")
