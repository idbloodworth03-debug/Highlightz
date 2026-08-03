"""Asking users for a review.

Two things here are not merely bugs if they break — they are the kind of wrong
that costs trust or gets a review profile removed:

  * A prompt that reappears after someone declined.
  * Selecting WHO gets asked based on how happy they seem (review gating).
    Google and Trustpilot both prohibit it and Trustpilot pulls profiles for it.

Most of the file is about those.
"""

import json
import time

import pytest

from src.feedback import reviews


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(reviews, "_INDEX", tmp_path / "reviews.json")
    reviews._reviews.clear()
    reviews._loaded = False
    yield
    reviews._reviews.clear()


# ── when we ask ──────────────────────────────────────────────────────────────

def test_nobody_is_asked_before_the_first_milestone():
    """At signup a user has no opinion worth collecting."""
    assert reviews.should_prompt({}, 0) is False
    assert reviews.should_prompt({}, 24) is False
    assert reviews.should_prompt({}, 25) is True


def test_the_trigger_is_a_clip_count_and_nothing_about_sentiment():
    """REVIEW GATING GUARD. If this ever takes an approval RATE, a rejection
    count, or any other proxy for whether the user is happy, we are selecting
    for positive reviews — prohibited by Google and Trustpilot."""
    import inspect
    src = inspect.getsource(reviews.should_prompt)
    for banned in ("rate", "ratio", "sentiment", "score", "happy", "rejected"):
        assert banned not in src.lower().replace("approved_clips", ""), \
            f"should_prompt looks at {banned!r} — that is review gating"


def test_declining_permanently_is_permanent():
    user = {"review_prompt": reviews.mark_never({})}
    assert reviews.should_prompt(user, 25) is False
    assert reviews.should_prompt(user, 10_000) is False, \
        "'don't ask again' must survive every later milestone"


def test_not_now_silences_the_prompt_for_a_month():
    user = {"review_prompt": reviews.mark_snoozed({}, 25)}
    assert reviews.should_prompt(user, 25) is False
    later = time.time() + reviews.SNOOZE_S + 60
    assert reviews.should_prompt(user, 25, now=later) is False, \
        "snooze expiring must not re-ask at the SAME milestone"


def test_a_snoozed_user_is_asked_again_at_the_next_milestone():
    """Someone who said 'not now' at 25 clips and is still here at 150 is worth
    asking once more — but at a new milestone, not the moment the timer runs
    out."""
    user = {"review_prompt": reviews.mark_snoozed({}, 25)}
    later = time.time() + reviews.SNOOZE_S + 60
    assert reviews.should_prompt(user, 150, now=later) is True


def test_someone_who_already_reviewed_is_never_asked_again():
    user = {"review_prompt": reviews.mark_submitted({})}
    assert reviews.should_prompt(user, 25) is False
    assert reviews.should_prompt(user, 10_000) is False


def test_being_shown_the_prompt_records_the_milestone():
    """Without this the prompt reopens on every page load at 25 clips."""
    state = reviews.mark_shown({}, 30)
    assert state["last_milestone"] == 25
    assert reviews.should_prompt({"review_prompt": state}, 30) is False


# ── what we store ────────────────────────────────────────────────────────────

def test_a_rating_outside_one_to_five_is_refused():
    for bad in (0, 6, -1):
        with pytest.raises(ValueError):
            reviews.add("u", "user", bad, "hi")


def test_a_review_is_private_unless_the_user_ticks_the_box():
    r = reviews.add("u", "user", 5, "great", publish_consent=False,
                    display_name="Dave")
    assert r.publish_consent is False
    assert r.display_name == "", \
        "a name was kept for someone who did not consent to being shown"
    assert reviews.published() == []


def test_consent_alone_is_not_enough_to_publish():
    """An admin still reads it first. Consent plus approval, not either."""
    r = reviews.add("u", "user", 5, "great", publish_consent=True, display_name="Dave")
    assert reviews.published() == []
    reviews.set_approved(r.id, True)
    assert [x.id for x in reviews.published()] == [r.id]


def test_the_public_shape_leaks_no_identity_beyond_the_chosen_name():
    r = reviews.add("secret-uid", "realname", 4, "good",
                    publish_consent=True, display_name="Dave")
    reviews.set_approved(r.id, True)
    pub = reviews.published()[0].public()
    assert pub["name"] == "Dave"
    blob = json.dumps(pub)
    assert "secret-uid" not in blob and "realname" not in blob


def test_an_anonymous_publisher_gets_a_neutral_label():
    r = reviews.add("u", "user", 4, "good", publish_consent=True, display_name="")
    reviews.set_approved(r.id, True)
    assert reviews.published()[0].public()["name"] == "Highlightz user"


def test_the_average_covers_only_what_a_visitor_can_actually_read():
    """This number would feed schema.org aggregateRating. Averaging private
    reviews in describes something nobody can see, which is exactly what
    structured-data penalties are for."""
    a = reviews.add("u1", "a", 5, "", publish_consent=True); reviews.set_approved(a.id, True)
    b = reviews.add("u2", "b", 3, "", publish_consent=True); reviews.set_approved(b.id, True)
    reviews.add("u3", "c", 1, "", publish_consent=False)      # private 1-star
    assert reviews.aggregate() == {"count": 2, "average": 4.0}


def test_no_published_reviews_reports_zero_not_a_flattering_default():
    assert reviews.aggregate() == {"count": 0, "average": 0.0}


def test_deleting_an_account_takes_its_reviews():
    """A published quote from a deleted account is someone's name on a
    marketing page with no way left to withdraw it."""
    r = reviews.add("leaving", "x", 5, "hi", publish_consent=True)
    reviews.set_approved(r.id, True)
    reviews.add("staying", "y", 4, "ok")
    assert reviews.delete_all_for_user("leaving") == 1
    assert reviews.published() == []
    assert len(reviews.all_reviews()) == 1


def test_an_over_long_comment_is_refused_before_storage():
    with pytest.raises(ValueError):
        reviews.add("u", "user", 5, "x" * (reviews.COMMENT_MAX + 1))


def test_a_corrupt_index_does_not_take_every_review_down():
    reviews._INDEX.write_text("[not json", encoding="utf-8")
    reviews._reviews.clear(); reviews._loaded = False
    assert reviews.all_reviews() == []


def test_dismissing_records_the_milestone_even_if_it_was_never_marked_shown():
    """The prompt reaches a user two ways — the live broadcast (which marks it
    shown) and the flag on /me for a tab opened later (which does not). If the
    dismissal did not record the milestone itself, anyone who saw it the second
    way and pressed 'not now' would be re-asked at the SAME milestone a month
    later."""
    state = reviews.mark_snoozed({}, 25)          # never marked shown first
    assert state["last_milestone"] == 25
    later = time.time() + reviews.SNOOZE_S + 60
    assert reviews.should_prompt({"review_prompt": state}, 25, now=later) is False


def test_a_snooze_holds_even_when_a_new_milestone_is_reached():
    """The case the other snooze test could not catch: someone says "not now"
    at 25 clips and blasts past 150 the same fortnight. The new milestone alone
    must not override a month they explicitly asked for.

    Written after a mutation check — deleting the snooze branch entirely left
    every other test green, because the last_milestone guard happened to cover
    the same scenario."""
    user = {"review_prompt": reviews.mark_snoozed({}, 25)}
    assert reviews.should_prompt(user, 150) is False, "snooze overridden by a milestone"
    later = time.time() + reviews.SNOOZE_S + 60
    assert reviews.should_prompt(user, 150, now=later) is True, \
        "and it must resume once the month is up"


# ── the admin panel ──────────────────────────────────────────────────────────

def _reviews_script() -> str:
    import re
    from src.dashboard.api import ADMIN_HTML
    blocks = [b for b in re.findall(r"<script>(.*?)</script>", ADMIN_HTML, re.S)
              if "rvEsc" in b]
    assert blocks, "the reviews script is not in the admin page"
    return blocks[0]


def test_the_admin_reviews_script_actually_parses():
    """ADMIN_HTML is a Python triple-quoted string, so a JS escape is eaten by
    Python before the browser sees it. The first version of this block used
    onclick="rvApprove(<escaped quote>ID<escaped quote>)" and arrived as
    rvApprove(''), a SyntaxError that killed the whole script — the section sat
    on "Loading..." forever and nothing in the test suite noticed."""
    import subprocess, tempfile, shutil
    node = shutil.which("node")
    if not node:                                    # pragma: no cover
        import pytest as _p
        _p.skip("node not available")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(_reviews_script())
        path = fh.name
    r = subprocess.run([node, "--check", path], capture_output=True, text=True)
    assert r.returncode == 0, f"admin reviews JS does not parse:\n{r.stderr}"


def test_the_admin_reviews_script_contains_no_backslashes():
    """The structural fix, not just the symptom: with zero backslashes in the
    block there is nothing for Python to eat. Inline handlers were replaced by
    data- attributes and one delegated listener to make that possible."""
    # Strip // comments first: the block documents the original bug and names
    # onclick in prose, which is not the same as using one.
    js = "\n".join(l for l in _reviews_script().splitlines()
                   if not l.strip().startswith("//"))
    assert "\\" not in js, "a backslash is back in the admin reviews JS"
    assert "onclick=" not in js, "inline onclick reintroduces the quoting problem"


def test_the_admin_panel_never_offers_to_publish_without_consent():
    """Consent is the user's decision and must not be overridable from the
    admin panel — a button there invites putting someone's words on the public
    site by accident."""
    js = _reviews_script()
    assert "r.publish_consent" in js and "no consent" in js


def test_admin_reviews_output_is_escaped():
    """Comments are user-written text rendered via innerHTML."""
    js = _reviews_script()
    assert "function rvEsc(" in js and "textContent" in js
    for field in ("r.comment", "r.display_name", "r.username", "r.id"):
        assert f"rvEsc({field}" in js or f"rvEsc({field} " in js, \
            f"{field} reaches innerHTML unescaped"
