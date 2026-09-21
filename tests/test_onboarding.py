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


def test_the_questions_cannot_be_skipped():
    """Owner, 2026-09-21: "make it so that users cannot skip it." The goals
    step used to carry a Skip; it does not any more, and Finish stays
    disabled until at least one is picked.

    THE COST, recorded because it is real: somebody who does not want to
    answer now picks something at random to get through, so the goal counts
    carry noise they did not before. That was the owner's call to make.
    """
    page = _page()
    body = page[page.index("function OnboardingModal("):]
    body = body[:body.index("\nfunction ")]
    assert ">Skip<" not in body, "the Skip button is back"
    assert "disabled={busy || !goals.length}" in body, "Finish does not require a goal"
    assert "disabled={!useCase}" in body, "Continue does not require a use case"


def test_there_is_no_way_to_dismiss_the_modal():
    """No close button, no Escape, and a backdrop click does nothing. All
    three, because any one of them is a way out."""
    page = _page()
    body = page[page.index("function OnboardingModal("):]
    body = body[:body.index("\nfunction ")]
    assert "rd-modal-close" not in body and "onClose" not in body
    assert "'Escape'" not in body
    # The backdrop carries no click handler, unlike ClipModal's.
    head = body[body.index('className="rd-ann-bg"'):]
    assert "onClick" not in head[:head.index(">")]


def test_the_keyboard_cannot_tab_out_of_it():
    """The backdrop stops the mouse. Without a focus trap a keyboard user
    tabs straight through to the app behind it — a way out, and an
    accessibility bug for everyone who is not trying to escape."""
    page = _page()
    body = page[page.index("function OnboardingModal("):]
    body = body[:body.index("\nfunction ")]
    assert "e.key !== 'Tab'" in body
    assert "preventDefault()" in body
    assert "box.current.contains(document.activeElement)" in body


def test_the_admin_preview_is_not_a_gate():
    """The trap exists to stop users leaving the questions. Trapping the
    admin inside a preview of them would be a bug."""
    page = _page()
    body = page[page.index("function OnboardingModal("):]
    body = body[:body.index("\nfunction ")]
    assert "if (preview) return;" in body


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


# ── the admin preview (GET /onboarding) ─────────────────────────────────────
#
# Owner: "add a new /onboarding so I can test it out and see how it looks…
# I just want to be able to go through it and tinker with it." The real modal
# appears exactly once per account, which makes it the kind of screen nobody
# can look at twice without editing the database.

def test_the_preview_is_admin_only():
    """It serves the dashboard HTML. Everything else on that page is already
    behind the auth gate, but the route still checks admin against the DB
    rather than trusting the session."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.onboarding_preview)
    assert "_require_admin(request)" in src


def test_the_preview_is_not_in_the_open_set():
    from src.dashboard import api
    assert "/onboarding" not in api._OPEN_PATHS
    assert not any("/onboarding".startswith(p) for p in api._OPEN_PREFIXES)


def test_the_get_preview_and_the_post_are_different_things():
    """Same path, two methods. The POST is the real save and is untouched by
    the preview existing."""
    from src.dashboard import api
    methods = {}
    for r in api.app.routes:
        if getattr(r, "path", "") == "/onboarding":
            methods.update({m: r.endpoint.__name__ for m in (r.methods or set())})
    assert methods.get("GET") == "onboarding_preview"
    assert methods.get("POST") == "post_onboarding"


def test_the_preview_serves_the_real_page_not_a_copy():
    """A mock-up of an onboarding screen drifts from the onboarding screen
    within a week, and then looking at it tells you nothing."""
    import inspect
    from src.dashboard import api
    assert "DASHBOARD_HTML" in inspect.getsource(api.onboarding_preview)


def test_the_preview_saves_nothing():
    """Tinkering must not move the admin's own Autopilot mode every lap."""
    page = _page()
    body = page[page.index("function OnboardingModal("):]
    body = body[:body.index("\nfunction ")]
    assert "if (preview) { setStep(3); return; }" in body, \
        "preview does not short-circuit before the POST"
    # The short-circuit has to come BEFORE the fetch, or it saves anyway.
    assert body.index("if (preview)") < body.index("method: 'POST'")


def test_the_preview_can_be_walked_more_than_once():
    page = _page()
    assert "const restart =" in page and ">Start over<" in page


def test_the_preview_opens_the_modal_whatever_the_account_answered():
    page = _page()
    assert "location.pathname === '/onboarding'" in page
    assert "onbPreview ||" in page, "the preview does not override the once-only gate"


def test_the_preview_says_it_is_a_preview():
    """So a screenshot of it is never mistaken for the real flow, and so it
    is obvious nothing was written."""
    page = _page()
    assert "Preview — nothing is saved" in page
    assert "Preview — nothing was saved" in page


# ── tracking it in the admin portal ─────────────────────────────────────────

def _admin_html() -> str:
    from src.dashboard.api import ADMIN_HTML
    return ADMIN_HTML


def test_the_admin_endpoint_is_admin_only():
    import inspect
    from src.dashboard import api
    assert "_require_admin(request)" in inspect.getsource(api.admin_onboarding)


def test_asked_and_answered_are_reported_separately():
    """A completion rate over EVERY account would mostly be measuring how
    many people have connected Twitch — nobody without it has seen the
    questions. Conflating the two hides which problem you have."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.admin_onboarding)
    assert '"asked"' in src and '"answered"' in src
    assert "twitch_id" in src, "asked is not gated on having reached the modal"
    assert "/ asked" in src, "completion is not measured against asked"


def test_completion_does_not_divide_by_zero_on_a_fresh_install():
    import inspect
    from src.dashboard import api
    assert "if asked else 0.0" in inspect.getsource(api.admin_onboarding)


def test_the_admin_payload_carries_both_the_split_and_the_goals():
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.admin_onboarding)
    assert '"use_cases"' in src and '"goals"' in src
    assert "USE_CASES" in src and "GOALS" in src, \
        "the buckets are not built from the stored vocabulary"


def test_the_admin_rows_say_what_the_answer_actually_did():
    """"clipper" means nothing without knowing plan.py. The resolved
    Autopilot mode is what makes the row readable."""
    import inspect
    from src.dashboard import api
    assert '"mode"' in inspect.getsource(api.admin_onboarding)


def test_the_admin_page_has_an_onboarding_tab_wired_to_a_panel():
    """A tab with no panel switches to a blank screen; a panel with no
    loader shows Loading for ever."""
    html = _admin_html()
    assert 'data-tab="onboarding"' in html
    assert 'id="panel-onboarding"' in html
    assert "if(b.dataset.tab === 'onboarding') loadOnboarding();" in html
    assert "async function loadOnboarding()" in html


def test_the_admin_tab_reads_the_real_endpoint():
    assert "api('/admin/onboarding')" in _admin_html()


def test_the_admin_tab_labels_the_goals_in_english():
    """The stored vocabulary is snake_case keys. A table of `earn_money` is
    a table nobody reads."""
    html = _admin_html()
    assert "OB_GOAL_LABELS" in html
    for goal in user_store.GOALS:
        assert goal + ":" in html, f"{goal} has no label in the admin table"


def test_the_admin_tab_explains_which_answer_changes_anything():
    """Whoever reads this panel in six months needs to know that the split
    drives the edit and the goals drive nothing."""
    html = _admin_html()
    panel = html[html.index('id="panel-onboarding"'):]
    panel = panel[:panel.index("</div>\n\n  <!--")] if "</div>\n\n  <!--" in panel else panel[:4000]
    assert "Autopilot mode" in panel
    assert "sixty seconds" in panel or "60 seconds" in panel


# ── telling the user what the answer does ───────────────────────────────────
#
# Owner (2026-09-21): "Make a notification that these answers will help the
# bot with providing accurate expectations." The questions are compulsory
# now, so somebody who was just made to answer two of them should see the
# product change rather than only the modal disappear.

def test_choosing_shows_what_will_happen_before_they_commit():
    """The expectation appears on the card the moment a use case is picked,
    not only after saving — the answer should visibly do something while it
    can still be changed."""
    page = _page()
    body = page[page.index("function OnboardingModal("):]
    body = body[:body.index("\nfunction ")]
    assert "Posts will run a full 60 seconds" in body
    assert "Posts will run as long as the moment does" in body


def test_finishing_says_what_the_bot_will_now_do():
    page = _page()
    assert "const ONB_EXPECT" in page
    for uc in user_store.USE_CASES:
        assert uc + ":" in page.split("const ONB_EXPECT")[1][:400], \
            f"no expectation message for {uc}"
    assert "if (expect) flash(expect);" in page, "the message is never shown"


def test_the_expectation_matches_what_the_builder_actually_does():
    """The copy promises a clipper a full 60 seconds and a streamer their own
    length. If limits_for stopped doing that, this message would become a
    lie told to every new account."""
    from src.autopilot import plan as P
    clips = [{"id": f"c{i}", "channel": "n"} for i in range(4)]
    sources = {f"c{i}": (f"/t/{i}.mp4", 25.0) for i in range(4)}
    clipper = P.build(clips, sources, mode="clipper")
    streamer = P.build(clips, sources, mode="streamer")
    assert P.plan_duration(clipper) == pytest.approx(60.0, abs=1.0), \
        "the clipper message promises 60 seconds"
    assert len(streamer.segments) == 1, \
        "the streamer message promises one moment per video"


def test_no_copy_claims_the_goals_change_what_the_bot_does():
    """THE HONESTY CONSTRAINT. use_case really does steer the edit. `goals`
    steer NOTHING — they are product research — and this test exists because
    the easy way to make a compulsory question feel worth answering is to
    imply it tunes something it does not touch.

    If goals are ever wired into the builder, delete this test rather than
    working around it.
    """
    from src.autopilot import llm_common, plan
    for mod in (plan, llm_common):
        import inspect
        assert '"goals"' not in inspect.getsource(mod), \
            f"{mod.__name__} now reads goals — the copy may tell the truth now"

    page = _page()
    body = page[page.index("function OnboardingModal("):]
    body = body[:body.index("\nfunction ")]
    # The goals step's own prose must not promise the bot anything.
    step2 = body[body.index("What do you want out of it?"):]
    step2 = step2[:step2.index("rd-onb-foot")]
    for claim in ("the bot", "your clips will", "we will edit"):
        assert claim.lower() not in step2.lower(), \
            f"the goals step claims {claim!r}, which is not true"
