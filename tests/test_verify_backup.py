"""The backup verifier, which exists because nothing read an archive back.

`backup.py` writes archives and uploads them. Nothing in the repo ever opened
one. Five nightly tarballs that have never been read are a hypothesis, and the
moment you learn otherwise is the moment you needed them.

The check worth having tests is the token one. `users.json` stores
tw_access/tw_refresh encrypted with TOKEN_ENCRYPTION_KEY — or, when that is
unset, with DASHBOARD_SECRET_KEY — and NEITHER IS IN THE ARCHIVE: both live in
.env, which is not under local_storage_path and is not a .json. So an archive
can be complete, parse perfectly, and still restore into 51 accounts nobody can
monitor. A verifier that reported "OK" on that would be worse than none.
"""

import json

import pytest

from src.auth import users as us
from src.maintenance import backup, verify_backup


def _store(tmp_path, *, users=None, skip=()):
    root = tmp_path / "clips"
    # exist_ok: one test builds the store twice to compare two configurations.
    (root / "profiles" / "u1").mkdir(parents=True, exist_ok=True)
    if users is None:
        users = [{"id": "u1", "username": "a", "subscription_status": "active",
                  "tw_access": us._encrypt("token-one")},
                 {"id": "u2", "username": "b", "subscription_status": "none",
                  "tw_access": us._encrypt("token-two")}]
    files = {"users.json": json.dumps(users), "clips.json": "[]",
             "streams.json": "[]", "clip_counter.json": '{"total": 50211}'}
    for name, body in files.items():
        if name not in skip:
            (root / name).write_text(body)
    (root / "profiles" / "u1" / "aceu.json").write_text('{"total_clips": 3}')
    return root


def _run(tmp_path, root, capsys):
    arc = backup.make_backup(storage_root=root, backups_dir=tmp_path / "backups",
                             keep=3)
    rc = verify_backup.main([f"--archive={arc}"])
    return rc, capsys.readouterr().out


@pytest.fixture(autouse=True)
def _no_offsite(monkeypatch):
    """Default to unconfigured, so tests that care set it explicitly."""
    monkeypatch.setattr(verify_backup.settings, "backup_s3_bucket", "")


def test_a_good_archive_reports_what_would_come_back(tmp_path, capsys):
    _, out = _run(tmp_path, _store(tmp_path), capsys)
    assert "2 account(s), 1 on an active or trialing plan" in out
    assert "2 with a stored Twitch token" in out
    assert "1 per-channel profile" in out


def test_tokens_that_decrypt_are_reported_as_restorable(tmp_path, capsys):
    _, out = _run(tmp_path, _store(tmp_path), capsys)
    assert "2/2 sampled tokens decrypt cleanly" in out


def test_tokens_encrypted_under_another_key_are_caught(tmp_path, capsys, monkeypatch):
    """THE POINT OF THE WHOLE TOOL. This is what a restore onto a fresh droplet
    looks like: the file is perfect and every token in it is noise. Reporting
    that archive as healthy is the failure mode worth preventing."""
    root = _store(tmp_path)
    monkeypatch.setattr(us.settings, "token_encryption_key", "a-different-key")
    rc, out = _run(tmp_path, root, capsys)
    assert "DO NOT decrypt with the current key" in out
    assert "reconnect Twitch" in out
    assert rc == 1, "an unrestorable archive exited as success"


def test_a_missing_irreplaceable_file_is_loud(tmp_path, capsys):
    rc, out = _run(tmp_path, _store(tmp_path, skip=("clips.json",)), capsys)
    assert "MISSING  clips.json" in out
    assert rc == 1


def test_the_key_not_being_in_the_archive_is_always_said(tmp_path, capsys):
    """True of every archive, including a healthy one — so it is stated even on
    a clean run rather than only when something already went wrong."""
    _, out = _run(tmp_path, _store(tmp_path), capsys)
    assert "IT IS NOT IN THIS ARCHIVE" in out


def test_the_shared_secret_foot_gun_is_called_out(tmp_path, capsys, monkeypatch):
    """With TOKEN_ENCRYPTION_KEY unset the session secret does double duty, so
    rotating it breaks every stored token with no disaster involved."""
    monkeypatch.setattr(us.settings, "token_encryption_key", "")
    monkeypatch.setattr(verify_backup.settings, "token_encryption_key", "")
    _, out = _run(tmp_path, _store(tmp_path), capsys)
    assert "doing double duty" in out

    monkeypatch.setattr(verify_backup.settings, "token_encryption_key", "set")
    _, out = _run(tmp_path, _store(tmp_path), capsys)
    assert "doing double duty" not in out


def test_a_missing_offsite_copy_fails_the_run(tmp_path, capsys):
    rc, out = _run(tmp_path, _store(tmp_path), capsys)
    assert "Every archive is on the same disk" in out
    assert rc == 1


def test_a_configured_offsite_copy_passes(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(verify_backup.settings, "backup_s3_bucket", "hz-backups")
    rc, out = _run(tmp_path, _store(tmp_path), capsys)
    assert "hz-backups" in out and rc == 0


def test_a_corrupt_users_file_is_not_reported_as_fine(tmp_path, capsys):
    root = _store(tmp_path)
    (root / "users.json").write_text("{not json")
    rc, out = _run(tmp_path, root, capsys)
    assert "CORRUPT" in out and rc == 1


def test_an_unopenable_archive_says_so(tmp_path, capsys):
    bad = tmp_path / "highlightz-state-20260101-000000.tar.gz"
    bad.write_bytes(b"not a tarball")
    rc = verify_backup.main([f"--archive={bad}"])
    assert "WILL NOT OPEN" in capsys.readouterr().out and rc == 1


def test_a_stale_archive_is_flagged(tmp_path, capsys):
    import os, time
    arc = backup.make_backup(storage_root=_store(tmp_path),
                             backups_dir=tmp_path / "backups", keep=3)
    old = time.time() - 86400 * 3
    os.utime(arc, (old, old))
    verify_backup.main([f"--archive={arc}"])
    assert "STALE" in capsys.readouterr().out


def test_it_writes_nothing(tmp_path, capsys):
    """Pointed at production. A verifier that mutates state is one nobody dares
    run, and extracting an archive over a live store is the worst version of
    that."""
    import inspect
    src = inspect.getsource(verify_backup)
    for writes in ("extractall", "write_text(", "unlink(", "_save(", ".extract("):
        assert writes not in src, f"the verifier calls {writes}"
