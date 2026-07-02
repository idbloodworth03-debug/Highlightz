"""
State backup: archive every piece of irreplaceable JSON state so losing the
droplet's disk is an inconvenience, not a business-ending event.

What's backed up (everything under local_storage_path matching state suffixes):
  users.json (+ .bak)     — accounts, Stripe customer links, encrypted tokens
  clips.json / streams.json / trial_claims.json / clip_counter.json
  training_log.jsonl      — the labeled dataset for future weight learning
  profiles/**             — per-channel learned baselines and thresholds
Media (*.mp4 etc.) is deliberately excluded — clips live on Twitch, not here.

Behavior:
  1. Always writes <storage_parent>/backups/highlightz-state-<UTC>.tar.gz and
     rotates, keeping the newest `backup_keep_local` archives.
  2. If BACKUP_S3_BUCKET is set, also uploads to S3-compatible storage
     (BACKUP_S3_ENDPOINT for DigitalOcean Spaces; credentials from
     AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY). Upload failure never deletes
     the local archive.

Usage (on the server, from /opt/highlightz):
  venv/bin/python -m src.maintenance.backup
Cron (nightly at 04:10):
  10 4 * * * cd /opt/highlightz && venv/bin/python -m src.maintenance.backup >> backups/backup.log 2>&1
"""

import sys
import tarfile
import time
from pathlib import Path

from config.settings import settings

_STATE_SUFFIXES = {".json", ".jsonl", ".bak"}
_ARCHIVE_PREFIX = "highlightz-state-"


def _state_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix in _STATE_SUFFIXES
    )


def make_backup(storage_root: Path | None = None,
                backups_dir: Path | None = None,
                keep: int | None = None) -> Path | None:
    """Create one archive + rotate. Returns the archive path, or None if there
    was nothing to back up."""
    root = storage_root or Path(settings.local_storage_path)
    dest = backups_dir or (root.parent / "backups")
    keep = keep if keep is not None else settings.backup_keep_local

    files = _state_files(root)
    if not files:
        print(f"nothing to back up under {root}")
        return None

    dest.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    archive = dest / f"{_ARCHIVE_PREFIX}{stamp}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=str(f.relative_to(root.parent)))
    size_kb = archive.stat().st_size / 1024
    print(f"wrote {archive}  ({len(files)} files, {size_kb:.0f} KB)")

    # Rotate: newest `keep` stay (names embed UTC timestamps, so sort by name)
    old = sorted(dest.glob(f"{_ARCHIVE_PREFIX}*.tar.gz"))[:-keep] if keep > 0 else []
    for stale in old:
        stale.unlink(missing_ok=True)
        print(f"rotated out {stale.name}")
    return archive


def upload_offsite(archive: Path) -> bool:
    """Upload to S3-compatible storage if configured. Returns True on success."""
    if not settings.backup_s3_bucket:
        print("off-site upload skipped: BACKUP_S3_BUCKET not set "
              "(set it + BACKUP_S3_ENDPOINT + AWS keys in .env to enable)")
        return False
    try:
        import boto3
        kwargs = {"region_name": settings.aws_region}
        if settings.backup_s3_endpoint:
            kwargs["endpoint_url"] = settings.backup_s3_endpoint
        if settings.aws_access_key_id:
            kwargs["aws_access_key_id"] = settings.aws_access_key_id
            kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        s3 = boto3.client("s3", **kwargs)
        key = f"backups/{archive.name}"
        s3.upload_file(str(archive), settings.backup_s3_bucket, key)
        print(f"uploaded off-site: s3://{settings.backup_s3_bucket}/{key}")
        return True
    except Exception as exc:
        # Never let a failed upload look like a failed backup — the local
        # archive is already safely written.
        print(f"OFF-SITE UPLOAD FAILED (local archive kept): {exc}")
        return False


def main() -> None:
    archive = make_backup()
    if archive is None:
        sys.exit(1)
    upload_offsite(archive)


if __name__ == "__main__":
    main()
