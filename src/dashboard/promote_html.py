"""Renders /promote: Campaigns, for streamers (owner, 2026-10-23).

"I want them to explain everything that they want and I need this to all be
explained on a new tab in the landing page. Make it a campaign tab that is on
the landing page that basically explains the whole deal with everything and
let the streamers be able to request a campaign for their streams after they
sign up with an account (which they can do for free)."

The streamer's side of the marketplace: what a campaign is, what they control,
what it costs, and what happens to the money. The request itself is made in the
dashboard (the Promote screen), after a free sign-up; this page is the pitch
and the plain-words version of Terms Section 4A.

EVERY NUMBER IS READ, NEVER TYPED. The fee, the percentage and the budget range
come from settings through campaigns.requests.pricing() and quote(), the same
functions that price a real request, so the page cannot quote a price the
checkout will not charge. The calculator repeats quote()'s arithmetic in whole
cents, rounding the percentage half up, and a test holds the two together.

EVERY CLAIM IS ABOUT WHAT IS BUILT. Winners are judged and paid by Highlightz
after a campaign ends, by hand for now; the page says who does it and when, and
nothing about automatic leaderboards or view tracking, which do not exist yet.

Same design system as /compare (tutorial_html.BASE_CSS) and the same two rules:
no f-strings around CSS or JS, and no backslashes in the embedded JS.
"""

from __future__ import annotations

import json
import re
from html import escape

from src.campaigns import requests as creq
from src.dashboard.tutorial_html import BASE_CSS

_TITLE = "Campaigns for streamers: get your stream clipped — Highlightz"
_DESC = ("Fund a clipping campaign and clippers compete to post the best moments of "
         "your Twitch or Kick streams to TikTok, YouTube Shorts and Instagram. You set "
         "the budget and the rules; the whole budget is paid to the winners.")
CONTACT = "support@highlightz.app"
_EXAMPLE_BUDGETS = (250, 500, 1000, 5000)
_CHIPS = (250, 500, 1000, 2500, 5000)
_DEFAULT_BUDGET = 1000


# ── money, from the real pricing ─────────────────────────────────────────────

def _usd(cents: int, cents_always: bool = False) -> str:
    if cents % 100 == 0 and not cents_always:
        return "${:,}".format(cents // 100)
    return "${:,.2f}".format(cents / 100)


def _fee_text() -> str:
    """The fee in words: "5% of the budget", or with a flat part set,
    "$25 per campaign plus 5% of the budget"."""
    q = creq.quote(0)
    parts = []
    if q["flat_fee_cents"]:
        parts.append(_usd(q["flat_fee_cents"]) + " per campaign")
    if q["fee_pct"]:
        parts.append("{:g}% of the budget".format(q["fee_pct"]))
    return " plus ".join(parts) or "nothing"


def _range_text() -> str:
    p = creq.pricing()
    return (_usd(int(round(p["min_budget"] * 100))) + " to "
            + _usd(int(round(p["max_budget"] * 100))))


# ── the content ──────────────────────────────────────────────────────────────

def _steps() -> list[tuple[str, str]]:
    return [
        ("Make a free account",
         "Sign in with Twitch or Kick. A free account is all you need to run a "
         "campaign; no Highlightz plan, no card."),
        ("Tell us what you want",
         "Your channel, what clippers should look for, your rules, the hashtags "
         "and tags every post must carry, where clips get posted, how long it "
         "runs, how winners are picked, and your budget split into prizes."),
        ("We review it",
         "A person at Highlightz reads every request, usually within a day. We "
         "approve it, or tell you exactly what to change. Nothing is charged."),
        ("Pay, and it goes live",
         "Pay the budget plus the fee by card, through Stripe. The campaign goes "
         "into the Highlightz marketplace straight away, or on the start date you "
         "picked, and clippers start posting."),
        ("Winners get paid",
         "When it ends, we judge the entries the way you chose and pay the whole "
         "prize pool to the winning clippers."),
    ]


def _controls() -> list[tuple[str, str]]:
    return [
        ("Budget and prizes",
         "Any budget from " + _range_text() + ", split across up to "
         + str(creq.MAX_PLACES) + " winning places. Winner takes all, or spread it out."),
        ("Your channels",
         "Up to " + str(creq.MAX_STREAMERS) + " Twitch or Kick channels in one "
         "campaign: yours, or the group you stream with."),
        ("What to clip",
         "Describe the moments you want: clutch plays, reactions, a running bit, "
         "a new game. It is the first thing clippers read."),
        ("Your rules",
         "Clip length, what is off limits, language, anything else. Shown on the "
         "campaign page, word for word."),
        ("Required hashtags and tags",
         "Every post must carry them. A clip posted for your campaign from "
         "Highlightz is checked, and refused while one is missing."),
        ("Where clips go",
         "TikTok, YouTube Shorts and Instagram: pick any of them."),
        ("When it runs",
         "As soon as it is approved, or from a date you choose, for "
         + str(creq.DAYS_MIN) + " to " + str(creq.DAYS_MAX) + " days."),
        ("How winners are picked",
         "Most views on a single clip, most views across a clipper's best clips, "
         "most engagement, or your own criteria."),
    ]


def _faq() -> list[tuple[str, str]]:
    """(question, answer). The answers are HTML, written here; the only value
    from outside them is the fee text, which is escaped."""
    fee = _fee_text()
    return [
        ("Who can run a campaign?",
         "Anyone with a Highlightz account, and a free account is enough. You "
         "should be the streamer, or have their permission: a manager, an agency, a "
         "team. When the account asking signed in with the channel in the campaign, "
         "our reviewers see the request marked as verified."),
        ("When do I pay, and how?",
         "Only after we approve the request. You pay once, by card, on Stripe's "
         "checkout page; Highlightz never sees your card number. There is no "
         "subscription and nothing renews."),
        ("Where does the money go?",
         "Your budget is the prize pool, and all of it is paid to the winning "
         "clippers. The fee on top, " + escape(fee) + ", is what Highlightz charges "
         "to run the campaign: reviewing it, listing it in the marketplace, judging "
         "the entries and paying the winners."),
        ("What if you turn my request down?",
         "Nothing is charged, because nothing is charged before approval. We tell "
         "you why and, when it is something you can fix, what to change so you can "
         "send it again."),
        ("Can I change or cancel it?",
         "Until it is approved you can edit or withdraw it from your dashboard at "
         "any time. After approval and before you pay, you can still withdraw it, or "
         "ask us to reopen it for changes. Once paid, and before it starts, email us "
         "and we refund it in full. Once it has started, clippers are working on it "
         "and the prize pool is theirs to win, so it can no longer be refunded."),
        ("What if not enough clippers enter?",
         "Prizes go only to eligible entries. If a campaign ends with fewer eligible "
         "entries than prize places, the prizes nobody won are refunded to you."),
        ("How are the winners picked?",
         "The way you chose when you set it up: most views on a single clip, most "
         "views across a clipper's best clips, most engagement (views, likes and "
         "comments), or your own criteria. Only clips posted while the campaign runs, "
         "to the platforms you picked and with every hashtag and tag you required, "
         "count. When it ends we judge the entries and pay the winners."),
        ("How do clippers find my campaign?",
         "It is listed in the Campaigns marketplace inside Highlightz from the day "
         "it starts to the day it ends, with your picture, dates, rules, prizes and "
         "posting requirements. A clipper posting from Highlightz gets your "
         "hashtags, tags and caption filled in for them."),
        ("Who owns the clips?",
         "Your streams stay yours. Running a campaign gives the clippers taking part "
         "permission to clip your streams and post those clips for it, on the "
         "platforms you picked. The full terms are in "
         '<a href="/tos#campaigns">Section 4A of our Terms of Service</a>.'),
        ("What campaigns won't you run?",
         "Anything that breaks the rules of Twitch, Kick or the platforms the clips "
         "are posted to, anything illegal, and anything that asks clippers to "
         "mislead the people watching. We may decline any request; when we do, "
         "nothing is charged."),
        ("How big can a campaign be?",
         "Budgets from " + _range_text() + ", running " + str(creq.DAYS_MIN) + " to "
         + str(creq.DAYS_MAX) + " days, with up to " + str(creq.MAX_STREAMERS)
         + " channels and " + str(creq.MAX_PLACES) + " prize places. Planning "
         'something bigger? Email <a href="mailto:' + CONTACT + '">' + CONTACT
         + "</a>."),
    ]


# ── pieces ───────────────────────────────────────────────────────────────────

def _example_card() -> str:
    """The hero's picture: what a campaign looks like, with a real quote."""
    places = [500, 300, 200]
    q = creq.quote(sum(places))
    top = max(places)
    bars = "".join(
        '<div class="pr-place"><span class="pr-pn">' + n + '</span>'
        '<span class="pr-bar"><i style="width:' + str(round(100 * v / top)) + '%"></i></span>'
        '<span class="pr-pv">' + _usd(v * 100) + "</span></div>"
        for n, v in zip(("1st", "2nd", "3rd"), places))
    receipt = (
        '<div class="pr-rc"><span>Prize pool</span><b>' + _usd(q["budget_cents"], True) + "</b></div>"
        + ('<div class="pr-rc"><span>Campaign fee</span><b>' + _usd(q["flat_fee_cents"], True)
           + "</b></div>" if q["flat_fee_cents"] else "")
        + ('<div class="pr-rc"><span>Platform fee ({:g}%)</span><b>'.format(q["fee_pct"])
           + _usd(q["percent_fee_cents"], True) + "</b></div>" if q["percent_fee_cents"] else "")
        + '<div class="pr-rc pr-rc-t"><span>You pay</span><b>' + _usd(q["total_cents"], True)
        + "</b></div>")
    return (
        '<figure class="pr-card" aria-label="An example campaign">'
        '<div class="pr-card-k"><span class="pr-live">Live</span><span>14 days</span>'
        '<span class="pr-ex">Example</span></div>'
        '<div class="pr-card-t">Clip my week</div>'
        '<div class="pr-card-s">twitch.tv/yourchannel</div>'
        '<div class="pr-card-h">Prize pool &middot; ' + _usd(q["budget_cents"]) + "</div>"
        + bars +
        '<dl class="pr-card-d">'
        "<div><dt>Judged by</dt><dd>Most views on one clip</dd></div>"
        "<div><dt>Post with</dt><dd>#yourchannel @you</dd></div>"
        "<div><dt>Posts to</dt><dd>TikTok &middot; Shorts &middot; Instagram</dd></div>"
        "</dl>"
        '<div class="pr-receipt">' + receipt + "</div>"
        "</figure>")


def _steps_html() -> str:
    return '<ol class="pr-steps">' + "".join(
        '<li><span class="pr-sn">0' + str(i) + '</span><h3>' + escape(t) + "</h3><p>"
        + escape(d) + "</p></li>" for i, (t, d) in enumerate(_steps(), start=1)) + "</ol>"


def _controls_html() -> str:
    return '<div class="pr-grid">' + "".join(
        '<div class="pr-cell"><h3>' + escape(t) + "</h3><p>" + escape(d) + "</p></div>"
        for t, d in _controls()) + "</div>"


def _calculator() -> str:
    """The fee calculator. Rendered with the default budget's real numbers so
    it reads without JS; the script recomputes as the budget changes."""
    p = creq.pricing()
    q = creq.quote(_DEFAULT_BUDGET)
    bp = int(round(q["fee_pct"] * 100))
    chips = "".join(
        '<button type="button" class="pr-chip" data-v="' + str(v) + '" aria-pressed="'
        + ("true" if v == _DEFAULT_BUDGET else "false") + '">' + _usd(v * 100) + "</button>"
        for v in _CHIPS)
    pct_label = "Platform fee ({:g}% of the budget)".format(q["fee_pct"])
    return (
        '<div class="pr-calc" id="pc" data-flat="' + str(q["flat_fee_cents"]) + '" data-bp="'
        + str(bp) + '" data-min="' + str(int(round(p["min_budget"] * 100))) + '" data-max="'
        + str(int(round(p["max_budget"] * 100))) + '">'
        '<div class="pr-calc-in">'
        '<label class="pr-lab" for="pc-budget">Your budget</label>'
        '<div class="pr-field"><span aria-hidden="true">$</span>'
        '<input id="pc-budget" type="number" inputmode="decimal" min="'
        + "{:g}".format(p["min_budget"]) + '" max="' + "{:g}".format(p["max_budget"])
        + '" step="1" value="' + str(_DEFAULT_BUDGET) + '" aria-describedby="pc-msg"></div>'
        '<div class="pr-chips" role="group" aria-label="Try a budget">' + chips + "</div>"
        '<p class="pr-msg" id="pc-msg" aria-live="polite"></p>'
        "</div>"
        '<div class="pr-calc-out" aria-live="polite">'
        '<div class="pr-row"><span>Prize pool, paid to clippers</span><b id="pc-pool">'
        + _usd(q["budget_cents"], True) + "</b></div>"
        # No flat part (the default since 2026-10-23): no "$0.00" row.
        + ('<div class="pr-row"><span>Campaign fee</span><b id="pc-flat">'
           + _usd(q["flat_fee_cents"], True) + "</b></div>" if q["flat_fee_cents"] else "")
        + '<div class="pr-row"><span>' + escape(pct_label) + '</span><b id="pc-pct">'
        + _usd(q["percent_fee_cents"], True) + "</b></div>"
        '<div class="pr-row pr-total"><span>You pay</span><b id="pc-total">'
        + _usd(q["total_cents"], True) + "</b></div>"
        "</div></div>")


def _examples() -> str:
    rows = ""
    for b in _EXAMPLE_BUDGETS:
        q = creq.quote(b)
        rows += ('<tr><th scope="row">' + _usd(q["budget_cents"]) + "</th><td>"
                 + _usd(q["flat_fee_cents"] + q["percent_fee_cents"], True) + "</td><td>"
                 + _usd(q["total_cents"], True) + "</td></tr>")
    return ('<table class="pr-ex-t"><caption>For example</caption><thead><tr><th scope="col">'
            "Budget = prize pool</th><th scope=\"col\">Our fee</th><th scope=\"col\">You pay"
            "</th></tr></thead><tbody>" + rows + "</tbody></table>")


def _faq_html() -> str:
    return '<div class="faq-list">' + "".join(
        '<details class="faq-item"><summary class="faq-q">' + escape(q) + "</summary>"
        '<div class="faq-a">' + a + "</div></details>" for q, a in _faq()) + "</div>"


def _schema() -> str:
    """A Service and its FAQPage, from the same values the page shows."""
    service = {
        "@context": "https://schema.org", "@type": "Service",
        "name": "Highlightz Campaigns", "serviceType": "Clipping campaign",
        "url": "https://highlightz.app/promote", "description": _DESC,
        "provider": {"@type": "Organization", "name": "Highlightz",
                     "url": "https://highlightz.app"},
        "offers": {"@type": "Offer", "priceCurrency": "USD",
                   "description": "A budget you choose, paid to clippers as prizes, plus "
                                  + _fee_text() + "."},
    }
    faq = {"@context": "https://schema.org", "@type": "FAQPage",
           "mainEntity": [{"@type": "Question", "name": q,
                           "acceptedAnswer": {"@type": "Answer",
                                              "text": re.sub(r"<[^>]+>", "", a)}}
                          for q, a in _faq()]}
    # "<\/" is the same string to a JSON parser and cannot close the script.
    return "".join('<script type="application/ld+json">'
                   + json.dumps(d, ensure_ascii=False).replace("</", "<\\/") + "</script>"
                   for d in (service, faq))


_CSS = BASE_CSS + """
  /* ── /promote ─────────────────────────────────────────────────────────── */
  .nav-link.on{color:var(--ink)}
  .pr-hero{background:#000;color:var(--white);padding:calc(var(--nav-h) + var(--s-8)) 0 var(--s-9);overflow:hidden}
  .pr-hero-in{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,.95fr);
    column-gap:clamp(32px,5vw,96px);align-items:center}
  .pr-hero .k{color:var(--ember)}
  .pr-hero h1{font-size:clamp(40px,5.6vw,84px);max-width:12ch;margin-top:var(--s-4);color:var(--white)}
  .pr-hero .lead{margin:var(--s-5) 0 0;font-size:clamp(16px,1.4vw,19px);line-height:1.5;
    color:rgba(255,255,255,.72);max-width:var(--measure)}
  .pr-act{margin-top:var(--s-6);display:flex;gap:var(--s-4);flex-wrap:wrap;align-items:center}
  .pr-note{margin-top:var(--s-4);font-family:var(--mono);font-size:12px;letter-spacing:.08em;
    text-transform:uppercase;color:rgba(255,255,255,.62)}

  /* The example campaign: the marketplace card, on black. */
  .pr-card{margin:0;background:#0D0B10;border:1px solid rgba(255,255,255,.09);border-radius:10px;
    padding:var(--s-5);box-shadow:0 30px 60px -10px rgba(0,0,0,.9);color:var(--ink);min-width:0}
  .pr-card-k{display:flex;gap:var(--s-3);align-items:center;font-family:var(--mono);font-size:12px;
    letter-spacing:.12em;text-transform:uppercase;color:var(--ink-3)}
  .pr-live{color:#0A0A0C;background:var(--ember);border-radius:3px;padding:4px 8px;font-weight:600}
  .pr-ex{margin-left:auto;border:1px solid var(--hair);border-radius:3px;padding:4px 8px}
  .pr-card-t{margin-top:var(--s-4);font-weight:800;font-size:clamp(22px,2.2vw,30px);letter-spacing:-.03em;
    line-height:1.1;color:var(--white)}
  .pr-card-s{margin-top:var(--s-1);font-family:var(--mono);font-size:13px;color:var(--ink-3)}
  .pr-card-h{margin:var(--s-5) 0 var(--s-2);font-family:var(--mono);font-size:12px;letter-spacing:.12em;
    text-transform:uppercase;color:var(--ink-2)}
  .pr-place{display:grid;grid-template-columns:36px minmax(0,1fr) 64px;gap:var(--s-3);align-items:center;
    padding:4px 0}
  .pr-pn{font-family:var(--mono);font-size:12px;color:var(--ink-3)}
  .pr-bar{height:8px;background:rgba(242,234,247,.06);border-radius:4px;overflow:hidden}
  .pr-bar i{display:block;height:100%;background:var(--ember);border-radius:4px}
  .pr-pv{font-family:var(--mono);font-weight:600;font-size:14px;text-align:right;color:var(--ink);
    font-variant-numeric:tabular-nums}
  .pr-card-d{margin:var(--s-4) 0 0;border-top:1px solid var(--hair)}
  .pr-card-d div{display:flex;justify-content:space-between;gap:var(--s-4);padding:8px 0;
    border-bottom:1px solid var(--hair);font-size:14px}
  .pr-card-d dt{color:var(--ink-3)}
  .pr-card-d dd{margin:0;color:var(--ink);text-align:right}
  .pr-receipt{margin-top:var(--s-4);padding:var(--s-3) var(--s-4);background:rgba(247,167,69,.07);
    border:1px solid rgba(247,167,69,.22);border-radius:6px}
  .pr-rc{display:flex;justify-content:space-between;gap:var(--s-4);font-size:14px;padding:4px 0;color:var(--ink-2)}
  .pr-rc b{font-family:var(--mono);font-weight:600;color:var(--ink);font-variant-numeric:tabular-nums}
  .pr-rc-t{margin-top:4px;padding-top:8px;border-top:1px solid rgba(247,167,69,.22);color:var(--white);font-weight:700}
  .pr-rc-t b{color:var(--ember)}

  /* Paper sections. */
  .pr-sec{padding:var(--s-9) 0 0}
  .pr-sec .k{color:var(--paper-ink-3)}
  .pr-sec h2{font-size:clamp(28px,3.4vw,44px);max-width:18ch;margin-top:var(--s-4);color:var(--paper-ink)}
  .pr-sec .lead{margin:var(--s-4) 0 0;font-size:17px;line-height:1.55;color:var(--paper-ink-2);max-width:var(--measure)}
  .pr-sec .lead b{color:var(--paper-ink)}

  .pr-steps{list-style:none;margin:var(--s-7) 0 0;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));
    gap:clamp(16px,2vw,32px)}
  .pr-steps li{border-top:2px solid var(--paper-ink);padding-top:var(--s-4);min-width:0}
  .pr-steps li:nth-child(4){border-top-color:var(--ember)}
  .pr-sn{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.12em;color:var(--ember)}
  .pr-steps h3{margin-top:var(--s-2);font-weight:800;font-size:19px;letter-spacing:-.02em;line-height:1.2;
    color:var(--paper-ink)}
  .pr-steps p{margin-top:var(--s-2);font-size:15px;line-height:1.55;color:var(--paper-ink-2)}

  /* The calculator: the budget on the left, the bill on the right. */
  .pr-calc{margin-top:var(--s-7);display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);
    gap:clamp(24px,4vw,64px);align-items:start;border-top:2px solid var(--paper-ink);padding-top:var(--s-5)}
  .pr-lab{display:block;font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--paper-ink-3)}
  .pr-field{margin-top:var(--s-2);display:flex;align-items:center;gap:var(--s-2);border-bottom:2px solid var(--paper-ink);
    padding-bottom:var(--s-2);max-width:360px}
  .pr-field span{font-weight:800;font-size:clamp(32px,4vw,52px);color:var(--paper-ink-3);letter-spacing:-.04em}
  .pr-field input{flex:1;min-width:0;border:0;background:transparent;outline:none;font-family:var(--sans);
    font-weight:800;font-size:clamp(32px,4vw,52px);letter-spacing:-.04em;color:var(--paper-ink);
    font-variant-numeric:tabular-nums;-moz-appearance:textfield;appearance:textfield}
  .pr-field input::-webkit-outer-spin-button,.pr-field input::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
  .pr-field:focus-within{border-bottom-color:var(--ember)}
  .pr-chips{margin-top:var(--s-4);display:flex;flex-wrap:wrap;gap:var(--s-2)}
  .pr-chip{font-family:var(--mono);font-weight:600;font-size:13px;padding:8px 12px;border-radius:3px;cursor:pointer;
    background:transparent;color:var(--paper-ink);border:1px solid var(--paper-hair);
    transition:border-color var(--dur-fast),background var(--dur-fast)}
  .pr-chip:hover{border-color:var(--paper-ink)}
  .pr-chip[aria-pressed="true"]{background:var(--paper-ink);border-color:var(--paper-ink);color:var(--white)}
  .pr-msg{margin-top:var(--s-3);min-height:24px;font-size:14px;color:#A4471A}
  .pr-row{display:flex;justify-content:space-between;align-items:baseline;gap:var(--s-4);
    padding:var(--s-3) 0;border-bottom:1px solid var(--paper-hair);font-size:15px;color:var(--paper-ink-2)}
  .pr-row b{font-family:var(--mono);font-weight:600;font-size:16px;color:var(--paper-ink);white-space:nowrap;
    font-variant-numeric:tabular-nums}
  .pr-total{border-bottom:2px solid var(--paper-ink);color:var(--paper-ink);font-weight:700}
  .pr-total b{font-family:var(--sans);font-weight:800;font-size:clamp(28px,3vw,40px);letter-spacing:-.03em}
  .pr-calc.bad .pr-calc-out{opacity:.45}
  .pr-ex-t{margin-top:var(--s-7);width:100%;max-width:640px;border-collapse:collapse;font-size:15px}
  .pr-ex-t caption{text-align:left;font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--paper-ink-3);padding-bottom:var(--s-2)}
  .pr-ex-t th,.pr-ex-t td{padding:var(--s-3) var(--s-3) var(--s-3) 0;border-bottom:1px solid var(--paper-hair);text-align:left}
  .pr-ex-t thead th{font-weight:700;color:var(--paper-ink);border-bottom:2px solid var(--paper-ink)}
  .pr-ex-t tbody th{font-family:var(--mono);font-weight:600;color:var(--paper-ink)}
  .pr-ex-t td{font-family:var(--mono);color:var(--paper-ink-2);font-variant-numeric:tabular-nums}
  .pr-ex-t td:last-child{color:var(--paper-ink);font-weight:600}

  .pr-grid{margin-top:var(--s-7);display:grid;grid-template-columns:repeat(4,minmax(0,1fr));
    gap:0 clamp(24px,3vw,48px)}
  .pr-cell{border-top:1px solid var(--paper-hair);padding:var(--s-5) 0}
  .pr-cell h3{font-weight:800;font-size:18px;letter-spacing:-.02em;line-height:1.25;color:var(--paper-ink)}
  .pr-cell p{margin-top:var(--s-2);font-size:15px;line-height:1.55;color:var(--paper-ink-2)}

  /* The one argument for this marketplace over another: a black band. */
  .pr-band{margin-top:var(--s-9);background:#000;color:var(--white);padding:var(--s-9) 0}
  .pr-band .k{color:var(--ember)}
  .pr-band h2{font-size:clamp(28px,3.6vw,52px);max-width:18ch;margin-top:var(--s-4);color:var(--white)}
  .pr-band p{margin-top:var(--s-4);color:rgba(255,255,255,.72);font-size:clamp(16px,1.3vw,18px);line-height:1.55;
    max-width:var(--measure)}
  .pr-band p:first-of-type{margin-top:var(--s-6)}

  .pr-faq{padding-bottom:var(--s-9)}

  .closer{background:#000;color:var(--white);padding:var(--s-9) 0}
  .closer h2{font-size:clamp(32px,4.6vw,64px);max-width:14ch;color:var(--white)}
  .closer p{margin:var(--s-5) 0 0;color:rgba(255,255,255,.72);font-size:clamp(16px,1.4vw,19px);line-height:1.5;max-width:var(--measure)}
  .closer .act{margin-top:var(--s-6);display:flex;gap:var(--s-4);flex-wrap:wrap;align-items:center}
  .closer .note{margin-top:var(--s-4);font-family:var(--mono);font-size:12px;letter-spacing:.08em;
    text-transform:uppercase;color:rgba(255,255,255,.62)}

  @media (max-width:1100px){
    .pr-steps{grid-template-columns:repeat(2,minmax(0,1fr));row-gap:var(--s-6)}
    .pr-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  }
  @media (max-width:900px){
    .pr-hero-in{grid-template-columns:minmax(0,1fr);row-gap:var(--s-7)}
    .pr-hero h1{font-size:clamp(36px,9vw,56px)}
    .pr-calc{grid-template-columns:minmax(0,1fr)}
  }
  @media (max-width:600px){
    .pr-steps,.pr-grid{grid-template-columns:minmax(0,1fr)}
    .pr-card{padding:var(--s-4)}
    .pr-card-d div{flex-direction:column;gap:4px}
    .pr-card-d dd{text-align:left}
  }
"""


# The bar's height as --nav-h (the landing page's block), then the calculator,
# which repeats campaigns.requests.quote() in whole cents. No backslashes.
_JS = """
(function(){
  var nav = document.querySelector('.nav'), root = document.documentElement, last = 0;
  function measure(){
    if (!nav) return;
    var h = Math.round(nav.getBoundingClientRect().height);
    if (h && h !== last){ last = h; root.style.setProperty('--nav-h', h + 'px'); }
  }
  measure();
  window.addEventListener('resize', measure, { passive: true });
  if (nav && 'ResizeObserver' in window) new ResizeObserver(measure).observe(nav);

  var calc = document.getElementById('pc');
  if (!calc) return;
  var flat = Number(calc.getAttribute('data-flat')), bp = Number(calc.getAttribute('data-bp'));
  var min = Number(calc.getAttribute('data-min')), max = Number(calc.getAttribute('data-max'));
  var input = document.getElementById('pc-budget'), msg = document.getElementById('pc-msg');
  var chips = Array.prototype.slice.call(calc.querySelectorAll('[data-v]'));
  function money(c){
    return '$' + (c / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function put(id, text){ var el = document.getElementById(id); if (el) el.textContent = text; }
  function update(){
    var v = parseFloat(input.value);
    var b = isFinite(v) && v > 0 ? Math.round(v * 100) : 0;
    var pct = Math.floor((b * bp + 5000) / 10000);
    put('pc-pool', money(b)); put('pc-flat', money(flat)); put('pc-pct', money(pct));
    put('pc-total', money(b + flat + pct));
    var ok = b >= min && b <= max;
    msg.textContent = ok ? '' : (b < min ? 'The smallest budget is ' + money(min) + '.'
      : 'The largest budget here is ' + money(max) + '. Email us for a bigger campaign.');
    if (ok) calc.classList.remove('bad'); else calc.classList.add('bad');
    chips.forEach(function(c){
      c.setAttribute('aria-pressed', Number(c.getAttribute('data-v')) * 100 === b ? 'true' : 'false');
    });
  }
  chips.forEach(function(c){
    c.addEventListener('click', function(){ input.value = c.getAttribute('data-v'); update(); });
  });
  input.addEventListener('input', update);
  update();
})();
"""


def render(cta_href: str) -> str:
    """The page. `cta_href` is where Request a campaign goes: the sign-in for a
    visitor (which comes back to the request form), the form itself for an
    account already signed in."""
    cta = escape(cta_href, quote=True)
    return (
"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>""" + escape(_TITLE) + """</title>
<meta name="description" content=\"""" + escape(_DESC) + """\">
<link rel="icon" type="image/png" href="/static/icon.png">
<link rel="canonical" href="https://highlightz.app/promote">
<link rel="preload" href="/static/fonts/sora-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/plexmono-600.woff2" as="font" type="font/woff2" crossorigin>
<meta property="og:type" content="website">
<meta property="og:site_name" content="Highlightz">
<meta property="og:url" content="https://highlightz.app/promote">
<meta property="og:title" content=\"""" + escape(_TITLE) + """\">
<meta property="og:description" content=\"""" + escape(_DESC) + """\">
<meta property="og:image" content="https://highlightz.app/static/og-card-v6.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content=\"""" + escape(_TITLE) + """\">
<meta name="twitter:description" content=\"""" + escape(_DESC) + """\">
<meta name="twitter:image" content="https://highlightz.app/static/og-card-v6.png">
<link rel="alternate" type="text/markdown" href="https://highlightz.app/llms.txt" title="Highlightz for language models">
""" + _schema() + """
<style>""" + _CSS + """</style>
</head>
<body>

<nav class="nav">
  <a href="/" class="nav-logo"><img src="/static/logo-mark.png" alt="Highlightz"><span>Highlightz</span></a>
  <div class="nav-links">
    <a href="/#catches" class="nav-link">What it catches</a>
    <a href="/#score" class="nav-link">How it scores</a>
    <a href="/#watch" class="nav-link">Channels</a>
    <a href="/#pricing" class="nav-link">Pricing</a>
    <a href="/#faq" class="nav-link">FAQ</a>
    <a href="/tutorial" class="nav-link">Tutorial</a>
    <a href="/compare" class="nav-link">Compare</a>
    <a href="/blog" class="nav-link">Blog</a><!--NAV_CAMPAIGNS-->
  </div>
  <div class="nav-right">
    <a href="/login" class="nav-link">Sign in</a>
    <a href=\"""" + cta + """\" class="btn btn-go">Get started</a>
  </div>
</nav>

<header class="pr-hero">
  <div class="wrap pr-hero-in">
    <div>
      <div class="k">Campaigns &middot; For streamers</div>
      <h1 class="disp">Get your stream clipped, everywhere.</h1>
      <p class="lead">Run a campaign on Highlightz and clippers compete to post the best moments of your Twitch or Kick streams to TikTok, YouTube Shorts and Instagram. You set the budget and the rules, and every dollar of the budget is paid to the winners.</p>
      <div class="pr-act">
        <a href=\"""" + cta + """\" class="btn btn-go btn-lg">Request a campaign</a>
        <a href="#how" class="btn btn-ghost btn-lg">How it works</a>
      </div>
      <div class="pr-note">Free account &middot; You pay only once it is approved</div>
    </div>
    """ + _example_card() + """
  </div>
</header>

<section class="pr-sec" id="how">
  <div class="wrap">
    <div class="k">How it works</div>
    <h2 class="disp">Five steps, and you pay at the fourth.</h2>
    """ + _steps_html() + """
  </div>
</section>

<section class="pr-sec" id="pricing">
  <div class="wrap">
    <div class="k">Pricing</div>
    <h2 class="disp">Your budget is the prize pool. All of it.</h2>
    <p class="lead">You decide how much to put in. That money is split into prizes and paid to the winning clippers in full. Highlightz adds its fee on top: <b>""" + escape(_fee_text()) + """</b>. No subscription, and nothing that renews.</p>
    """ + _calculator() + _examples() + """
  </div>
</section>

<section class="pr-sec" id="control">
  <div class="wrap">
    <div class="k">Your campaign</div>
    <h2 class="disp">You write the brief. Clippers follow it.</h2>
    <p class="lead">Everything a clipper needs to know is on the campaign page, set by you when you ask for it.</p>
    """ + _controls_html() + """
  </div>
</section>

<section class="pr-band">
  <div class="wrap">
    <div class="k">Why Highlightz</div>
    <h2 class="disp">The clippers here already work live streams.</h2>
    <p>Highlightz watches Twitch and Kick channels while they are live and catches the moments worth clipping as they happen. The people using it spend their time on live streams, which is exactly where your campaign needs them.</p>
    <p>Your campaign sits in the marketplace inside the app they already have open. When they post for it from Highlightz, your hashtags, tags and caption are filled in for them, and a post missing one is refused, so the entries follow your rules.</p>
  </div>
</section>

<section class="pr-sec pr-faq" id="faq">
  <div class="wrap">
    <div class="k">Questions</div>
    <h2 class="disp">The fine print, in plain words.</h2>
    """ + _faq_html() + """
  </div>
</section>

<section class="closer">
  <div class="wrap">
    <h2 class="disp">Ready when your stream is.</h2>
    <p>Make a free account, tell us about your campaign, and hear back from a person, usually within a day.</p>
    <div class="act">
      <a href=\"""" + cta + """\" class="btn btn-go btn-lg">Request a campaign</a>
      <a href="/tos#campaigns" class="btn btn-ghost btn-lg">Read the campaign terms</a>
    </div>
    <div class="note">No card needed to ask</div>
  </div>
</section>

<footer class="footer">
  <img src="/static/logo-mark.png" alt="Highlightz" width="374" height="501">
  <nav aria-label="Site"><a href="/tutorial">Tutorial</a><a href="/compare">Compare</a><a href="/blog">Blog</a><!--FOOT_CAMPAIGNS--><a href="/tos">Terms of Service</a><a href="/privacy">Privacy Policy</a><a href="/cookies">Cookie Policy</a><a href="/opt-out">Streamer Opt-Out</a></nav>
  <span class="fl">&copy; 2026 ANTI Technology LLC</span>
</footer>

<script>""" + _JS + """</script>
</body>
</html>""")
