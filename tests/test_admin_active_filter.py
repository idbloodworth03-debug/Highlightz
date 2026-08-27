"""The admin user list's default view must show free-tier signups.

WHY THIS EXISTS. "Active" meant `admin || active || trialing`, which was right
while the free tier was closed: an account that had signed up and not paid had
no access, so a list full of them buried the real users. Reopening the free
tier inverted that — a free signup is a live user, often the newest one — and
the default view silently stopped showing the very people the free tier exists
to attract. Nothing failed; the list was just quietly short.

The predicate is JavaScript inside the admin page, so these run the SHIPPED
function in node rather than reimplementing it. A Python copy of the rule would
only ever test the copy. Same approach as tests/test_clip_sorting.py.
"""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SRC = Path("src/dashboard/api.py").read_text()
NODE = shutil.which("node") or "/opt/node22/bin/node"

pytestmark = pytest.mark.skipif(
    not Path(NODE).exists(), reason="node is not installed here")


def _filter_block() -> str:
    """userState + userMatches, exactly as they ship."""
    m = re.search(r"\nfunction userState\(u\)\{.*?\n\}\n\nfunction userMatches\(u\)\{.*?\n\}\n",
                  SRC, re.S)
    assert m, "the admin filter block moved — this test is no longer testing anything"
    return m.group(0)


# Everyone the list has to place, with the subscription_status each really
# carries. Both spellings a fresh signup can have are here: upsert writes
# "none", and an older record can have it empty.
USERS = [
    {"id": "paying",   "subscription_status": "active",   "plan": "pro"},
    {"id": "trialing", "subscription_status": "trialing", "plan": "pro"},
    {"id": "free_new", "subscription_status": "none",     "plan": "free"},
    {"id": "free_old", "subscription_status": "",         "plan": "free"},
    {"id": "churned",  "subscription_status": "canceled", "plan": "free"},
    {"id": "staff",    "subscription_status": "none",     "plan": "pro", "is_admin": True},
]


def _shown(filter_name: str, users=None) -> list[str]:
    """Ids the admin table would render under one chip."""
    script = textwrap.dedent("""
        %s
        const STALLED = [];
        let U_Q = '', U_FILTER = %s;
        const users = %s;
        console.log(JSON.stringify(users.filter(userMatches).map(u => u.id)));
    """) % (_filter_block(), json.dumps(filter_name), json.dumps(users or USERS))
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_new_free_signup_appears_in_the_default_view():
    """The bug, stated as the thing the owner actually noticed."""
    shown = _shown("active")
    assert "free_new" in shown, "a new free-tier signup is missing from Active"
    assert "free_old" in shown, "a signup with a blank status is missing from Active"


def test_active_still_shows_everyone_who_was_there_before():
    """The fix must add, not swap."""
    shown = _shown("active")
    for who in ("paying", "trialing", "staff"):
        assert who in shown, f"{who} fell out of the Active view"


def test_a_churned_subscriber_stays_under_lapsed():
    """NOT because they lack access — plans.get_plan drops a cancelled account
    to free, same as a new signup. They are held out because somebody who paid
    and stopped is a churn event, and folding them into the default view would
    bury it."""
    assert "churned" not in _shown("active")
    assert _shown("lapsed") == ["churned"]


def test_the_default_chip_is_the_one_these_tests_describe():
    """`active` is asserted above; if the page opened on a different chip these
    tests would be describing a view nobody sees."""
    m = re.search(r"U_FILTER = '([a-z]+)'", SRC)
    assert m and m.group(1) == "active", "the admin list no longer opens on Active"


def test_every_account_is_reachable_from_some_chip():
    """A user who matches no filter is invisible unless you think to press All.

    Guards the shape of the change rather than one case: any future state that
    falls between the chips shows up here instead of as a support question.
    """
    chips = re.findall(r'data-f="([a-z]+)"', SRC)
    assert "active" in chips and "lapsed" in chips, f"chips changed: {chips}"
    placed = set()
    for chip in chips:
        if chip == "all":
            continue
        placed.update(_shown(chip))
    missing = {u["id"] for u in USERS} - placed
    assert not missing, f"{sorted(missing)} appear under no chip except All"
