"""
One-shot migration: re-encrypt stored OAuth tokens from the legacy key
(derived from DASHBOARD_SECRET_KEY) to the dedicated TOKEN_ENCRYPTION_KEY.

Why this exists: when TOKEN_ENCRYPTION_KEY is unset, src/auth/users.py falls
back to deriving the Fernet key from the session secret. Simply setting
TOKEN_ENCRYPTION_KEY later would change the key and make every stored token
undecryptable — every user's clipping would silently break until they signed
in again. This script decrypts with the old key and re-encrypts with the new
one so nobody notices anything.

Run it ON THE SERVER, in this exact order:

  1. Add TOKEN_ENCRYPTION_KEY to /opt/highlightz/.env:
       python3 -c "import secrets; print(secrets.token_hex(32))"
  2. systemctl stop highlightz          # so the old-key process can't write
  3. venv/bin/python -m src.maintenance.rotate_token_key           # dry run
     venv/bin/python -m src.maintenance.rotate_token_key --apply   # for real
  4. systemctl start highlightz

The dry run changes nothing; --apply snapshots users.json to
users.json.pre-rotation first, then writes atomically. Tokens that fail to
decrypt with the old key (already broken, e.g. from an earlier key change)
are left untouched — those users re-auth on next login, same as before.
"""

import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

from config.settings import settings

_TOKEN_FIELDS = ("tw_access", "tw_refresh", "kick_access", "kick_refresh")


def _key_from(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def rotate(users_file: Path, old_secret: str, new_secret: str, apply: bool) -> tuple[int, int, int]:
    """Re-encrypt token fields. Returns (rotated, skipped_undecryptable, untouched_empty)."""
    old_f = _key_from(old_secret)
    new_f = _key_from(new_secret)
    users = json.loads(users_file.read_text(encoding="utf-8"))

    rotated = skipped = empty = 0
    for u in users:
        for field in _TOKEN_FIELDS:
            value = u.get(field) or ""
            if not value:
                empty += 1
                continue
            try:
                plaintext = old_f.decrypt(value.encode()).decode()
            except Exception:
                # Not decryptable with the legacy key — either already broken
                # or (if re-running after a partial apply) already on the new
                # key. Verify the latter so a re-run is a safe no-op.
                try:
                    new_f.decrypt(value.encode())
                    print(f"  already-new  {u.get('username','?')}.{field}")
                except Exception:
                    skipped += 1
                    print(f"  UNDECRYPTABLE (left as-is)  {u.get('username','?')}.{field}")
                continue
            u[field] = new_f.encrypt(plaintext.encode()).decode()
            rotated += 1
            print(f"  rotated      {u.get('username','?')}.{field}")

    if apply and rotated:
        snapshot = users_file.with_suffix(".json.pre-rotation")
        shutil.copyfile(users_file, snapshot)
        os.chmod(snapshot, 0o600)
        fd, tmp = tempfile.mkstemp(dir=users_file.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2)
        os.replace(tmp, users_file)
        os.chmod(users_file, 0o600)
        print(f"\nwrote {users_file} (snapshot kept at {snapshot})")
    return rotated, skipped, empty


def main() -> int:
    apply = "--apply" in sys.argv

    if not settings.token_encryption_key:
        print("ERROR: TOKEN_ENCRYPTION_KEY is not set in .env — set it first (step 1).")
        return 1
    if settings.dashboard_secret_key == settings.token_encryption_key:
        print("ERROR: TOKEN_ENCRYPTION_KEY must differ from DASHBOARD_SECRET_KEY.")
        return 1

    users_file = Path(settings.local_storage_path) / "users.json"
    if not users_file.exists():
        print(f"ERROR: {users_file} not found.")
        return 1

    print(f"{'APPLY' if apply else 'DRY RUN (pass --apply to write)'} — {users_file}\n")
    rotated, skipped, empty = rotate(
        users_file, settings.dashboard_secret_key, settings.token_encryption_key, apply)
    print(f"\n{rotated} field(s) rotated, {skipped} undecryptable, {empty} empty.")
    if not apply and rotated:
        print("Nothing written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
