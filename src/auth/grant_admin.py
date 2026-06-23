"""
CLI helper: grant admin to a user by username or user_id.

Usage (run from repo root):
    python -m src.auth.grant_admin <username_or_user_id>

The script loads the existing users DB, finds the matching user, sets
is_admin=True and subscription_status="active", then saves.
"""
import sys

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m src.auth.grant_admin <username_or_user_id>")
        sys.exit(1)

    target = sys.argv[1].strip()

    # Load env so the DB path resolves correctly
    from config.settings import settings  # noqa: F401
    from src.auth import users as user_store

    all_users = user_store._load()
    match = next(
        (u for u in all_users
         if u.get("username", "").lower() == target.lower()
         or u.get("id") == target),
        None,
    )
    if not match:
        print(f"No user found matching '{target}'.")
        print("Existing users:")
        for u in all_users:
            print(f"  id={u['id']}  username={u.get('username')}  is_admin={u.get('is_admin')}")
        sys.exit(1)

    was_admin = match.get("is_admin", False)
    match["is_admin"] = True
    match["subscription_status"] = "active"

    # Write directly since _save is internal but accessible
    user_store._save(all_users)

    status = "already admin" if was_admin else "granted admin"
    print(f"✓ User '{match.get('username')}' (id={match['id']}) — {status}.")
    print("Restart the server and log out / log back in to pick up the change.")


if __name__ == "__main__":
    main()
