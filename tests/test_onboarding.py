"""The two questions asked once, after Twitch is attached.

Owner (2026-09-21): "during the onboarding after a user attaches their twitch
I want them to ask what they want to use the site for be it for clipping or
promoting their own channel… I want them to also ask what they want in their
clips just for my own knowledge."

Only ONE of the two answers changes anything. `use_case` sets the Autopilot
mode, so a clipper's post is filled to sixty seconds from several clips and a
streamer's is one clip from their own stream. `goals` changes nothing at all
and exists to be counted in aggregate.

THE GUARDRAIL, and the reason a whole section of this file is about it:
"I still want the highlight clips to come through though for Twitch for every
user." A question that could silently switch somebody's clips off would be a
bad question to ask, so the tests below pin that nothing in the clip path
reads `use_case`, `goals` or `onboarded_at` — including for an account that
never answered.
"""

import time

import pytest

from src.auth import users as user_store


# ── the answers, normalised ─────────────────────────────────────────────────

def test_a_fresh_account_has_not_been_asked():
    p = user_store.normalize_prefs({})
    assert p["use_case"] == "" and p["goals"] == [] and p["onboarded_at"] == 0.0


def test_both_use_cases_are_accepted():
    for uc in ("clipper", "streamer"):
        assert user_store.normalize_prefs({"use_case": uc})["use_case"] == uc


def test_an_invented_use_case_falls_back_to_unasked():
    """Rather than to a default. Guessing somebody into "clipper" would
    silently change how their videos are cut."""
    assert user_store.normalize_prefs({"use_case": "wizard"})["use_case"] == ""
    assert user_store.normalize_prefs({"use_case": None})["use_case"] == ""


def test_goals_keep_only_the_known_vocabulary():
    p = user_store.normalize_prefs({"goals": ["more_clips", "free_ponies"]})
    assert p["goals"] == ["more_clips"]


def test_goals_are_de_duplicated_so_a_count_counts_people():
    p = user_store.normalize_prefs({"goals": ["automation", "automation"]})
    assert p["goals"] == ["automation"]


def test_goals_that_are_not_a_list_do_not_crash_the_account():
    for junk in ("more_clips", 7, None, {"a": 1}):
        assert user_store.normalize_prefs({"goals": junk})["goals"] == []


def test_a_nonsense_timestamp_reads_as_not_asked():
    for junk in ("soon", None, -5):
        assert user_store.normalize_prefs({"onboarded_at": junk})["onboarded_at"] == 0.0


def test_the_answers_survive_a_later_unrelated_pref_change():
    """set_prefs merges, and normalize_prefs drops unknown keys — an answer
    lost when somebody toggled a checkbox would re-ask the questions."""
    merged = user_store.normalize_prefs({
        **user_store.normalize_prefs({"use_case": "streamer",
                                      "goals": ["earn_money"],
                                      "onboarded_at": 1700.0}),
        "reduce_motion": True})
    assert merged["use_case"] == "streamer"
    assert merged["goals"] == ["earn_money"]
    assert merged["onboarded_at"] == 1700.0


# ── the answer reaches the thing that edits ─────────────────────────────────

def test_the_use_cases_are_exactly_the_edit_modes():
    """The onboarding vocabulary and the plan's are the same two words. If
    they drifted, an answer would set a mode limits_for does not know and
    every clipper would quietly be treated as a streamer."""
    from src.autopilot import plan as P
    assert set(user_store.USE_CASES) == set(P.MODES)


def test_a_clipper_gets_sixty_seconds_and_a_streamer_gets_their_own_clip():
    from src.autopilot import plan as P
    clips = [{"id": f"c{i}", "channel": "novafps"} for i in range(4)]
    sources = {f"c{i}": (f"/t/{i}.mp4", 25.0) for i in range(4)}
    clipper = P.build(clips, sources, mode="clipper")
    streamer = P.build(clips, sources, mode="streamer")
    assert len(clipper.segments) > 1
    assert P.plan_duration(clipper) == pytest.approx(60.0, abs=1.0)
    assert len(streamer.segments) == 1


def test_the_autopilot_config_takes_the_same_words():
    from src.autopilot import normalize, MODES
    assert set(MODES) == set(user_store.USE_CASES)
    for uc in user_store.USE_CASES:
        assert normalize({"mode": uc})["mode"] == uc


# ── THE GUARDRAIL: clips do not depend on any of this ───────────────────────

def _clip_path_sources() -> str:
    """Every module that decides whether a clip is made, kept or shown."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    wanted = ("src/trigger/engine.py", "src/trigger/signals.py",
              "src/trigger/suggested_clips.py", "src/trigger/viewer_clips.py",
              "src/queue/job_queue.py", "src/billing/plans.py",
              "src/ingestion/stream_worker.py")
    out = []
    for rel in wanted:
        p = root / rel
        # Named, not globbed, and asserted present: a typo'd path would make
        # this guardrail pass by reading nothing.
        assert p.exists(), f"{rel} moved — point this test at it again"
        out.append(p.read_text())
    return "\n".join(out)


def test_nothing_in_the_clip_path_reads_the_onboarding_answers():
    """Owner: "I still want the highlight clips to come through for Twitch
    for every user." The cheapest way to keep that true is for the clip path
    never to have heard of these fields."""
    body = _clip_path_sources()
    assert body, "no clip-path modules were found to check"
    for field in ("use_case", "onboarded_at", '"goals"', "'goals'"):
        assert field not in body, f"the clip path reads {field}"


def test_highlights_are_not_gated_on_having_answered():
    """A highlight is a moment a crowd clipped themselves — the strongest
    signal in the product. It ranks first whatever the account said, and
    whether or not it said anything."""
    from src.autopilot import plan as P
    clips = [{"id": "plain", "suggested": False, "virality_score": 99},
             {"id": "highlight", "suggested": True, "virality_score": 1}]
    assert P.rank(clips)[0]["id"] == "highlight"


def test_an_account_that_never_answered_still_edits():
    """The modal can be ignored forever. Everything downstream has to work
    on the empty answer, so the default mode is a real mode."""
    from src.autopilot import normalize
    from src.autopilot import plan as P
    cfg = normalize({})
    assert cfg["mode"] in P.MODES
    p = P.build([{"id": "c0", "channel": "n"}], {"c0": ("/t/0.mp4", 30.0)},
                mode=cfg["mode"])
    assert P.valid(p)[0]


def test_an_empty_use_case_is_never_passed_through_as_a_mode():
    """normalize_prefs leaves use_case "" before the questions are answered.
    limits_for has to treat that as a clipper rather than as nothing."""
    from src.autopilot import plan as P
    assert P.limits_for("") == P.limits_for("clipper")


# ── the endpoint ────────────────────────────────────────────────────────────

def test_the_endpoint_needs_a_login():
    """It writes to an account, so it is not in the open set."""
    from src.dashboard import api
    assert "/onboarding" not in api._OPEN_PATHS
    assert not any("/onboarding".startswith(p) for p in api._OPEN_PREFIXES)


def test_the_endpoint_refuses_an_answer_it_cannot_act_on():
    """A use_case outside the vocabulary would set an Autopilot mode that
    limits_for does not know. Better a 400 than a silently wrong edit."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.post_onboarding)
    assert "USE_CASES" in src and "400" in src


def test_the_endpoint_broadcasts_both_things_it_changes():
    """CLAUDE.md's realtime contract: it writes prefs AND the Autopilot
    config, so a second tab must hear about both without a refresh."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.post_onboarding)
    assert "prefs_changed" in src
    assert "autopilot_changed" in src


def test_the_server_stamps_the_time_not_the_browser():
    """It is what stops the modal reappearing. A clock-skewed browser must
    not be able to set it to 1970 or to next year."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.post_onboarding)
    assert "time.time()" in src


def test_every_event_the_endpoint_emits_has_a_frontend_handler():
    """An event with no branch in ws.onmessage is silently dropped, which is
    the same as not sending it."""
    from src.dashboard import aurora_html
    page = aurora_html.PAGE if hasattr(aurora_html, "PAGE") else ""
    if not page:
        import pathlib
        page = (pathlib.Path(aurora_html.__file__)).read_text()
    for event in ("prefs_changed", "autopilot_changed"):
        assert f"'{event}'" in page, f"no ws.onmessage branch for {event}"


# ── the modal ───────────────────────────────────────────────────────────────

def _page() -> str:
    import pathlib
    from src.dashboard import aurora_html
    return pathlib.Path(aurora_html.__file__).read_text()


def test_the_modal_waits_for_twitch():
    """The flow is "connect Twitch, then tell us what you are here for".
    Asking before there is a channel attached is asking about nothing."""
    page = _page()
    assert "const needsOnboarding" in page
    assert "me.platforms.twitch" in page
    assert "me.prefs.onboarded_at" in page


def test_the_modal_offers_both_use_cases_and_the_whole_goal_vocabulary():
    page = _page()
    for uc in user_store.USE_CASES:
        assert f"setUse('{uc}')" in page, f"no way to choose {uc}"
    for goal in user_store.GOALS:
        assert f"'{goal}'" in page, f"{goal} is not offered in the modal"


def test_the_goals_step_can_be_skipped():
    """It is for the owner's knowledge, not the user's benefit — making it
    compulsory would tax every signup for a question that changes nothing."""
    assert ">Skip<" in _page()


def test_a_failed_save_leaves_the_modal_open():
    """Closing on failure would look like it worked and lose the answer."""
    page = _page()
    assert "setErr(" in page and "rd-onb-err" in page


def test_the_flag_is_derived_rather_than_stored():
    """So it is already right after a reconnect — refetchAll re-reads /me and
    prefs_changed carries the full prefs. Its own useState would go stale on
    the deploy that drops every socket at once."""
    page = _page()
    assert "useState" not in page.split("const needsOnboarding")[1].split(";")[0]
