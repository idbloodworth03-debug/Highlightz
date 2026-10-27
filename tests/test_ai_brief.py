"""What an AI answer about the site is built from (owner, 2026-10-27: an AI
summary of highlightz.app was "not accurate ... make sure everything lines up
so when people search up stuff like this they get the correct answers").

The summary was the site's own copy, read by a model. Where it went wrong, the
copy had let it:
  1. Campaigns came out as only something a streamer pays for. /llms.txt and
     /llms-full.txt never mentioned them, so nothing said a clipper can join
     one free, on any plan, how to enter, or how winners are paid. They now
     do, under the same switches as the blog guide, with the fee and the
     budget range read from the settings that price a real request.
  2. "Sudden silence": the landing FAQ listed the signals as "... viewer
     movement and silence", and the brief named them without saying what
     they are. The signal is the quiet BEFORE an eruption.
  3. The brief said only a Twitch broadcaster can opt out; the opt-out page
     verifies Kick broadcasters too wherever Kick sign-in is set up.
  4. /llms-full.txt opened mid-sentence ("from Twitch, is private to the
     account ...") whenever uploads were on, as in production: a conditional
     expression swallowed the opening sentences.
"""

import asyncio
import re

import pytest

from src.dashboard import api
from src.dashboard import blog_content as B
from src.dashboard.promote_html import _fee_text, _range_text


@pytest.fixture
def flags(monkeypatch):
    def set_(campaigns=True, requests=True, uploads=True, kick=True, pct=5.0, flat=0.0,
             lo=100.0, hi=50000.0):
        s = api.settings
        monkeypatch.setattr(s, "campaigns_enabled", campaigns)
        monkeypatch.setattr(s, "campaign_requests_enabled", requests)
        monkeypatch.setattr(s, "uploads_enabled", uploads)
        monkeypatch.setattr(s, "campaign_fee_pct", pct)
        monkeypatch.setattr(s, "campaign_flat_fee_usd", flat)
        monkeypatch.setattr(s, "campaign_min_budget_usd", lo)
        monkeypatch.setattr(s, "campaign_max_budget_usd", hi)
        monkeypatch.setattr(s, "kick_client_id", "kick-id" if kick else "")
        monkeypatch.setattr(s, "kick_client_secret", "kick-secret" if kick else "")
    return set_


def brief() -> str:
    return asyncio.run(api.llms_txt())


def full() -> str:
    return asyncio.run(api.llms_full_txt())


def flat(s: str) -> str:
    """Line wraps (and blockquote markers) out, so a test reads a sentence
    however the file wraps it."""
    return re.sub(r"\s+", " ", s.replace("\n> ", "\n"))


def section(text: str, heading: str) -> str:
    at = text.index(heading)
    nxt = text.find("\n## ", at + len(heading))
    return text[at:] if nxt < 0 else text[at:nxt]


# ── 1. campaigns, in both files ─────────────────────────────────────────────

def test_the_brief_tells_a_clipper_how_to_join_enter_and_get_paid(flags):
    flags()
    b = brief()
    camp = flat(section(b, "## Highlightz Campaigns"))
    for said in ("Free to join, on any plan, the Free plan included",
                 "open the Campaigns tab and join one",
                 "Post it anywhere and add its link on the campaign page (any plan)",
                 "or post it straight from Highlightz (Pro)",
                 "A tie goes to whoever posted first",
                 "PayPal, Venmo, Cash App"):
        assert said in camp, f"the campaigns section lost: {said!r}"
    # Where a model looks first: the summary at the top and who it is for.
    top = flat(b[:b.index("## What it does")])
    assert "Highlightz Campaigns" in top and "Free included" in top
    assert "Clippers can also join Highlightz Campaigns, free on any plan" in flat(b)
    assert "(https://highlightz.app/blog/" + B.CAMPAIGNS_SLUG + ")" in b
    assert "(https://highlightz.app/promote)" in b


def test_the_streamers_half_reads_the_price_from_the_settings(flags):
    flags(pct=7.5, lo=250.0, hi=20000.0)
    camp = flat(section(brief(), "## Highlightz Campaigns"))
    assert "7.5% of the budget" in camp and "$250 to $20,000" in camp
    assert _fee_text() in camp and _range_text() in camp
    assert "fee of 5%" not in camp
    assert "prizes nobody wins are refunded" in camp and "No number of entries or views" in camp
    flags(flat=25.0)
    assert "$25 per campaign plus 5% of the budget" in flat(brief())


def test_each_half_follows_its_own_switch(flags):
    flags(campaigns=False, requests=False)
    b = brief()
    assert "Campaigns" not in b and "/promote" not in b and B.CAMPAIGNS_SLUG not in b

    # The marketplace open, requests closed: the clippers' half and its guide;
    # no price, and no link to a /promote that answers 404.
    flags(campaigns=True, requests=False)
    b = brief()
    assert "For clippers:" in b and "For streamers:" not in b
    assert "/promote" not in b and "% of the budget" not in b
    assert "Streamers can fund a campaign" not in b

    # Requests open, marketplace closed: nothing may invite a clipper to join,
    # and nothing may link the guide, which 404s while campaigns are off.
    flags(campaigns=False, requests=True)
    b = brief()
    assert "For streamers:" in b and "For clippers:" not in b
    assert "join" not in flat(section(b, "## Highlightz Campaigns")).lower()
    assert "Free included" not in b and "Clippers can also join" not in b
    assert B.CAMPAIGNS_SLUG not in b and "(https://highlightz.app/promote)" in b


def test_entering_says_what_posting_from_highlightz_needs(flags):
    flags(uploads=False)
    camp = flat(section(brief(), "## Highlightz Campaigns"))
    assert "posting straight from Highlightz opens on Pro" in camp
    assert "or post it straight from Highlightz (Pro)" not in camp


def test_the_full_copy_carries_the_campaigns_guide(flags):
    flags()
    f = full()
    art = B.campaigns_article()
    assert f"## {art.title} (from /blog/{B.CAMPAIGNS_SLUG})" in f
    for kind, val in art.blocks:
        if kind == "h":
            assert f"### {val}" in f, f"guide section missing: {val}"
    camp = flat(section(f, f"## {art.title}"))
    assert "Joining is free, on any plan, the Free plan included" in camp
    assert "Posted from: Highlightz on Pro, or anywhere else and added by its link" in camp
    assert _fee_text() in camp
    # A file read on its own: its links have to work outside the site.
    assert "](/" not in camp and "(https://highlightz.app/promote)" in camp
    flags(campaigns=False)
    assert "Highlightz Campaigns" not in full()


def test_the_guide_s_search_snippet_does_not_send_clippers_to_pro(flags):
    """The meta description is what a search result shows. It said clippers
    post "from Highlightz", which is Pro; a post made anywhere counts."""
    flags()
    d = B.campaigns_article().description
    assert "from Highlightz" not in d and "join free on any plan" in d


def test_the_landing_says_clipping_for_a_campaign_is_free(flags):
    flags()
    html = api.render_landing()
    block = html[html.index('<div class="edit" id="campaigns">'):]
    assert "<h3>Clippers play for the pool</h3><p>Free on any plan. Join a campaign" in block


def test_no_page_says_only_a_streamer_funds_the_pool(flags):
    """The first campaign, for Duxxion, is paid for by Highlightz (owner,
    2026-10-27), so "a streamer puts up a prize pool" is wrong as a
    description of campaigns. Everywhere a clipper or a model reads about
    them says the pool comes from the streamer or from Highlightz itself.
    (The streamer-facing pitch, "Put up a prize pool" on the landing page and
    /promote, is addressed to a streamer asking for one and stays.)"""
    flags()
    b, f = flat(brief()), full().splitlines()[2]
    art = B.campaigns_article()
    find = next(a for a in B.articles() if a.slug == "find-streamer-campaigns")
    fits = dict(B.highlightz_points())["Campaigns with prize pools"]
    html = api.render_landing()
    card = html[html.index("<h3>Clippers play for the pool</h3>"):]
    card = card[:card.index("</div>")]
    surfaces = {
        "llms.txt summary": flat(b[:b.index("The full public copy")]),
        "llms.txt section": flat(section(b, "## Highlightz Campaigns")),
        "llms-full opening": f,
        "guide": art.blocks[1][1],
        "guide snippet": art.description,
        "find-campaigns guide": next(t for k, t in find.blocks if k == "p" and "Highlightz runs" in t),
        "where Highlightz fits": fits,
        "landing clipper card": card,
    }
    for name, text in surfaces.items():
        assert re.search(r"streamer or (by |from )?Highlightz", text), \
            f"{name} does not say Highlightz can fund the pool: {text[:160]!r}"
        low = text.lower()
        for wrong in ("a streamer puts up a prize pool", "streamers put up prize pools",
                      "the streamer puts up the pool"):
            assert wrong not in low, f"{name} still says {wrong!r}"
    assert "its first one included" in art.blocks[1][1] and "its first one included" in surfaces["llms.txt section"]
    # The same line in every guide's "Where Highlightz fits" also said an
    # entry had to be posted from Highlightz (Pro) and never that joining is
    # free.
    assert "anywhere else with its link added" in fits and "Joining is free on any plan" in fits


# ── 2. the signals, described rather than named ─────────────────────────────

def test_every_signal_is_described_in_both_files(flags):
    flags()
    b, f = brief(), full()
    caught = api._catches()
    assert len(caught) == 7
    for _key, title, line in caught:
        assert f"  - {title}: {line}" in b, f"llms.txt names {title} without saying what it is"
        assert f"- {title}: {line}" in f, f"llms-full.txt names {title} without saying what it is"
    for text in (b, f):
        assert "sudden silence" not in text.lower()


def test_the_faq_says_the_silence_breaks_into_chaos():
    faq = dict(api._landing_faq_pairs())
    a = faq["What does Highlightz actually do?"]
    assert a.startswith("It watches a live Twitch or Kick channel for you.")
    assert "a quiet moment that breaks into chaos" in a
    assert "viewer movement" not in a and "and silence —" not in a
    # It reaches the structured data Google reads, not only the page.
    assert "a quiet moment that breaks into chaos" in api._faq_schema(api.LANDING_HTML)


# ── 3. the opt-out ──────────────────────────────────────────────────────────

def test_the_opt_out_names_kick_wherever_kick_can_verify(flags):
    flags(kick=True)
    b = flat(brief())
    assert "Any Twitch or Kick broadcaster can opt their channel out" in b
    assert "any Twitch or Kick streamer can remove their channel" in b
    # Without Kick sign-in the page has no Kick button, so the brief may not
    # promise one.
    flags(kick=False)
    b = flat(brief())
    assert "Any Twitch broadcaster can opt their channel out" in b
    assert "Twitch or Kick streamer" not in b


# ── 4. the opening of the full copy ─────────────────────────────────────────

@pytest.mark.parametrize("uploads", [True, False])
def test_the_full_copy_opens_with_the_whole_description(flags, uploads):
    flags(uploads=uploads)
    lines = full().splitlines()
    assert lines[0] == "# Highlightz — the full public copy" and lines[1] == ""
    lead = lines[2]
    assert lead.startswith("> Automatic Twitch and Kick clipping. Highlightz watches a live channel"), lead[:120]
    for said in ("is private to the account that made it. It is what the Clip Editor",
                 "Kick channels are monitored the same way",
                 "Highlightz Campaigns",
                 "Operated by ANTI Technology LLC. Short brief: https://highlightz.app/llms.txt"):
        assert said in lead, f"uploads={uploads}: the opening lost {said!r}"
    if uploads:
        assert "not yet open to accounts" not in lead
    else:
        assert "not yet open to accounts" in lead
