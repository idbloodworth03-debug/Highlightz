"""Strip stored Kick OAuth credentials from every user record.

    /opt/highlightz/venv/bin/python scripts/purge_kick_credentials.py          # report only
    /opt/highlightz/venv/bin/python scripts/purge_kick_credentials.py --apply  # write

READ-ONLY WITHOUT --apply. It reports; it changes nothing.

WHY THIS EXISTS. The Kick "Connect" button ran a full OAuth flow and wrote
`kick_access`, `kick_refresh` and `kick_expires_at` onto the user record —
while Kick monitoring itself is switched off, and while both the Terms and the
Privacy Policy stated that no Kick credentials are requested or stored. The
flow has been removed, so nothing writes those fields any more. That makes the
sentence true for new records; this makes it true for the ones already on disk.

Deleting code does not delete data. Without this step the claim stays false for
every user who ever pressed Connect, which is the whole reason it was worth
fixing.

WHAT IT TOUCHES. Exactly three keys, and only where present. `kick_id`,
`kick_slug` and `kick_username` are left alone: they are identity, not
credentials, nothing displays them any more, and removing them is a separate
decision from honouring the credential claim.

SAFE TO RUN TWICE. The second run finds nothing and writes nothing.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.auth import users as user_store  # noqa: E402

# The exact fields the removed flow wrote. Not a prefix match: `kick_id` and
# `kick_slug` also start with "kick_" and must survive.
_CREDENTIAL_FIELDS = ("kick_access", "kick_refresh", "kick_expires_at")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually write the change (default is a dry run)")
    args = ap.parse_args()

    users = user_store._load()
    if not users:
        print("No user records found — nothing to do.")
        return 0

    affected = []
    for u in users:
        present = [f for f in _CREDENTIAL_FIELDS if f in u]
        if present:
            affected.append((u.get("username") or u.get("id", "?"), present))

    print(f"{len(users)} user record(s) scanned.")
    if not affected:
        print("No stored Kick credentials. Nothing to purge.")
        return 0

    print(f"{len(affected)} record(s) still carry Kick credentials:\n")
    for name, present in affected:
        print(f"  {name:<28} {', '.join(present)}")

    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply to purge.")
        return 0

    removed = 0
    for u in users:
        for f in _CREDENTIAL_FIELDS:
            if f in u:
                del u[f]
                removed += 1
    user_store._save(users)
    print(f"\nPurged {removed} field(s) across {len(affected)} record(s).")
    print("Re-run without --apply to confirm the store is clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
