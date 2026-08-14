"""The global stream cap, and who it refuses.

max_concurrent_streams is a real resource guard: every worker holds a chat
socket, an evaluation loop and — with audio detection on — a streamlink and an
ffmpeg subprocess, on one vCPU. The cap is not the bug.

WHO IT REFUSED was. A flat `len(_streams) >= cap` is first-come-first-served,
so with cap=20 two Pro users at ten channels each fill the pool and the third
customer is refused their very first stream — while the landing page, the
pricing cards and the FAQ all sell "10 channels at once". The person turned
away is the one using nothing.
"""

import pytest

from src.dashboard import api


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api.settings, "max_concurrent_streams", 20)

    def fill(uid, n):
        for i in range(n):
            api._streams[f"{uid}:ch{i}_{uid}"] = {"channel": f"ch{i}_{uid}", "user_id": uid}
    return fill


def _refused(uid):
    try:
        api._check_server_capacity(uid)
        return None
    except api.HTTPException as e:
        return e.status_code


def test_a_new_customer_is_not_refused_because_two_heavy_users_got_there_first(pool):
    """The headline case. Two Pro users at 10 each = 18 of 20 before this even
    gets interesting; the third customer's FIRST channel must still start."""
    pool("heavy1", 9)
    pool("heavy2", 9)
    assert _refused("newcomer") is None


def test_two_pro_users_alone_are_not_cut_back(pool):
    """With cap=20 and two users the pool exactly fits what they bought, so
    fair share IS ten each and neither should be refused. Fairness must not
    invent scarcity that is not there."""
    pool("pro1", 9)
    pool("pro2", 9)
    assert _refused("pro1") is None


def test_the_heavy_user_is_the_one_cut_back_when_the_pool_is_tight(pool):
    """Three users, cap 20, so fair share is 6. Degrade onto whoever is using
    the most, not whoever happened to ask last."""
    pool("heavy", 9)
    pool("mid", 8)
    pool("light", 1)            # 18 of 20 — headroom is inside the reserve
    assert _refused("heavy") == 429


def test_the_light_user_still_gets_slots_while_the_heavy_one_waits(pool):
    """The whole point: scarcity lands on the biggest consumer, and the person
    well under their share carries on."""
    pool("heavy", 9)
    pool("mid", 8)
    pool("light", 1)
    assert _refused("light") is None


def test_plan_limits_govern_while_there_is_real_headroom(pool):
    """Fair-sharing must not kick in early — a Pro user alone on a quiet box
    gets all ten channels, which is exactly what was sold."""
    pool("solo", 9)
    assert _refused("solo") is None


def test_fair_share_does_not_apply_while_the_box_is_quiet(pool):
    """The promise-preserving half. Three users on a mostly-empty box gives a
    fair share of 6 — but a Pro customer paid for 10 and there is plenty of
    room, so they must get all 10. Fairness is a scarcity rule; applying it
    unconditionally would break the headline claim on an idle server."""
    pool("pro", 9)
    pool("small1", 1)
    pool("small2", 1)               # 11 of 20 — nowhere near tight
    assert _refused("pro") is None, \
        "fair-share kicked in with 9 slots free and cut a Pro user below plan"


def test_a_genuinely_full_pool_still_refuses_everyone(pool):
    """The guard has to still guard. Fairness is about ordering, not about
    pretending there is capacity that does not exist."""
    pool("a", 10)
    pool("b", 10)
    assert _refused("newcomer") == 503
    assert _refused("a") == 503


def test_the_refusal_says_whose_limit_it_is(pool):
    """The old message was "Server stream capacity reached", which reads as
    "you hit your plan limit" to someone who just paid for ten channels."""
    pool("a", 10)
    pool("b", 10)
    with pytest.raises(api.HTTPException) as e:
        api._check_server_capacity("newcomer")
    assert "not your plan" in str(e.value.detail).lower()


def test_capacity_pressure_is_logged_before_customers_notice(pool, capsys):
    pool("a", 9)
    pool("b", 8)                     # 17 of 20 = 85%, past the warn line
    api._check_server_capacity("newcomer")
    # capsys, not caplog: structlog writes to stdout and never reaches the
    # stdlib handlers caplog installs.
    out = capsys.readouterr().out
    assert "server_capacity_high" in out
    assert "total=17" in out and "cap=20" in out


def test_the_cap_is_env_tunable_without_a_deploy(pool, monkeypatch):
    """Raising it is a capacity decision that needs measurement on the real box,
    so it must be changeable in .env rather than requiring a code change."""
    from config.settings import Settings
    assert "max_concurrent_streams" in Settings.model_fields
    monkeypatch.setattr(api.settings, "max_concurrent_streams", 40)
    pool("a", 10); pool("b", 10)
    assert _refused("newcomer") is None, "cap did not follow the setting"


def test_a_newcomer_is_never_refused_while_any_room_exists(pool):
    """The guarantee, across every pool shape rather than one example. It holds
    because `fair` is at least 1 and a newcomer has 0, so the fair-share branch
    cannot catch them — worth asserting directly, since the arithmetic that
    makes it true is easy to break while editing."""
    for shape in ([19], [10, 9], [7, 7, 5], [5, 5, 5, 4], [1] * 19):
        api._streams.clear()
        for i, n in enumerate(shape):
            pool(f"u{i}", n)
        assert sum(len(k) > 0 for k in api._streams) == sum(shape)
        assert _refused("brand_new") is None, f"newcomer refused with pool {shape}"


def test_add_stream_actually_calls_the_capacity_check():
    """Every test above drives the helper directly, so reverting add_stream to
    a flat `len(_streams) >= cap` leaves them all green. Assert the wiring."""
    import inspect
    src = inspect.getsource(api.add_stream)
    assert "_check_server_capacity(uid)" in src, \
        "add_stream no longer routes through the fair-share check"
    assert "len(_streams) >= settings.max_concurrent_streams" not in src, \
        "the flat first-come-first-served check is back"
