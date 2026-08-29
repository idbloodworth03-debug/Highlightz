"""The NO AI position, after the hero that carried it was removed.

WHAT CHANGED. This file used to pin a badge sitting directly above the
slogan — "NO AI | A formula you can read" — plus the slogan itself, the
lead, and the order of the two. The owner removed the hero's entire lede
(kicker, badge, slogan, lead, both CTAs), so all of that markup is gone and
the assertions that described it were deleted rather than loosened: a test
kept alive by weakening it until it passes is worse than no test.

WHAT STILL HAS TO HOLD, and is pinned below:

  1. The page never claims to use AI. This is the assertion that was always
     doing the real work, and nothing about the hero affects it.
  2. The claim is still MADE, in the section that carries the argument
     (#how), not only in the FAQ. That is the part the badge used to
     guarantee by being above the fold.

KNOWN LOSS, recorded on purpose. The differentiator is no longer visible
before a scroll. The badge was the only thing stating it above the fold, and
removing the hero removed it. That was the owner's call; this note exists so
the next person reads it as a decision rather than an accident.
"""

import pytest


def test_the_claim_is_made_in_the_argument_not_only_the_faq():
    """The badge used to put this above the fold. With it gone, the floor is
    that the page still says it where it makes its case — a differentiator
    that appears only in an FAQ answer is one most visitors never meet."""
    from src.dashboard.api import LANDING_HTML as h
    how = h[h.index('id="how"'):h.index('id="features"')]
    low = how.lower()
    assert "no black box" in low or "not ai" in low or "formula" in low, \
        "the how-it-works section no longer states what the detector is"
    # and the FAQ still answers it directly for anyone who looks
    faq = h[h.index('id="faq"'):]
    assert "is this ai?" in faq.lower(), "the FAQ dropped the AI question"


def test_the_formula_section_the_claim_points_at_exists():
    """#how is where the claim is cashed: it shows the signals and the score.
    If that anchor ever disappears the claim has nothing standing behind it."""
    from src.dashboard.api import LANDING_HTML
    assert 'id="how"' in LANDING_HTML


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
