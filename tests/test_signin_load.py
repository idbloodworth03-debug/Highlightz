"""Password sign-in must not be able to freeze the site (audit, 2026-10-20).

POST /login takes a password and nothing else, and it used to run the slow
hash (PBKDF2, ~0.12 s) against EVERY password account for every guess, on the
event loop. Measured on a copy of the app: ten wrong guesses from one IP —
inside the per-IP limit — stopped every page, socket and live channel for 5.4
seconds.

What these pin:
  1. each password account carries a fingerprint (HMAC under the server
     secret), never shown to a client, so the account a password belongs to
     is found without the slow hash; a wrong guess runs NO slow hash once every
     account has one, and a right one runs exactly one;
  2. accounts made before fingerprints (or fingerprinted under a rotated key)
     still sign in — the slow way — and are fingerprinted when they do;
  3. every slow hash runs off the event loop, and the old-way checks are capped
     across all IPs together; past the cap, or the queue, a sign-in is told to
     wait (a page, not JSON), and the right password for a fingerprinted
     account is never refused for load;
  4. the form, the per-IP limit and the uniqueness rule are unchanged.
"""

import asyncio
import json
import threading

import pytest

from src.auth import users as user_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    from src.dashboard import api
    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(api, "_login_attempts", {})
    monkeypatch.setattr(api, "_pw_slow_spent", [])
    monkeypatch.setattr(api, "_pw_waiting", 0)
    monkeypatch.setattr(api, "_pw_gate", None)
    monkeypatch.setattr(api, "_pw_gate_loop", None)
    return tmp_path


@pytest.fixture
def client(store):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    return TestClient(api.app, base_url="https://testserver")


@pytest.fixture
def hashes(monkeypatch):
    """Counts slow hashes from here on."""
    n = [0]
    real = user_store._hash_password

    def counted(password, salt=None):
        n[0] += 1
        return real(password, salt)
    monkeypatch.setattr(user_store, "_hash_password", counted)
    return n


def _legacy(store, username, password):
    """An account as it was before fingerprints: hash and salt only."""
    dk, salt = user_store._hash_password(password)
    users = json.loads((store / "users.json").read_text()) if (store / "users.json").exists() else []
    users.append({"id": f"id-{username}", "username": username, "password_hash": dk,
                  "salt": salt, "is_admin": False, "subscription_status": "none"})
    (store / "users.json").write_text(json.dumps(users))


def _sign_in(client, password):
    return client.post("/login", data={"password": password}, follow_redirects=False)


# ── 1. the fingerprint ───────────────────────────────────────────────────────

def test_a_new_password_account_is_fingerprinted_and_the_fingerprint_stays_private(store):
    u = user_store.create("rev", "reviewer-password-1")
    raw = user_store.get_by_id(u["id"])
    assert raw["pw_lookup"] and raw["pw_lookup_kid"]
    assert raw["pw_lookup"] != "reviewer-password-1"
    public = next(x for x in user_store.get_all() if x["id"] == u["id"])
    assert "pw_lookup" not in public and "pw_lookup_kid" not in public


def test_a_wrong_password_runs_no_slow_hash(client, hashes):
    for i in range(5):
        user_store.create(f"acct{i}", f"password-number-{i}-xyz")
    hashes[0] = 0
    r = _sign_in(client, "a-wrong-guess-123")
    assert r.headers["location"] == "/login?error=incorrect_password"
    assert hashes[0] == 0, f"{hashes[0]} slow hashes for a wrong guess"


def test_the_slow_hash_still_decides_when_a_fingerprint_matches(client, store):
    """A fingerprint left behind by a password change made elsewhere must not
    sign anyone in with the old password."""
    user_store.create("rev", "reviewer-password-1")
    users = json.loads((store / "users.json").read_text())
    users[0]["password_hash"], users[0]["salt"] = user_store._hash_password("a-new-password-2")
    (store / "users.json").write_text(json.dumps(users))
    matched, _ = user_store.password_candidates("reviewer-password-1")
    assert matched is not None                    # the stale fingerprint still matches...
    r = _sign_in(client, "reviewer-password-1")
    assert r.headers["location"] == "/login?error=incorrect_password"   # ...and is not enough


def test_the_right_password_signs_in_with_one_slow_hash(client, hashes):
    for i in range(5):
        user_store.create(f"acct{i}", f"password-number-{i}-xyz")
    hashes[0] = 0
    r = _sign_in(client, "password-number-3-xyz")
    assert r.status_code == 302 and r.headers["location"] == "/"
    assert hashes[0] == 1
    me = client.get("/me").json()
    assert me["username"] == "acct3"


# ── 2. accounts from before fingerprints ─────────────────────────────────────

def test_an_old_account_still_signs_in_and_is_fingerprinted_then(client, store, hashes):
    _legacy(store, "old_reviewer", "old-reviewer-password")
    user_store.create("newer", "newer-account-password")
    r = _sign_in(client, "old-reviewer-password")
    assert r.headers["location"] == "/"
    raw = user_store.get_by_id("id-old_reviewer")
    assert raw.get("pw_lookup") and raw.get("pw_lookup_kid")
    # Next time it is found by its fingerprint: no old-way checks are left.
    matched, unchecked = user_store.password_candidates("old-reviewer-password")
    assert matched["id"] == "id-old_reviewer" and unchecked == []
    hashes[0] = 0
    _sign_in(client, "a-wrong-guess-123")
    assert hashes[0] == 0


def test_a_wrong_guess_checks_only_the_unfingerprinted_accounts(client, store, hashes):
    _legacy(store, "old1", "old-one-password")
    _legacy(store, "old2", "old-two-password")
    for i in range(4):
        user_store.create(f"acct{i}", f"password-number-{i}-xyz")
    hashes[0] = 0
    _sign_in(client, "a-wrong-guess-123")
    assert hashes[0] == 2


def test_a_rotated_key_falls_back_to_the_slow_way_and_refingerprints(client, monkeypatch, hashes):
    from src.dashboard import api
    user_store.create("rev", "reviewer-password-1")
    old_kid = user_store.get_by_username("rev")["pw_lookup_kid"]
    monkeypatch.setattr(api.settings, "token_encryption_key", "a-brand-new-key-after-rotation")
    matched, unchecked = user_store.password_candidates("reviewer-password-1")
    assert matched is None and [u["username"] for u in unchecked] == ["rev"]
    assert _sign_in(client, "reviewer-password-1").headers["location"] == "/"
    assert user_store.get_by_username("rev")["pw_lookup_kid"] != old_kid


# ── 3. off the loop, and capped ──────────────────────────────────────────────

def test_slow_hashes_run_off_the_event_loop(store, monkeypatch):
    from src.dashboard import api
    _legacy(store, "old1", "old-one-password")
    user_store.create("rev", "reviewer-password-1")
    threads = []
    real = user_store.verify

    def spy(u, pw):
        threads.append(threading.get_ident())
        return real(u, pw)
    monkeypatch.setattr(user_store, "verify", spy)
    loop_thread = threading.get_ident()
    asyncio.run(api._password_account("old-one-password"))      # the slow way
    asyncio.run(api._password_account("reviewer-password-1"))   # by fingerprint
    assert threads and loop_thread not in threads


def test_the_old_way_is_capped_across_all_ips(client, store, monkeypatch, hashes):
    from src.dashboard import api
    _legacy(store, "old1", "old-one-password")
    _legacy(store, "old2", "old-two-password")
    monkeypatch.setattr(api, "_PW_SLOW_PER_MIN", 4)       # two guesses' worth
    for _ in range(2):
        assert _sign_in(client, "a-wrong-guess-123").headers["location"] == "/login?error=incorrect_password"
    hashes[0] = 0
    r = _sign_in(client, "old-one-password")
    assert r.headers["location"] == "/login?error=too_many_attempts"
    assert hashes[0] == 0, "the cap was spent and slow hashes still ran"


def test_the_right_password_of_a_fingerprinted_account_is_never_refused_for_load(client, store, monkeypatch):
    from src.dashboard import api
    _legacy(store, "old1", "old-one-password")
    user_store.create("rev", "reviewer-password-1")
    monkeypatch.setattr(api, "_PW_SLOW_PER_MIN", 0)
    monkeypatch.setattr(api, "_pw_waiting", api._PW_MAX_WAITING)
    assert _sign_in(client, "reviewer-password-1").headers["location"] == "/"


def test_a_full_queue_is_told_to_wait_rather_than_queue(client, store, monkeypatch, hashes):
    from src.dashboard import api
    _legacy(store, "old1", "old-one-password")
    monkeypatch.setattr(api, "_pw_waiting", api._PW_MAX_WAITING)
    hashes[0] = 0
    assert _sign_in(client, "old-one-password").headers["location"] == "/login?error=too_many_attempts"
    assert hashes[0] == 0


# ── 4. unchanged from the person's side ──────────────────────────────────────

def test_the_per_ip_limit_still_applies_and_now_says_so_on_the_page(client, store):
    user_store.create("rev", "reviewer-password-1")
    from src.dashboard import api
    for _ in range(api._LOGIN_MAX_ATTEMPTS):
        assert _sign_in(client, "a-wrong-guess-123").headers["location"] == "/login?error=incorrect_password"
    assert _sign_in(client, "reviewer-password-1").headers["location"] == "/login?error=too_many_attempts"
    page = client.get("/login?error=too_many_attempts").text
    assert "Too many sign-in attempts. Wait a minute and try again." in page


def test_the_form_is_still_one_password_field():
    from src.dashboard import api
    assert 'placeholder="Password"' in api.LOGIN_HTML
    assert 'name="username"' not in api.LOGIN_HTML


def test_a_password_in_use_is_still_found_fingerprinted_or_not(store):
    user_store.create("rev", "reviewer-password-1")
    _legacy(store, "old1", "old-one-password")
    assert user_store.password_in_use("reviewer-password-1")
    assert user_store.password_in_use("old-one-password")
    assert not user_store.password_in_use("nobody-has-this-one")
