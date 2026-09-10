"""
The account picker in the admin Growth tab.

WHY THIS HAS TESTS AT ALL. The picker is built from /admin/users, and its
labels carry each account's current affiliate code so that assigning a second
one to somebody who already has one is a visible act rather than a surprise the
server has to refuse. That only works if the field actually survives the trip —
and `affiliate_code` reaches the browser by NOT being in the secret-field list,
which is exactly the kind of implicit inclusion a later change breaks silently.
"""

import base64
import json as _j

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.dashboard import api


PEOPLE = {
    "boss":  {"id": "boss", "username": "bloodworthhh", "is_admin": True,
              "subscription_status": "active", "plan": "pro"},
    "tommy": {"id": "tommy", "username": "tommy", "twitch_id": "1",
              "subscription_status": "none", "plan": "free",
              "affiliate_code": "tommy", "tw_access": "SECRET",
              "tw_refresh": "SECRET"},
}


@pytest.fixture
def client(monkeypatch):
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    monkeypatch.setattr(user_store, "get_all",
                        lambda: [{k: v for k, v in u.items()
                                  if k not in ("tw_access", "tw_refresh")}
                                 for u in PEOPLE.values()])
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    data = base64.b64encode(_j.dumps({
        "auth": True, "user_id": "boss", "subscription_status": "active",
    }).encode())
    c.cookies.set("session", signer.sign(data).decode())
    return c


def test_the_user_list_carries_the_affiliate_code(client):
    """The picker's labels are built from this. Without the field they all read
    as unassigned and the owner overwrites somebody's code by accident."""
    rows = client.get("/admin/users").json()
    tommy = next(r for r in rows if r["id"] == "tommy")
    assert tommy["affiliate_code"] == "tommy"


def test_the_user_list_still_carries_no_tokens(client):
    """It gained a field; it must not have gained these. The picker made this
    payload wider, and wider payloads are how secrets escape."""
    body = client.get("/admin/users").text
    assert "tw_access" not in body and "tw_refresh" not in body
    assert "SECRET" not in body


def test_the_growth_tab_renders_a_picker_not_a_text_box():
    """The whole request: choose an account, do not retype one."""
    html = api.ADMIN_HTML
    assert '<select class="btn" id="af-user">' in html
    assert 'id="af-user" placeholder' not in html, "the old text input is back"


def test_the_picker_is_filled_when_the_user_list_lands():
    """Not from loadAffiliates: the two run concurrently at boot, so a picker
    built there can be built from an empty USERS and never refill itself."""
    html = api.ADMIN_HTML
    load_users = html[html.index("async function loadUsers()"):]
    load_users = load_users[:load_users.index("function fillAffiliatePicker")]
    assert "fillAffiliatePicker()" in load_users


def test_assigning_a_code_refreshes_the_picker_labels():
    """Otherwise the dropdown keeps advertising the code somebody just lost."""
    html = api.ADMIN_HTML
    fn = html[html.index("async function setAffiliate("):]
    fn = fn[:fn.index("document.getElementById('af-set')")]
    assert "loadUsers()" in fn, "the picker is left showing stale codes"
    assert "loadAffiliates()" in fn
