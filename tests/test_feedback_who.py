"""A name on the admin Feedback page opens that account's details (owner,
2026-11-18: "make it so I can click someones name in the feedback page and
see the details about them").

The page already loads every account for its composer, so the drawer is drawn
from that plus the two read-only per-account lists the admin page uses. What
these hold:

  1. the name in a thread is a button carrying the account's id, and a thread
     with no account behind it is not;
  2. the drawer on this page is READ-ONLY: it cannot grant, revoke, delete or
     post anything, and "Manage this account" is the way to what can;
  3. everything a person typed is escaped;
  4. its labels and its styles are the admin page's own, lifted, not retyped;
  5. /admin?user=<id> opens that account's drawer on the admin page;
  6. both pages' scripts still parse, with no backslashes;
  7. an account deleted since it wrote in is said to be gone.
"""

import inspect
import re
import shutil
import subprocess

import pytest

from src.auth import users as user_store
from src.dashboard import api

FB = api._ADMIN_FEEDBACK_HTML
ADMIN = api.ADMIN_HTML


def _script(page):
    blocks = re.findall(r"<script>(.*?)</script>", page, re.S)
    assert len(blocks) == 1, "one script block per admin page"
    return blocks[0]


def _fn(src, start, end):
    at = src.index(start)
    return src[at:src.index(end, at)]


OPEN = _fn(_script(FB), "async function openWho(uid, name){", "document.getElementById('list').addEventListener('click'")


# ── 1. the name ──────────────────────────────────────────────────────────────

def test_a_name_in_a_thread_is_a_button_that_knows_whose_it_is():
    js = _script(FB)
    assert '<button class="fb-user fb-who" data-uid="${esc(f.user_id)}"' in js
    # A thread with no account behind it gets a plain name, not a dead button.
    assert "${f.user_id\n" in js and '<span class="fb-user">${esc(f.username||\'Unknown\')}</span>' in js
    # Opened by one listener on the list, so it survives every re-render.
    assert "const b = e.target.closest('.fb-who'); if(!b) return;" in js
    assert "openWho(b.dataset.uid, b.textContent);" in js
    assert ".fb-who{" in FB and "cursor:pointer" in FB[FB.index(".fb-who{"):][:200]


def test_it_can_be_closed_three_ways_and_focus_goes_back():
    js = _script(FB)
    assert "document.getElementById('dr-close').addEventListener('click', closeWho);" in js
    assert "document.getElementById('scrim').addEventListener('click', closeWho);" in js
    assert "e.key === 'Escape' && document.getElementById('drawer').classList.contains('open')" in js
    assert "if(DR_FROM && document.contains(DR_FROM)) DR_FROM.focus();" in js
    assert 'id="drawer" role="dialog" aria-modal="true" aria-labelledby="dr-name"' in FB


# ── 2. read-only ─────────────────────────────────────────────────────────────

def test_the_drawer_here_cannot_change_an_account():
    # The only requests it makes are two GETs for this account's lists.
    calls = re.findall(r"api\('([^']+)'", OPEN)
    assert calls == ["/admin/users/", "/admin/users/"]
    assert "/streams').catch(() => [])" in OPEN and "/clips').catch(() => [])" in OPEN
    assert "fetch(" not in OPEN
    for banned in ("'POST'", "'DELETE'", "grant", "revoke", "dr-del", "dr-admin", "dr-stop",
                   "dr-offer", "confirm("):
        assert banned not in OPEN, banned
    # What changes an account is one press away, on the page built for it.
    assert "href=\"/admin?user=' + encodeURIComponent(u.id) + '\">Manage this account</a>" in OPEN
    for route in ("admin_user_streams", "admin_user_clips"):
        fn = getattr(api, route, None)
        if fn is not None:
            assert "_require_admin(request)" in inspect.getsource(fn)


def test_the_two_lists_it_reads_are_admin_only():
    src = inspect.getsource(api)
    for path in ('@app.get("/admin/users/{user_id}/streams")', '@app.get("/admin/users/{user_id}/clips")'):
        at = src.index(path)
        assert "_require_admin(request)" in src[at:at + 900], path
    assert "_require_admin(request)" in inspect.getsource(api.admin_feedback_page)


# ── 3. escaping ──────────────────────────────────────────────────────────────

def test_everything_a_person_typed_is_escaped():
    for field in ("esc(u.email)", "esc(u.found_via_detail)", "esc(a.detail)", "esc(u.ref)",
                  "esc(u.promo_code)", "esc(s.channel)", "esc(c.channel)",
                  "esc(c.clip_title || c.stream_title || '')", "esc(u.subscription_status || 'none')",
                  "esc(u.plan_label || u.plan || '')"):
        assert field in OPEN, field
    # The name goes in as text, never as markup.
    assert "document.getElementById('dr-name').textContent = u.username || name || uid;" in OPEN
    # No raw interpolation of a user field into the HTML string.
    for raw in ("+ u.email +", "+ a.detail +", "+ u.found_via_detail +", "+ s.channel +", "+ c.clip_title +"):
        assert raw not in OPEN, raw


# ── 4. one source for labels and styles ──────────────────────────────────────

def _table(page, name):
    return re.search(r"const " + name + r" = (\{.*?\});", page, re.S).group(1)


def test_the_labels_are_the_admin_pages_own():
    for name, keys in (("FU_LABELS", user_store.FOUND_VIA), ("CX_LABELS", user_store.CANCEL_REASONS)):
        assert _table(FB, name) == _table(ADMIN, name), name
        assert tuple(re.findall(r"^\s*([a-z_]+):", _table(FB, name), re.M)) == tuple(keys), name
    assert "<!--FU_LABELS-->" not in FB and "<!--CX_LABELS-->" not in FB
    assert api._admin_js_table("NO_SUCH_TABLE") == "{}"


def test_the_drawer_is_styled_from_one_place():
    css = api._ADMIN_DRAWER_STYLE
    assert ".drawer{" in css and ".kv{" in css and ".sbox{" in css and ".srow{" in css
    assert css in ADMIN and css in FB
    assert ADMIN.count(".drawer{position:fixed") == 1 and FB.count(".drawer{position:fixed") == 1


# ── 5. the admin page opens a named account ──────────────────────────────────

def test_the_admin_page_opens_the_account_named_in_the_address():
    js = _script(ADMIN)
    block = _fn(js, "refresh().then(function(){", "loadAffiliates();")
    assert "new URLSearchParams(location.search).get('user')" in block
    assert "USERS.find(function(x){ return x.id === want; })" in block
    assert "if(u) openUser(u);" in block
    # Nothing else on the page waits on it: the boot line still starts the load.
    assert js.count("refresh().then(") == 1


# ── 6. both scripts parse ────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["admin", "feedback"])
def test_the_scripts_parse_and_carry_no_backslashes(name, tmp_path):
    # By name: a whole page as a parameter becomes the test's id, and an id
    # that long does not fit in the environment node is started with.
    js = _script({"admin": ADMIN, "feedback": FB}[name])
    assert chr(92) not in js, "a backslash in an embedded script is eaten by Python first"
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed here")
    f = tmp_path / (name + ".js")
    f.write_text(js)
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[:500]


# ── 7. an account that is gone ───────────────────────────────────────────────

def test_a_deleted_account_is_said_to_be_gone_and_a_slow_list_is_waited_for():
    assert "This account no longer exists." in OPEN
    assert OPEN.index("await PEOPLE_READY;") < OPEN.index("const u = PEOPLE.find(x => x.id === uid);")
    assert "const PEOPLE_READY = loadPeople();" in _script(FB)
    # A second name pressed while the first is loading wins.
    assert OPEN.count("if(DR_UID !== uid) return;") == 2
    assert "refresh" not in OPEN.lower()
