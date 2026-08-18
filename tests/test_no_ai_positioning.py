"""NO AI is the hero's first claim, and the slogan stays.

The differentiator was already stated all over the page — in the meta
description, in how-it-works, in the formula section — but in the HERO it was
the third item in a row of small uppercase tags, which is where copy goes to be
skipped. It is now a badge above the slogan.

Two things are pinned here because they are the two easiest to lose:
the SLOGAN, which the owner has restored once already after it was replaced,
and the ORDER, because a claim below the fold is a claim nobody read.
"""

import pytest


@pytest.fixture
def hero():
    """Just the hero block — claims elsewhere on the page must not satisfy
    these assertions, which is the entire point of the change."""
    from src.dashboard.api import LANDING_HTML
    start = LANDING_HTML.index('<header class="wrap hero')
    return LANDING_HTML[start:LANDING_HTML.index("</header>", start)]


def test_the_slogan_is_still_the_headline(hero):
    """Restored once already after being replaced. It is the brand line."""
    assert "Never miss a" in hero and "again." in hero
    assert '<h1>' in hero and 'highlight' in hero


def test_no_ai_appears_before_the_slogan(hero):
    """Above the fold is not enough — above the HEADLINE is the ask. If this
    slips below the h1 it becomes supporting copy instead of the claim."""
    assert hero.index("NO AI") < hero.index("<h1>"), \
        "the NO AI badge fell below the slogan"


def test_the_no_ai_claim_is_a_badge_not_a_buried_tag(hero):
    """It lived in the tag row before, in 11px uppercase mono between two other
    tags. A test that only checked the words were present would have passed
    then too."""
    assert 'class="no-ai"' in hero, "the badge element is gone"
    i = hero.index('class="no-ai"')
    assert "NO AI" in hero[i:i + 400]


def test_the_claim_links_to_the_evidence(hero):
    """Saying "no AI" is worth nothing if the reader cannot immediately check
    it. The badge points at the section that shows the actual formula."""
    i = hero.index('class="no-ai"')
    tag = hero[hero.rindex("<a", 0, i):hero.index(">", i)]
    assert 'href="#formula"' in tag, "the badge does not link to the formula"


def test_the_formula_section_the_badge_points_at_exists():
    from src.dashboard.api import LANDING_HTML
    assert 'id="formula"' in LANDING_HTML, "the badge links to a section that is gone"


def test_the_lead_leads_with_it_too(hero):
    """The badge is the headline of the claim; the lead has to carry it or the
    paragraph underneath quietly contradicts the pill above it."""
    lead = hero[hero.index('class="lead"'):]
    lead = lead[:lead.index("</p>")]
    assert "No AI" in lead or "no AI" in lead


def test_the_multi_channel_pitch_survived(hero):
    """The other half of the positioning. Making room for NO AI must not have
    cost the thing that actually differentiates this for clippers."""
    assert "10 channels" in hero
    assert "clip for" in hero or "every channel" in hero


def test_the_page_does_not_claim_to_use_ai_anywhere():
    """The whole position collapses if some other section still sells AI.

    Negation-aware, because the page says "isn't powered by AI guesswork" —
    a plain substring check flags the product's own denial as a claim, which
    would make this test fire on exactly the copy it is meant to protect.
    """
    from src.dashboard.api import LANDING_HTML as h
    lowered = h.lower()
    negations = ("not ", "n't ", "no ", "never", "without", "instead of",
                 "rather than", "zero ")
    for phrase in ("ai-powered", "powered by ai", "our ai ", "ai model",
                   "machine learning", "neural network"):
        start = 0
        while True:
            i = lowered.find(phrase, start)
            if i < 0:
                break
            start = i + 1
            before = lowered[max(0, i - 40):i]
            if any(n in before for n in negations):
                continue                      # a denial, which is the point
            raise AssertionError(
                f"the landing page advertises {phrase!r}: "
                f"...{h[max(0, i - 70):i + 70]!r}...")
