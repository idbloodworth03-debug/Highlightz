"""While the editor and Scheduler are held back, the landing page says so.

Owner, 2026-09-19: "close off the editor and schedule for now and say coming
soon on the landing page."

WHAT THIS IS GUARDING. `UPLOADS_ENABLED=false` already takes both tabs away —
`_require_upload_access` answers 503 for everyone but admins. The danger is
the half of the change nobody sees in testing: a landing page that goes on
selling them. A plan row reading "Yes" for a tab that refuses, or a share
card promising "auto-posting built in", is the kind of claim a customer is
entitled to hold you to, and the share card is often the ONLY thing they
read.

Every one of these is derived from the flag rather than typed, so flipping
UPLOADS_ENABLED back and restarting restores the marketing in one move. Both
directions are tested, because a held-back page that stays held back after
launch is the same bug pointing the other way.
"""

import importlib
import re

import pytest


def landing(flag: bool) -> str:
    """The landing page as it is built at import with the flag either way.
    Rebuilt from source because LANDING_HTML is assembled once, at import."""
    from config.settings import settings
    import src.dashboard.api as api
    was = settings.uploads_enabled
    settings.uploads_enabled = flag
    try:
        return importlib.reload(api).LANDING_HTML
    finally:
        settings.uploads_enabled = was
        importlib.reload(api)


@pytest.fixture(scope="module")
def held():
    return landing(False)


@pytest.fixture(scope="module")
def live():
    return landing(True)


# ── held back ────────────────────────────────────────────────────────────────

def test_the_two_sections_are_tagged_coming_soon(held):
    assert held.count("Coming soon") == 2, "the editor and the Scheduler each say it"
    assert "Clip Editor &middot; Coming soon" in held or "Clip Editor · Coming soon" in held


def test_the_plan_rows_say_soon_instead_of_yes(held):
    """A pricing table is a promise about today. Two rows — the editor and
    the Scheduler — on the one plan that includes them."""
    assert held.count("<b>Soon</b>") == 2
    rows = re.findall(r"<span>(Clip Editor|Scheduler)</span><b>(\w+)</b>", held)
    assert ("Clip Editor", "Yes") not in rows and ("Scheduler", "Yes") not in rows
    # Free still says No: the flag says WHEN, the plan says WHO, and a plan
    # that never included it must not start advertising it as coming.
    assert ("Clip Editor", "No") in rows


def test_the_button_stops_inviting_people_in(held):
    assert "Open the editor" not in held
    assert "Start free" in held


def test_nothing_in_the_head_still_sells_it_as_built_in(held):
    """The share card and the description are what a link preview quotes."""
    assert "auto-posting built in" not in held
    assert "posts it for you. Chat spikes" not in held
    for tag in ('property="og:description"', 'name="twitter:description"',
                'name="description"'):
        m = re.search(r"<meta [^>]*" + re.escape(tag) + r"[^>]*>", held)
        assert m and "coming soon" in m.group(0).lower(), f"{tag} still promises it"


def test_the_structured_data_agrees_with_the_page(held):
    """Search engines quote the JSON-LD, not the body copy."""
    blob = re.search(r'"@type": "WebSite".*?"inLanguage"', held, re.S)
    assert blob and "coming soon" in blob.group(0)


def test_the_faq_says_which_of_the_three_is_live(held):
    assert "The VOD Scanner is live today" in held
    assert "open to Pro accounts shortly" in held


def test_the_feature_cards_are_still_there(held):
    """Held back is not deleted. The cards are what make somebody want it;
    only the claim that they can have it today had to go."""
    assert "Five templates" in held
    assert "Connect once" in held
    assert "edit-grid" in held


# ── and back again ───────────────────────────────────────────────────────────

def test_flipping_the_flag_restores_the_marketing(live):
    """The other half of the bug: a page that stays coy after launch."""
    assert "Coming soon" not in live
    assert "<b>Soon</b>" not in live
    assert "Open the editor" in live
    assert "auto-posting built in" in live
    assert live.count("<b>Yes</b>") >= 1


def test_the_api_refuses_while_the_page_says_soon():
    """The page and the gate must be the same switch, or one of them lies."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api._require_upload_access)
    assert "settings.uploads_enabled" in src
    assert "coming soon" in src.lower()
