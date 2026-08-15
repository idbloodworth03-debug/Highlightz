"""users.json must survive being written from a root shell.

This took the site down. Admin scripts get run from a root prompt, `_save()`
writes a fresh temp file and chmods it 0600, and `os.replace` therefore leaves
users.json owned by root. The service does not run as root, so its next start
died in ensure_admin_exists() with PermissionError before it could serve
anything — the whole site, not just billing.

The store is the only thing that can prevent this, because it is the only thing
that knows the file had an owner before the write.
"""

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0, reason="needs root to change file ownership")

SERVICE_UID, SERVICE_GID = 1000, 1000    # stand-in for the service account


@pytest.fixture
def store(tmp_path, monkeypatch):
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    os.chmod(tmp_path, 0o777)
    (tmp_path / "users.json").write_text(json.dumps(
        [{"id": "u1", "username": "nova", "subscription_status": "inactive"}]))
    os.chown(tmp_path / "users.json", SERVICE_UID, SERVICE_GID)
    os.chmod(tmp_path / "users.json", 0o600)
    return user_store, tmp_path


def test_a_root_write_leaves_the_file_readable_by_the_service(store):
    user_store, tmp_path = store
    user_store.update_subscription("u1", "cus_X", "active")
    st = os.stat(tmp_path / "users.json")
    assert (st.st_uid, st.st_gid) == (SERVICE_UID, SERVICE_GID), \
        "root took ownership of users.json — the service cannot start"


def test_the_backup_is_handed_back_too(store):
    """The backup is the recovery path. Root-owned, it is useless at exactly
    the moment it is needed."""
    user_store, tmp_path = store
    user_store.update_subscription("u1", "cus_X", "active")
    bak = tmp_path / "users.json.bak"
    if bak.exists():
        st = os.stat(bak)
        assert (st.st_uid, st.st_gid) == (SERVICE_UID, SERVICE_GID)


def test_the_write_still_happens_and_stays_private(store):
    """Ownership must not be preserved by skipping the write, and the file
    holds OAuth tokens so it must stay 0600."""
    user_store, tmp_path = store
    user_store.update_subscription("u1", "cus_X", "active")
    assert user_store.get_by_id("u1")["stripe_customer_id"] == "cus_X"
    assert oct(os.stat(tmp_path / "users.json").st_mode)[-3:] == "600"
