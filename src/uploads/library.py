"""
Clip Upload library — the user's own video files, stored on our disk.

This is the ONE place in Highlightz that holds video bytes. Everything else
(live clipping, the VOD scanner) deliberately keeps video on Twitch's CDN and
only ever handles metadata. The reason this exists: TikTok's and Instagram's
publishing APIs take either raw bytes or a URL on a domain you have verified
you own. Neither accepts a twitch.tv link, so posting a clip anywhere requires
possessing the file. Editing needs the same thing.

The source is the USER'S OWN UPLOAD, not a scrape. Broadcasters can already
download their own clips from the Twitch Creator Dashboard, so this asks them
for a file they are entitled to and we never fetch from Twitch ourselves.
That keeps the "we never record or re-host" promise in the Terms of Service
true for the automated clipping path, which is what it is actually about.

Three things drive the design:

**Disk is a shared fate.** The droplet has one 50 GB disk. If uploads fill it,
clipping stops, billing writes fail, and the dashboard falls over — a full
disk is not an "uploads are broken" event, it is an everything-is-broken
event. So the global cap is enforced before every write and the per-write
check happens DURING the copy, not after: checking afterwards means a 40 GB
upload has already landed by the time you reject it.

**Client input is never a path.** Filenames arrive from a browser and are
attacker-controlled. Stored paths are built from a server-generated UUID and
nothing else; the original name is kept as a display string only. There is no
string concatenation from client data into a path anywhere in this module.

**Content-type is not evidence.** A browser will happily label a .exe as
video/mp4. Files are sniffed for real container magic bytes, and anything that
is not a recognised video container is refused.
"""

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

MB = 1024 * 1024

# Where the files live. Kept under the existing storage root so backups,
# permissions and the disk-usage story stay in one place.
_ROOT = Path(settings.local_storage_path) / "uploads"
_INDEX = Path(settings.local_storage_path) / "uploads.json"

# Containers we accept. MP4/MOV covers everything Twitch hands you and is what
# TikTok and Instagram want anyway; WebM is included because browser-side
# recording produces it. Anything else is refused rather than guessed at.
EXT_FOR_KIND = {"mp4": ".mp4", "mov": ".mov", "webm": ".webm"}


def sniff_container(head: bytes) -> str | None:
    """Identify a video container from its leading bytes, or None.

    Deliberately strict. The uploaded Content-Type header is set by the client
    and proves nothing; this is the only thing that decides what a file is.
    """
    if len(head) < 12:
        return None
    # ISO base media (MP4/MOV/M4V): a box-size field, then the literal 'ftyp'.
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        return "mov" if brand in (b"qt  ",) else "mp4"
    # Matroska / WebM share the EBML magic.
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    return None


class UploadError(Exception):
    """A rejected upload. The message is safe to show the user."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class Upload:
    id: str
    user_id: str
    filename: str          # sanitised original name, for display only
    kind: str              # mp4 | mov | webm
    size: int              # bytes
    created_at: float
    source: str = "upload"  # where it came from; future: "twitch", "recording"

    def public(self) -> dict:
        d = asdict(self)
        d.pop("user_id", None)          # never leak ownership across users
        d["url"] = f"/uploads/{self.id}/file"
        return d


# id -> Upload. Small enough to keep in memory; persisted as one JSON index.
_uploads: dict[str, Upload] = {}
_loaded = False


def _index_path() -> Path:
    return _INDEX


def load() -> None:
    """Read the index from disk. Safe to call repeatedly."""
    global _loaded
    _uploads.clear()
    path = _index_path()
    if path.exists():
        try:
            for row in json.loads(path.read_text() or "[]"):
                try:
                    _uploads[row["id"]] = Upload(**row)
                except (TypeError, KeyError):
                    log.warning("upload_index_row_skipped", row_id=row.get("id"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("upload_index_unreadable", error=str(exc))
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        load()


def _save() -> None:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([asdict(u) for u in _uploads.values()], indent=2))
    os.replace(tmp, path)          # atomic: never leave a half-written index


def _safe_display_name(raw: str) -> str:
    """Reduce a client filename to something safe to store and render.

    This value is NEVER used to build a path — that is what makes it safe.
    It is stripped anyway so a name like `../../etc/passwd` cannot mislead
    someone reading the index later, and so it cannot smuggle markup.
    """
    name = (raw or "").strip().replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ \-]", "", name)[:120].strip(" .")
    return name or "clip"


def path_for(up: Upload) -> Path:
    """On-disk path for an upload.

    Built only from a server-generated UUID and a whitelisted extension, so it
    cannot be steered by client input.
    """
    return _ROOT / up.user_id / f"{up.id}{EXT_FOR_KIND[up.kind]}"


def for_user(user_id: str) -> list[Upload]:
    _ensure_loaded()
    return sorted(
        (u for u in _uploads.values() if u.user_id == user_id),
        key=lambda u: u.created_at, reverse=True,
    )


def get(upload_id: str, user_id: str) -> Upload | None:
    """Fetch one upload, scoped to its owner.

    Ownership is checked HERE rather than at the call site so no endpoint can
    forget it. A miss and a wrong-owner hit are indistinguishable to the
    caller, so this cannot be used to probe whether an id exists.
    """
    _ensure_loaded()
    up = _uploads.get(upload_id)
    return up if up and up.user_id == user_id else None


def user_bytes(user_id: str) -> int:
    return sum(u.size for u in for_user(user_id))


def total_bytes() -> int:
    _ensure_loaded()
    return sum(u.size for u in _uploads.values())


def quota(user_id: str) -> dict:
    """Everything the UI needs to show usage, in one shape."""
    used = user_bytes(user_id)
    limit = settings.upload_max_user_mb * MB
    return {
        "used": used,
        "limit": limit,
        "remaining": max(0, limit - used),
        "max_file": settings.upload_max_file_mb * MB,
        "count": len(for_user(user_id)),
    }


def check_headroom(user_id: str) -> None:
    """Refuse before reading a single byte when there is clearly no room.

    Cheap pre-flight. The authoritative enforcement is the running total in
    `save_stream`, because the real size is not known until the bytes arrive.
    """
    _ensure_loaded()
    if total_bytes() >= settings.upload_max_total_mb * MB:
        raise UploadError(
            "Upload storage is full — contact support.", status=507)
    if user_bytes(user_id) >= settings.upload_max_user_mb * MB:
        raise UploadError(
            f"You've used all {settings.upload_max_user_mb} MB of upload space. "
            "Delete a clip to make room.", status=507)


async def save_stream(user_id: str, filename: str, chunks) -> Upload:
    """Stream an upload to disk, enforcing every cap as the bytes arrive.

    `chunks` is an async iterator of bytes. Nothing is buffered whole in
    memory — the droplet has 2 GB of RAM and a 300 MB file read into a bytes
    object would be a meaningful fraction of it.

    On ANY rejection the partial file is deleted before raising, so a refused
    upload leaves no disk behind. That matters because the most likely reason
    for rejection is that we are near a disk cap already.
    """
    _ensure_loaded()
    check_headroom(user_id)

    max_file = settings.upload_max_file_mb * MB
    user_room = settings.upload_max_user_mb * MB - user_bytes(user_id)
    global_room = settings.upload_max_total_mb * MB - total_bytes()

    upload_id = uuid.uuid4().hex
    tmp = _ROOT / user_id / f".{upload_id}.part"
    tmp.parent.mkdir(parents=True, exist_ok=True)

    head = b""
    written = 0
    try:
        with tmp.open("wb") as f:
            async for chunk in chunks:
                if not chunk:
                    continue
                if len(head) < 12:
                    head += chunk[:12 - len(head)]
                written += len(chunk)
                # Checked DURING the copy: an oversized upload is stopped
                # partway rather than after it has already hit the disk.
                if written > max_file:
                    raise UploadError(
                        f"That file is larger than the {settings.upload_max_file_mb} MB limit.",
                        status=413)
                if written > user_room:
                    raise UploadError(
                        "That file would exceed your upload space. "
                        "Delete a clip to make room.", status=507)
                if written > global_room:
                    raise UploadError(
                        "Upload storage is full — contact support.", status=507)
                f.write(chunk)

        if written == 0:
            raise UploadError("That file is empty.")

        kind = sniff_container(head)
        if not kind:
            # Content-Type is not consulted anywhere: the browser sets it and
            # an attacker controls it. The bytes are the only evidence.
            raise UploadError(
                "That doesn't look like a video file. Upload an MP4, MOV or WebM.")

        up = Upload(
            id=upload_id,
            user_id=user_id,
            filename=_safe_display_name(filename),
            kind=kind,
            size=written,
            created_at=time.time(),
        )
        final = path_for(up)
        os.replace(tmp, final)       # atomic: never expose a partial file
    except BaseException:
        tmp.unlink(missing_ok=True)  # refused uploads leave no disk behind
        raise

    _uploads[up.id] = up
    _save()
    log.info("upload_saved", user_id=user_id, upload_id=up.id,
             kind=kind, size=written)
    return up


def delete(upload_id: str, user_id: str) -> Upload | None:
    """Remove an upload and its file. Returns the record, or None if the
    caller does not own it (indistinguishable from 'does not exist')."""
    up = get(upload_id, user_id)
    if not up:
        return None
    path_for(up).unlink(missing_ok=True)
    _uploads.pop(upload_id, None)
    _save()
    log.info("upload_deleted", user_id=user_id, upload_id=upload_id)
    return up


def delete_all_for_user(user_id: str) -> int:
    """Drop every upload for a user — called on account deletion so we don't
    keep video for someone who has left."""
    n = 0
    for up in for_user(user_id):
        path_for(up).unlink(missing_ok=True)
        _uploads.pop(up.id, None)
        n += 1
    if n:
        _save()
    return n
