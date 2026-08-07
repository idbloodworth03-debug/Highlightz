"""
Tests for the audit-batch maintenance work: state backups, the dry-spell
threshold retune, and the landing page's social share card.
"""

import json
import tarfile

from src.maintenance import backup as bk
from src.trigger import scoring


# ── Backups ────────────────────────────────────────────────────────────────────

def _make_state(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "users.json").write_text(json.dumps([{"id": "u1"}]))
    (root / "clips.json").write_text("[]")
    (root / "training_log.jsonl").write_text('{"label":"approved"}\n')
    prof = root / "profiles" / "u1"
    prof.mkdir(parents=True)
    (prof / "jynxzi.json").write_text(json.dumps({"channel": "jynxzi"}))
    (root / "some_clip.mp4").write_bytes(b"\x00" * 64)   # media must be excluded


def test_backup_archives_state_and_excludes_media(tmp_path):
    root = tmp_path / "clips"
    dest = tmp_path / "backups"
    _make_state(root)
    archive = bk.make_backup(storage_root=root, backups_dir=dest, keep=5)
    assert archive is not None and archive.exists()
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert any(n.endswith("users.json") for n in names)
    assert any(n.endswith("training_log.jsonl") for n in names)
    assert any(n.endswith("profiles/u1/jynxzi.json") for n in names)
    assert not any(n.endswith(".mp4") for n in names)


def test_backup_rotation_keeps_newest_n(tmp_path):
    root = tmp_path / "clips"
    dest = tmp_path / "backups"
    _make_state(root)
    # Pre-seed older archives (names sort lexicographically by timestamp)
    dest.mkdir()
    for stamp in ("20250101-000000", "20250102-000000", "20250103-000000"):
        (dest / f"highlightz-state-{stamp}.tar.gz").write_bytes(b"x")
    bk.make_backup(storage_root=root, backups_dir=dest, keep=2)
    left = sorted(p.name for p in dest.glob("highlightz-state-*.tar.gz"))
    assert len(left) == 2
    assert "highlightz-state-20250101-000000.tar.gz" not in left  # oldest gone


def test_backup_handles_empty_state(tmp_path):
    assert bk.make_backup(storage_root=tmp_path / "nope",
                          backups_dir=tmp_path / "b", keep=3) is None


# ── Dry-spell retune ───────────────────────────────────────────────────────────

def test_dry_spell_floor_beats_junk_clip_zone():
    # The old floor (40) let sustained dry-spells drag busy channels into a bar
    # low enough that weak moments fired constantly. The retune keeps the floor
    # above that zone and slows the step so reject feedback (+2/reject) wins.
    assert scoring.DRY_SPELL_THRESHOLD_FLOOR >= 52.0
    assert scoring.DRY_SPELL_THRESHOLD_STEP <= 2.0
    assert scoring.DRY_SPELL_SECS >= 900.0


def test_dry_spell_never_lowers_below_floor():
    import time as _t
    from src.trigger.engine import TriggerEngine
    from src.profiles.profile import StreamerProfile

    async def _noop(_): pass
    start = scoring.DRY_SPELL_THRESHOLD_FLOOR + 1   # one step lands on the floor
    eng = TriggerEngine("x", on_trigger=_noop,
                        profile=StreamerProfile(channel="x", trigger_threshold=start))
    now = _t.time()
    eng._dry_anchor = now - scoring.DRY_SPELL_SECS - 1   # long dry spell elapsed
    eng._maybe_recalibrate_dry_spell(now)
    assert eng.profile.trigger_threshold == scoring.DRY_SPELL_THRESHOLD_FLOOR
    # Another full dry cycle cannot go below the floor.
    eng._dry_anchor = now - scoring.DRY_SPELL_SECS - 1
    eng._maybe_recalibrate_dry_spell(now)
    assert eng.profile.trigger_threshold == scoring.DRY_SPELL_THRESHOLD_FLOOR


def test_dry_spell_never_raises_a_threshold_already_below_floor():
    # Approve-learning may legitimately hold a channel below the dry-spell
    # floor; the dry spell must not fight that by raising it.
    import time as _t
    from src.trigger.engine import TriggerEngine
    from src.profiles.profile import StreamerProfile

    async def _noop(_): pass
    below = scoring.DRY_SPELL_THRESHOLD_FLOOR - 7
    eng = TriggerEngine("x", on_trigger=_noop,
                        profile=StreamerProfile(channel="x", trigger_threshold=below))
    now = _t.time()
    eng._dry_anchor = now - scoring.DRY_SPELL_SECS - 1
    eng._maybe_recalibrate_dry_spell(now)
    assert eng.profile.trigger_threshold == below


# ── Share card ─────────────────────────────────────────────────────────────────

def test_landing_has_share_card_tags_and_asset():
    from pathlib import Path
    from src.dashboard.api import LANDING_HTML, _STATIC_DIR
    assert 'property="og:image"' in LANDING_HTML
    assert 'name="twitter:card"' in LANDING_HTML
    # Versioned, because social platforms cache the preview keyed on the URL —
    # rewriting the bytes at the old path does not refresh what anyone sees, so
    # a corrected card has to ship under a new filename. See
    # tests/test_brand_assets.py for the rest of that story.
    assert "https://highlightz.app/static/og-card-v2.png" in LANDING_HTML
    # The referenced asset must actually ship.
    assert (Path(_STATIC_DIR) / "og-card-v2.png").exists()
    # The retired path stays on disk: links shared before the rename still point
    # at it, and deleting it turns each of those posts into a broken image.
    assert (Path(_STATIC_DIR) / "og-card.png").exists()


def test_mp4_derivation_only_accepts_real_clip_thumbnails():
    """The probe must refuse to guess.

    Our own clips.json turned out to hold VOD thumbnails
    (cf_vods/.../thumb0-1280x720.jpg), not clip thumbnails. A naive
    `.replace('.jpg','.mp4')` would happily build a URL from one of those, get
    a 403, and read as "Twitch blocked us" when it really means "wrong input".
    Returning None keeps an inconclusive probe distinguishable from a negative
    one — which is the whole value of running it.
    """
    from src.maintenance.probe_clip_media import mp4_from_thumbnail

    clip = ("https://clips-media-assets2.twitch.tv/"
            "AT-cm%7C1234567890-preview-480x272.jpg")
    assert mp4_from_thumbnail(clip) == (
        "https://clips-media-assets2.twitch.tv/AT-cm%7C1234567890.mp4")
    # Any -preview- size works; Twitch varies it.
    assert mp4_from_thumbnail(
        "https://x.tv/abc-preview-260x147.jpg") == "https://x.tv/abc.mp4"

    # The VOD shape we actually have on prod must NOT derive anything.
    vod = ("https://static-cdn.jtvnw.net/cf_vods/d2nvs31859zcd8/"
           "87e4c6506abe71ffff76_beyondcookedmax_316854934263_1782266575//"
           "thumb/thumb0-1280x720.jpg")
    assert mp4_from_thumbnail(vod) is None
    assert mp4_from_thumbnail("") is None
    assert mp4_from_thumbnail("https://x.tv/no-suffix.jpg") is None
    # '-preview-' must be the SUFFIX, not merely present in the path.
    assert mp4_from_thumbnail("https://x.tv/a-preview-480x272.jpg/other.png") is None
