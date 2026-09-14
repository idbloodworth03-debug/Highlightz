"""Prove the newest backup could actually be restored. Read-only.

WHY THIS EXISTS. There was no restore path in this repo at all — `backup.py`
writes archives and uploads them, and nothing ever reads one back. Five nightly
tarballs that have never been opened are a hypothesis, not a backup, and the
moment you find out otherwise is the moment you needed it.

It opens the newest archive IN MEMORY and answers the questions that decide
whether a restore would work:

  * Is every irreplaceable file in there, and does it parse?
  * How many accounts and live subscriptions would come back?
  * CAN THE STORED TWITCH TOKENS STILL BE DECRYPTED? This is the one that
    surprises people. `users.json` holds tw_access/tw_refresh encrypted with
    TOKEN_ENCRYPTION_KEY — or, when that is unset, with DASHBOARD_SECRET_KEY.
    Neither lives inside the archive: both are in .env, which is not under
    local_storage_path and is not a .json. Restore onto a fresh droplet with a
    freshly generated secret and every token in the file is undecryptable
    noise, so all monitoring stops until each user signs in again.

    The same trap fires without a disaster: if TOKEN_ENCRYPTION_KEY was never
    set, rotating DASHBOARD_SECRET_KEY silently breaks every stored token.

    Checking is the only way to know, so this decrypts a sample and says.

    python -m src.maintenance.verify_backup
    python -m src.maintenance.verify_backup --archive=backups/highlightz-state-....tar.gz

NOTHING IS WRITTEN and nothing is extracted to disk. Safe to run on production
while it is serving.
"""

import json
import sys
import tarfile
import time
from pathlib import Path

from config.settings import settings

# Files whose loss cannot be recovered from anywhere else. Absence of any of
# these in an archive means the archive does not do its job.
_CRITICAL = {
    "users.json": "accounts, Stripe customer links, encrypted Twitch tokens",
    "clips.json": "the clip library and review queue",
    "streams.json": "which channels are monitored, and for whom",
}
_WORTH_HAVING = {
    "clip_counter.json": "the lifetime counter the landing page shows",
    "trial_claims.json": "who has already used a trial",
    "training_log.jsonl": "the labelled dataset for weight learning",
}


def _newest(backups: Path) -> Path | None:
    try:
        archives = sorted(backups.glob("highlightz-state-*.tar.gz"))
    except OSError:
        return None
    return archives[-1] if archives else None


def _read_member(tar: tarfile.TarFile, suffix: str):
    """Pull one file out of the archive without touching disk."""
    for m in tar.getmembers():
        if m.isfile() and m.name.endswith(suffix):
            f = tar.extractfile(m)
            return m.name, (f.read() if f else b"")
    return None, None


def main(argv) -> int:
    path = None
    for a in argv:
        if a.startswith("--archive="):
            path = Path(a.split("=", 1)[1])
    if path is None:
        path = _newest(Path(settings.local_storage_path).parent / "backups")
    if path is None or not path.is_file():
        print("No archive found. Is the nightly cron running?")
        print("  ls -la backups/ && crontab -l | grep backup")
        return 1

    age_h = (time.time() - path.stat().st_mtime) / 3600
    print("=" * 72)
    print(f"ARCHIVE  {path.name}")
    print(f"         {path.stat().st_size / 1024 / 1024:.1f} MB, written {age_h:.1f}h ago")
    print("=" * 72)
    if age_h > 36:
        print("  >> STALE. The nightly backup has not run in over a day.")

    problems = 0
    try:
        with tarfile.open(path, "r:gz") as tar:
            names = [m.name for m in tar.getmembers() if m.isfile()]
            print(f"\n{len(names)} file(s) inside.\n")

            print("IRREPLACEABLE:")
            for want, why in _CRITICAL.items():
                hit = [n for n in names if n.endswith("/" + want) or n == want]
                if hit:
                    print(f"  OK       {want:<22} {why}")
                else:
                    problems += 1
                    print(f"  MISSING  {want:<22} {why}   <<<")

            print("\nWORTH HAVING:")
            for want, why in _WORTH_HAVING.items():
                hit = [n for n in names if n.endswith("/" + want) or n == want]
                print(f"  {'OK     ' if hit else 'absent '} {want:<22} {why}")

            prof = [n for n in names if "/profiles/" in n and n.endswith(".json")]
            print(f"\n  {len(prof)} per-channel profile(s) — learned baselines and thresholds")

            # --- does it parse, and what would come back? --------------------
            name, raw = _read_member(tar, "users.json")
            if raw is None:
                print("\nCannot check accounts: users.json is not in the archive.")
                return 1 if problems else 0
            try:
                users = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"\n  >> users.json IN THE ARCHIVE IS CORRUPT: {exc}")
                return 1
            if not isinstance(users, list):
                print("\n  >> users.json is not a list — wrong shape")
                return 1

            paying = [u for u in users
                      if u.get("subscription_status") in ("active", "trialing")]
            with_tok = [u for u in users if u.get("tw_access")]
            print(f"\nWHAT WOULD COME BACK:")
            print(f"  {len(users)} account(s), {len(paying)} on an active or trialing plan")
            print(f"  {len(with_tok)} with a stored Twitch token")

            # --- THE CHECK THAT MATTERS --------------------------------------
            print(f"\nCAN THE TOKENS STILL BE DECRYPTED WITH THIS BOX'S KEY?")
            if not with_tok:
                print("  no stored tokens to check")
            else:
                from src.auth import users as user_store
                sample = with_tok[:20]
                ok = sum(1 for u in sample
                         if user_store._decrypt(u.get("tw_access", "")))
                if ok == len(sample):
                    print(f"  OK — {ok}/{len(sample)} sampled tokens decrypt cleanly.")
                    print("     A restore ONTO THIS KEY keeps every user signed in.")
                else:
                    problems += 1
                    print(f"  >> {len(sample) - ok}/{len(sample)} sampled tokens DO NOT "
                          f"decrypt with the current key.   <<<")
                    print("     Users would have to reconnect Twitch before their")
                    print("     channels could be monitored again.")

            keyed_by = ("TOKEN_ENCRYPTION_KEY" if settings.token_encryption_key
                        else "DASHBOARD_SECRET_KEY")
            print(f"\n  Tokens are encrypted with {keyed_by}.")
            print("  IT IS NOT IN THIS ARCHIVE — it lives in .env, which is not under")
            print("  local_storage_path. Restoring this file onto a fresh box with a")
            print("  new secret gives you accounts whose tokens are unreadable.")
            if not settings.token_encryption_key:
                print("\n  >> TOKEN_ENCRYPTION_KEY is unset, so the SESSION SECRET is")
                print("     doing double duty. Rotating DASHBOARD_SECRET_KEY would")
                print("     silently break every stored token, with no disaster needed.")
                print("     Set TOKEN_ENCRYPTION_KEY explicitly so the two can move")
                print("     independently.")
            print("\n  Record that key somewhere off this droplet — a password manager,")
            print("  not the archive. Keeping it beside the data means one leaked")
            print("  tarball is also the key to everything in it.")
    except tarfile.TarError as exc:
        print(f"\n  >> THE ARCHIVE WILL NOT OPEN: {exc}")
        return 1

    # --- off-site ---------------------------------------------------------
    print(f"\nOFF-SITE COPY")
    if settings.backup_s3_bucket:
        print(f"  configured: {settings.backup_s3_bucket}"
              + (f" @ {settings.backup_s3_endpoint}" if settings.backup_s3_endpoint else ""))
        print("  (this tool does not reach out to check the upload landed —")
        print("   `grep uploaded backups/backup.log` does)")
    else:
        problems += 1
        print("  >> NONE. Every archive is on the same disk as the data it")
        print("     protects, so one droplet failure takes both.   <<<")
        print("     Set BACKUP_S3_BUCKET + BACKUP_S3_ENDPOINT + AWS keys in .env.")

    print("\n" + "=" * 72)
    print("A CLEAN RUN HERE IS NOT A TESTED RESTORE. It proves the archive opens,")
    print("parses and decrypts. Actually restoring onto a spare box is the only")
    print("thing that proves the whole path, and it is worth doing once.")
    print("=" * 72)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
