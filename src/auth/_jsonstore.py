"""Atomic, private, ownership-preserving JSON writes.

Extracted from users._save rather than copied, because the subtle part of it
took the site down once already: a root shell running an admin script replaces
the file, the replacement is owned by root at mode 0600, and the service — which
does not run as root — cannot read its own data on the next start.

Any file holding account state should go through here. Two copies of this logic
is how the second outage happens.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path


def atomic_write_json(path: Path, data, backup_path: Path | None = None) -> None:
    """Replace `path` with `data` as JSON, atomically and privately.

    Writes a temp file in the same directory and renames it over the target, so
    a crash mid-write leaves the previous file intact rather than a truncated
    one. Mode is forced to 0600 — these files hold OAuth tokens and account
    identifiers — and the previous owner is restored afterwards.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Captured BEFORE the replace: the temp file belongs to whoever is running
    # us, which is not always the service.
    prev_owner = None
    try:
        if path.exists():
            st = os.stat(path)
            prev_owner = (st.st_uid, st.st_gid)
    except OSError:
        pass

    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        if backup_path is not None and path.exists():
            try:
                fd2, tmp_bak = tempfile.mkstemp(dir=path.parent, suffix=".bak.tmp")
                os.close(fd2)
                shutil.copyfile(path, tmp_bak)
                os.chmod(tmp_bak, 0o600)
                os.replace(tmp_bak, backup_path)
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    # Only root can chown, which is exactly the case that needs it — a non-root
    # writer already owns the file and this would be a no-op.
    if prev_owner and os.geteuid() == 0:
        for target in (path, backup_path):
            if target is None:
                continue
            try:
                if target.exists() and (os.stat(target).st_uid,
                                        os.stat(target).st_gid) != prev_owner:
                    os.chown(target, *prev_owner)
            except OSError:
                pass


def read_json(path: Path, default):
    """Load JSON, returning `default` for a missing or unreadable file."""
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
