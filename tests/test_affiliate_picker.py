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


# ── affiliates are visible in the users table ────────────────────────────────

def test_an_affiliate_is_marked_in_the_users_table():
    """They were indistinguishable from any other account. Somebody asking
    'is this person an affiliate?' had to open the Growth tab and read a
    different list."""
    html = api.ADMIN_HTML
    assert "u.affiliate_code" in html
    assert '<span class="tagm aff"' in html, "no affiliate mark is rendered"
    assert ".tagm.aff{" in html, "the mark has no styling of its own"


def test_the_mark_carries_the_code_itself():
    """'AFFILIATE' alone sends you to another tab to find out which code. The
    code is the thing you need when somebody asks about their numbers."""
    html = api.ADMIN_HTML
    marks = html[html.index("const marks = "):]
    marks = marks[:marks.index("const canGrant")]
    assert "esc(u.affiliate_code)" in marks, \
        "the mark does not print the code"


def test_affiliates_can_be_filtered_for():
    """A label you cannot filter by means scrolling to find them."""
    html = api.ADMIN_HTML
    assert 'data-f="affiliate"' in html, "no Affiliates chip"
    assert "U_FILTER === 'affiliate'" in html, "the chip filters nothing"


def test_the_affiliate_filter_is_checked_before_the_plan_fallback():
    """THE BUG THIS PREVENTS. The last line of userMatches is
    `return u.plan === U_FILTER`, so a filter added after it is compared
    against plan names and silently matches nobody — a chip that looks wired
    up and always shows an empty list."""
    html = api.ADMIN_HTML
    fn = html[html.index("function userMatches(u)"):]
    fn = fn[:fn.index("\n}")]
    assert fn.index("U_FILTER === 'affiliate'") < fn.index("return u.plan === U_FILTER")


def test_an_account_can_be_searched_by_its_code():
    """You are usually holding the code, not the username — somebody asks
    about 'tommy' and that is what you have to go on."""
    html = api.ADMIN_HTML
    hay = html[html.index("const hay = "):]
    hay = hay[:hay.index("\n")+120]
    assert "affiliate_code" in hay, "the code is not searchable"
