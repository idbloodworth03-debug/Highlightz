"""
Tests for the one-shot token key rotation (src/maintenance/rotate_token_key).

The migration must: re-encrypt legacy-key tokens so the NEW key can read them,
leave undecryptable garbage untouched, be a no-op on re-run (idempotent), and
never write in dry-run mode.
"""

import json

from src.maintenance.rotate_token_key import rotate, _key_from


def _users_file(tmp_path, users):
    p = tmp_path / "users.json"
    p.write_text(json.dumps(users))
    return p


def test_rotation_roundtrip_and_idempotency(tmp_path):
    old, new = "old-session-secret", "new-dedicated-key"
    old_f = _key_from(old)
    users = [
        {"id": "u1", "username": "alice",
         "tw_access":  old_f.encrypt(b"access-token").decode(),
         "tw_refresh": old_f.encrypt(b"refresh-token").decode()},
        {"id": "u2", "username": "bob",
         "tw_access": "not-fernet-garbage", "tw_refresh": ""},
    ]
    f = _users_file(tmp_path, users)

    # Dry run: reports work but writes nothing.
    rotated, skipped, _ = rotate(f, old, new, apply=False)
    assert (rotated, skipped) == (2, 1)
    assert json.loads(f.read_text()) == users          # untouched

    # Apply: alice's tokens decrypt under the NEW key afterwards.
    rotated, skipped, _ = rotate(f, old, new, apply=True)
    assert (rotated, skipped) == (2, 1)
    after = {u["id"]: u for u in json.loads(f.read_text())}
    new_f = _key_from(new)
    assert new_f.decrypt(after["u1"]["tw_access"].encode()) == b"access-token"
    assert new_f.decrypt(after["u1"]["tw_refresh"].encode()) == b"refresh-token"
    # Garbage left exactly as it was — those users just re-auth as before.
    assert after["u2"]["tw_access"] == "not-fernet-garbage"
    # Snapshot of the pre-rotation state exists for rollback.
    assert (tmp_path / "users.json.pre-rotation").exists()

    # Re-run: everything is already on the new key → clean no-op.
    rotated2, skipped2, _ = rotate(f, old, new, apply=True)
    assert rotated2 == 0
    assert new_f.decrypt(json.loads(f.read_text())[0]["tw_access"].encode()) == b"access-token"
