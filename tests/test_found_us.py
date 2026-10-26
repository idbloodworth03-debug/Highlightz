"""Where did you find us? The question a new account answers first.

Owner (2026-10-26): "When new users sign up with a free account lets make them
take a quick like 1 or 2 question quiz asking them where they found us before
they can continue in the process".

What these hold:
  1. only accounts created since it shipped are asked: the OAuth callbacks
     mark a NEW account, Twitch or Kick, never a returning one, never staff;
  2. the answer is a fixed vocabulary plus an optional line in their own
     words; "other" needs the line; the first answer stands;
  3. /me says when it is due; saving tells that account's other tabs, and the
     page has a handler for the event (CLAUDE.md's realtime contract);
  4. the quiz is in front of everything for a new account, the Add a channel
     screen included, and it cannot be skipped;
  5. the admin Onboarding tab counts it, asked and answered apart;
  6. nothing else in the product reads it, and the Privacy Policy says it is
     collected.
"""

import base64
import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.dashboard import api

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def store(tmp_path, monkeypatch):
    from src.auth import users
    f = tmp_path / "users.json"
    f.write_text("[]")
    monkeypatch.setattr(users, "_USERS_FILE", f)
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.bak.json")
    return users


@pytest.fixture
def scene(store, monkeypatch):
    """Both sign-ins wired to fakes, broadcasts recorded, and a way to hold a
    session for any account."""
    from src.auth import kick_oauth, twitch_oauth
    monkeypatch.setattr(api.settings, "kick_client_id", "kick-app")
    monkeypatch.setattr(api.settings, "kick_client_secret", "kick-secret")
    monkeypatch.setattr(api.settings, "twitch_client_id", "tw-app")
    monkeypatch.setattr(api.settings, "admin_twitch_id", "")
    sent = []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)

    async def _tx(code):
        return {"access_token": "tw-at", "refresh_token": "tw-rt", "expires_in": 3600}

    async def _tu(token):
        return {"id": "9001", "login": "novafps", "username": "NovaFPS", "avatar_url": "", "email": ""}

    async def _kx(code, verifier):
        return {"access_token": "kick-at", "refresh_token": "", "expires_in": 3600}

    async def _ku(token):
        return {"id": "77001", "username": "Nova Plays", "slug": "nova-plays", "avatar_url": "", "email": ""}
    monkeypatch.setattr(twitch_oauth, "exchange_code", _tx)
    monkeypatch.setattr(twitch_oauth, "get_user", _tu)
    monkeypatch.setattr(kick_oauth, "exchange_code", _kx)
    monkeypatch.setattr(kick_oauth, "get_user", _ku)

    c = TestClient(api.app, base_url="https://testserver")
    c.sent, c.store = sent, store

    def login(uid, username="x"):
        c.cookies.clear()
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        data = base64.b64encode(json.dumps({"auth": True, "user_id": uid, "username": username,
                                            "subscription_status": "none"}).encode())
        c.cookies.set("session", signer.sign(data).decode())
    c.login = login
    return c


def _sign_in(c, platform):
    r = c.get(f"/auth/{platform}", follow_redirects=False)
    state = re.search(r"state=([^&]+)", r.headers["location"]).group(1)
    return c.get(f"/auth/{platform}/callback?code=good&state={state}", follow_redirects=False)


def _only_user(store):
    users = store.get_all()
    assert len(users) == 1
    return users[0]


# ── who is asked ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("platform", ["twitch", "kick"])
def test_a_new_sign_up_is_asked_on_either_platform(scene, platform):
    r = _sign_in(scene, platform)
    assert r.status_code in (302, 307) and "error" not in r.headers["location"]
    u = _only_user(scene.store)
    assert u.get("found_via_asked_at"), "a new account was not marked as due the question"
    assert scene.get("/me").json()["found_via_due"] is True


def test_an_account_from_before_it_shipped_is_never_asked(scene):
    scene.store.upsert_twitch_user(twitch_id="9001", login="novafps", username="NovaFPS")
    _sign_in(scene, "twitch")
    u = _only_user(scene.store)
    assert not u.get("found_via_asked_at")
    assert scene.get("/me").json()["found_via_due"] is False


def test_staff_are_never_asked(scene, monkeypatch):
    monkeypatch.setattr(api.settings, "admin_twitch_id", "9001")
    _sign_in(scene, "twitch")
    u = _only_user(scene.store)
    assert u.get("is_admin") and not u.get("found_via_asked_at")
    assert scene.get("/me").json()["found_via_due"] is False


def test_answering_once_is_enough_across_sign_ins(scene):
    _sign_in(scene, "twitch")
    r = scene.post("/onboarding/source", json={"source": "reddit", "detail": "r/passive_income"})
    assert r.status_code == 200, r.text
    scene.cookies.clear()
    _sign_in(scene, "twitch")
    assert scene.get("/me").json()["found_via_due"] is False


# ── the answer ───────────────────────────────────────────────────────────────

def test_only_the_known_answers_are_kept(store):
    u = store.create("nova", "a-long-enough-password")
    with pytest.raises(ValueError):
        store.set_found_via(u["id"], "myspace")
    with pytest.raises(ValueError):
        store.set_found_via(u["id"], "")
    assert store.set_found_via(u["id"], " TikTok ")["source"] == "tiktok"


def test_somewhere_else_needs_the_where(store):
    u = store.create("nova", "a-long-enough-password")
    for empty in ("", "   ", "x"):
        with pytest.raises(ValueError):
            store.set_found_via(u["id"], "other", empty)
    a = store.set_found_via(u["id"], "other", "a podcast")
    assert a["source"] == "other" and a["detail"] == "a podcast"


def test_the_line_is_optional_for_everything_else(store):
    u = store.create("nova", "a-long-enough-password")
    a = store.set_found_via(u["id"], "youtube")
    assert a["source"] == "youtube" and a["detail"] == ""


def test_their_words_are_one_clean_line(store):
    assert store.clean_found_via_detail("  r/LivestreamFail\n\tthread  ") == "r/LivestreamFail thread"
    assert store.clean_found_via_detail("a" + chr(0) + "b" + chr(7) + "c") == "abc"
    assert len(store.clean_found_via_detail("x" * 500)) == store.FOUND_VIA_DETAIL_MAX
    assert store.clean_found_via_detail(None) == ""


def test_the_first_answer_stands(store):
    """Like the referral: a second tab or a replayed request cannot change
    what was said."""
    u = store.create("nova", "a-long-enough-password")
    store.ask_found_via(u["id"])
    first = store.set_found_via(u["id"], "reddit", "r/clips")
    again = store.set_found_via(u["id"], "tiktok")
    assert again == first and store.get_by_id(u["id"])["found_via"] == "reddit"


def test_an_answer_implies_the_question(store):
    """So asked can never be smaller than answered in the admin numbers."""
    u = store.create("nova", "a-long-enough-password")
    store.set_found_via(u["id"], "google")
    assert store.get_by_id(u["id"]).get("found_via_asked_at")


def test_asking_twice_keeps_the_first_time(store):
    u = store.create("nova", "a-long-enough-password")
    assert store.ask_found_via(u["id"]) is True
    at = store.get_by_id(u["id"])["found_via_asked_at"]
    assert store.ask_found_via(u["id"]) is False
    assert store.get_by_id(u["id"])["found_via_asked_at"] == at


def test_an_unknown_account_is_not_an_error(store):
    assert store.set_found_via("nobody", "reddit") is None
    assert store.ask_found_via("nobody") is False
    assert store.found_via_due(None) is False


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_the_endpoint_needs_a_login(scene):
    """Signed out, the auth gate sends it to the sign-in page before it gets
    anywhere near an account, exactly as it does POST /onboarding."""
    r = scene.post("/onboarding/source", json={"source": "reddit"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"
    assert "/onboarding/source" not in api._OPEN_PATHS
    assert scene.store.get_all() == []


def test_saving_closes_it_in_every_tab_of_that_account_only(scene):
    _sign_in(scene, "kick")
    uid = _only_user(scene.store)["id"]
    r = scene.post("/onboarding/source", json={"source": "tiktok", "detail": "a duxxion clip"})
    assert r.status_code == 200, r.text
    assert r.json()["found_via"]["source"] == "tiktok" and r.json()["found_via_due"] is False
    assert ("found_via_saved", uid) in scene.sent, "the event must be scoped to this account"
    assert not any(e == "found_via_saved" and who is None for e, who in scene.sent)
    assert scene.get("/me").json()["found_via_due"] is False
    stored = scene.store.get_by_id(uid)
    assert stored["found_via"] == "tiktok" and stored["found_via_detail"] == "a duxxion clip"


def test_a_bad_answer_is_refused_with_a_reason(scene):
    _sign_in(scene, "twitch")
    r = scene.post("/onboarding/source", json={"source": "other", "detail": ""})
    assert r.status_code == 400 and "where" in r.json()["detail"].lower()
    r = scene.post("/onboarding/source", json={"source": "carrier pigeon"})
    assert r.status_code == 400
    r = scene.post("/onboarding/source", json=["reddit"])
    assert r.status_code == 400
    assert scene.get("/me").json()["found_via_due"] is True, "a refused answer closed the quiz"


def test_every_event_it_emits_has_a_frontend_handler():
    import inspect
    assert "found_via_saved" in inspect.getsource(api.post_found_via)
    assert "msg.event==='found_via_saved'" in _page()


# ── the quiz ─────────────────────────────────────────────────────────────────

def _page() -> str:
    from src.dashboard import aurora_html
    return pathlib.Path(aurora_html.__file__).read_text()


def _modal() -> str:
    page = _page()
    body = page[page.index("function FoundUsModal("):]
    return body[:body.index("\nfunction ")]


def test_the_quiz_offers_exactly_the_stored_vocabulary():
    from src.auth import users
    page = _page()
    block = page[page.index("const FOUND_VIA = ["):]
    block = block[:block.index("];")]
    assert tuple(re.findall(r"\[\s*'([a-z]+)',", block)) == users.FOUND_VIA
    admin = api.ADMIN_HTML[api.ADMIN_HTML.index("const FU_LABELS = {"):]
    admin = admin[:admin.index("};")]
    assert tuple(re.findall(r"\n  ([a-z]+): '", admin)) == users.FOUND_VIA


def test_the_quiz_cannot_be_skipped():
    """No close button, no Escape, a backdrop with no click handler, Continue
    disabled until there is an answer, and focus kept inside: any one of
    those missing is a way past it."""
    body = _modal()
    assert "onClose" not in body and "rd-modal-close" not in body and ">Skip<" not in body
    assert "'Escape'" not in body
    head = body[body.index('className="rd-ann-bg" role="dialog" aria-modal="true" aria-labelledby'):]
    assert "onClick" not in head[:head.index(">")]
    assert "disabled={busy || !ready}" in body
    assert "const ready = !!source && (!needsLine || detail.trim().length >= 2);" in body
    assert "e.key !== 'Tab'" in body and "box.current.contains(document.activeElement)" in body


def test_a_failed_save_leaves_the_quiz_open():
    body = _modal()
    save = body[body.index("const save = async"):body.index("if (sent)")]
    assert "setErr(" in save
    assert save.index("if (!r.ok) throw") < save.index("onDone();"), "it closes before checking the save"


def test_the_quiz_comes_before_everything_else():
    page = _page()
    first_run = page[page.index("<FirstRun onAdd"):][:400]
    assert "{needsFoundUs && <FoundUsModal" in first_run, "the Add a channel screen comes first"
    shell = page[page.index("{needsFoundUs && <FoundUsModal preview={onbPreview} onDone={foundUsDone}/>}\n      {announcements"):]
    shell = shell[:shell.index("{postClip")]
    assert "announcements.length > 0 && !needsFoundUs" in shell
    assert "!needsOnboarding && !needsFoundUs && me && me.offer" in shell
    assert "{needsOnboarding && !needsFoundUs && !wake && <OnboardingModal" in shell


def test_the_flag_comes_from_me_so_a_reconnect_rederives_it():
    page = _page()
    line = page.split("const needsFoundUs = ")[1].split(";")[0]
    assert "me.found_via_due" in line and "useState" not in line


def test_the_admin_preview_walks_it_and_saves_nothing():
    body = _modal()
    assert "if (preview) { setSent(body); return; }" in body
    assert body.index("if (preview) { setSent(body); return; }") < body.index("fetch('/onboarding/source'")
    assert "if (preview) return;" in body, "the admin preview must not trap focus"
    page = _page()
    assert "const needsFoundUs = onbPreview ? !fuPreviewDone" in page


# ── the admin numbers ────────────────────────────────────────────────────────

def test_the_admin_numbers_keep_asked_and_answered_apart(scene):
    st = scene.store
    boss = st.create("boss", "a-long-enough-password", is_admin=True)
    old = st.create("old", "a-long-enough-password")          # before it shipped
    new1 = st.create("new1", "a-long-enough-password")
    new2 = st.create("new2", "a-long-enough-password")
    for u in (new1, new2):
        st.ask_found_via(u["id"])
    st.set_found_via(new1["id"], "reddit", "r/passive_income")
    scene.login(old["id"])
    assert scene.get("/admin/onboarding").status_code == 403
    scene.login(boss["id"], "boss")
    d = scene.get("/admin/onboarding").json()["found_us"]
    assert d["asked"] == 2 and d["answered"] == 1 and d["completion_pct"] == 50.0
    assert d["sources"]["reddit"] == 1 and set(d["sources"]) == set(st.FOUND_VIA)
    assert d["rows"] == [{"user": "new1", "source": "reddit", "detail": "r/passive_income",
                          "at": d["rows"][0]["at"]}]


def test_the_admin_tab_shows_the_counts_and_their_words():
    html = api.ADMIN_HTML
    panel = html[html.index('<div class="panel" id="panel-onboarding">'):]
    panel = panel[:panel.index("<!-- ── REVIEWS ── -->")]
    for el in ('id="fu-c"', 'id="fu-stats"', 'id="fu-wrap"'):
        assert el in panel
    js = html[html.index("async function loadOnboarding()"):html.index("// ── downloads")]
    assert "d.found_us" in js and "renderFoundUs(q);" in js
    assert "esc(r.detail)" in js, "their own words reach the admin page unescaped"


# ── nothing else reads it, and it is disclosed ───────────────────────────────

def test_nothing_in_the_product_branches_on_the_answer():
    """It exists to be counted. If something starts reading it, that is a
    decision to make out loud, not a side effect."""
    allowed = {"src/auth/users.py", "src/dashboard/api.py", "src/dashboard/aurora_html.py"}
    for p in (ROOT / "src").rglob("*.py"):
        rel = str(p.relative_to(ROOT))
        if "found_via" in p.read_text() and rel not in allowed:
            pytest.fail(f"{rel} reads the found-us answer")
    import inspect
    readers = {"twitch_callback", "kick_callback", "me", "post_found_via", "admin_onboarding"}
    for name, fn in inspect.getmembers(api, inspect.isfunction):
        if fn.__module__ != api.__name__ or name in readers:
            continue
        assert "found_via" not in inspect.getsource(fn), f"api.{name} reads the found-us answer"


def test_the_privacy_policy_says_it_is_collected():
    text = re.sub(r"<[^>]+>", " ", api.PRIVACY_HTML)
    assert "where you heard of us" in text


def test_the_growth_report_counts_without_their_words():
    src = (ROOT / "scripts" / "growth_report.py").read_text()
    assert "WHERE THEY FOUND US" in src
    assert "found_via_detail" not in src, "their own words can name a person; the report is aggregate"


# ── on every account in the admin page, and kept current ─────────────────────
# Owner, 2026-10-26: "make sure the information goes back to the admin page so
# I can see it all".

def _admin_js() -> str:
    html = api.ADMIN_HTML
    return html[html.index("<script>"):html.rindex("</script>")]


def _fn(js: str, head: str) -> str:
    body = js[js.index(head):]
    return body[:body.index("\n}\n")]


def test_the_admin_users_list_carries_every_answer(scene):
    st = scene.store
    boss = st.create("boss", "a-long-enough-password", is_admin=True)
    nova = st.create("nova", "a-long-enough-password")
    st.ask_found_via(nova["id"])
    st.set_found_via(nova["id"], "reddit", "r/passive_income")
    st.set_prefs(nova["id"], {"use_case": "clipper", "goals": ["earn_money"], "onboarded_at": 1.0})
    scene.login(boss["id"], "boss")
    rows = {u["username"]: u for u in scene.get("/admin/users").json()}
    n = rows["nova"]
    assert n["found_via"] == "reddit" and n["found_via_detail"] == "r/passive_income"
    assert n["found_via_at"] and n["found_via_asked_at"]
    assert n["prefs"]["use_case"] == "clipper" and n["prefs"]["goals"] == ["earn_money"]
    assert not rows["boss"].get("found_via"), "an account that was never asked has no answer"


def test_every_account_row_says_where_they_found_us():
    js = _admin_js()
    render = _fn(js, "function renderUsers(){")
    assert "signupLine(u)" in render, "the Users table does not show the answers"
    line = _fn(js, "function signupLine(u){")
    assert "esc(FU_LABELS[u.found_via] || u.found_via)" in line
    assert "esc(short)" in line and 'title="\' + esc(d) + \'"' in line, \
        "their own words reach the table unescaped, or uncut"
    assert "has not said where they found us yet" in line
    assert "p.use_case === 'clipper'" in line and "p.use_case === 'streamer'" in line


def test_the_details_drawer_has_the_sign_up_answers():
    js = _admin_js()
    assert "+ signupBlock(u);" in _fn(js, "async function openUser(u){")
    block = _fn(js, "function signupBlock(u){")
    for label in ("Sign-up answers", "Found us via", "Here for", "Wants"):
        assert label in block, label
    assert "esc(u.found_via_detail)" in block
    assert "OB_GOAL_LABELS[g]" in block
    assert "not asked: they signed up before the question existed" in block
    assert "not asked: no Twitch account attached" in block


def test_searching_users_finds_them_by_where_they_found_us():
    hay = _fn(_admin_js(), "function userMatches(u){")
    assert "FU_LABELS[u.found_via]" in hay and "u.found_via_detail" in hay


def test_the_admin_page_keeps_the_answers_current():
    """It has no socket, so the open tabs re-read themselves the way Accounts
    and Campaigns already do: on coming back to the window and on a timer."""
    js = _admin_js()
    assert "window.addEventListener('focus', function(){ if(onboardingOpen()) loadOnboarding(); });" in js
    assert "setInterval(function(){ if(onboardingOpen()) loadOnboarding(); }, 20000);" in js
    assert "window.addEventListener('focus', function(){ if(usersOpen()) refresh(); });" in js
    assert "setInterval(function(){ if(usersOpen()) loadUsers(); }, 60000);" in js
    users_open = _fn(js, "function usersOpen(){")
    assert "a.closest('#u-wrap')" in users_open, "a poll would close an open Grant picker"
    boot = js[js.index("refresh();\nloadAffiliates();"):]
    assert "loadOnboarding();" in boot, "the Onboarding tab's count is not read at boot"
    assert "document.getElementById('tc-onboarding').textContent" in js
