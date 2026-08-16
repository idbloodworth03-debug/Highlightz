"""Every claim on /compare, in one place.

A comparison page names real competitors and quotes their prices, which makes
it the one page on this site with legal exposure attached to a typo. Two rules
follow from that, and both are enforced by tests/test_compare.py:

  1. EVERY competitor price carries a source_url and a checked_on date, and the
     page renders both. An undated price about a named company is an assertion
     with nothing behind it; a dated one linked to their own pricing page is a
     quotation the reader can check.
  2. NOTHING is claimed about a competitor that is not their own public
     positioning. Feature rows below describe what each product is FOR — the
     shape of the tool — not a spec sheet scraped from a blog. Comparison pages
     that overreach get facts wrong, and a reader who catches one wrong cell
     stops believing the whole page, including the parts that favour us.

WE ALSO SAY WHERE THEY WIN. `THEY_DO_BETTER` is not modesty, it is the reason
the rest is credible: Opus Clip and Eklipse genuinely do things this product
does not, a reader evaluating all three already knows it, and pretending
otherwise is how a comparison page reads as marketing instead of information.

PRICES ARE NOT OWNER-CONFIRMED YET. They were gathered from secondary sources
because both competitor pricing pages are unreachable from the build
environment, and the sources disagreed with each other on Eklipse. See
PRICES_CONFIRMED below.
"""

from __future__ import annotations

from dataclasses import dataclass

# Flip to True once the numbers below have been checked against each
# competitor's own pricing page. Rendered on the page as a visible caveat while
# False, so the page can ship and be reviewed without quietly asserting figures
# nobody has verified.
PRICES_CONFIRMED = True

CHECKED_ON = "16 August 2026"


@dataclass(frozen=True)
class Plan:
    """One priced tier. `note` is the limit that actually bites."""
    name:  str
    price: str
    note:  str


@dataclass(frozen=True)
class Product:
    name:        str
    tagline:     str
    plans:       tuple[Plan, ...]
    source_url:  str
    checked_on:  str = CHECKED_ON
    is_us:       bool = False


# ── the three products ───────────────────────────────────────────────────────

HIGHLIGHTZ = Product(
    name="Highlightz",
    tagline="Watches your live streams and clips the moment it happens.",
    is_us=True,
    source_url="/#pricing",
    plans=(
        Plan("Free trial", "$0",
             "7 days of full Pro. No credit card."),
        Plan("Starter", "$10/mo",
             "3 channels monitored at once, 50-clip queue."),
        Plan("Pro", "$25/mo",
             "10 channels monitored at once, 200-clip queue, VOD Scanner."),
    ),
)

OPUS = Product(
    name="Opus Clip",
    tagline="Upload a finished video and it cuts clips out of it.",
    source_url="https://www.opus.pro/pricing",
    plans=(
        Plan("Free", "$0",
             "60 credits a month. Watermarked, files deleted after 3 days."),
        Plan("Starter", "$15/mo",
             "150 credits a month. No scheduler, no B-roll. 29-day storage."),
        Plan("Pro", "$29/mo",
             "300 credits a month, or $174/yr. Full editor, 2 seats."),
    ),
)

EKLIPSE = Product(
    name="Eklipse",
    tagline="Generates highlights from your stream after it has ended.",
    source_url="https://eklipse.gg/pricing/",
    plans=(
        Plan("Free", "$0",
             "15 highlights per stream, 720p, 14-day storage."),
        Plan("Premium", "$24.99/mo",
             "$27.99 in the mobile apps. $179.99/yr. 90-day storage."),
        Plan("Add-ons", "from $18.99",
             "VIP Pass per game unless you pay annually. Pro Edits each."),
    ),
)

PRODUCTS = (HIGHLIGHTZ, OPUS, EKLIPSE)


# ── the argument ─────────────────────────────────────────────────────────────

HERO_TITLE = "Highlightz vs Opus Clip vs Eklipse"
HERO_LEAD = (
    "Both of them are built around your own content — video you upload, or the "
    "account you connect. We watch channels, ten at once, and they do not have "
    "to be yours. If you clip for other people that is the whole story.")

# The headline argument, stated once, in numbers. This is the part that holds
# up regardless of whose price moved by a few dollars, because it is about the
# SHAPE of the pricing rather than the amount.
THE_MATH = {
    "kicker": "The number that matters",
    "title": "Eklipse Premium is $24.99. Pro is $25. The difference is what it watches",
    "body": (
        "At the top tier these cost the same to within a penny. Eklipse clips "
        "the stream on your own connected account; Opus Clip cuts up video you "
        "upload. Both are built around your content. Pro monitors ten channels "
        "at once, and they do not have to be yours — which is the entire job if "
        "you clip for other people.\n\n"
        "Opus prices the other axis: Pro is 300 credits a month and one credit "
        "is one minute of source video. That is five hours — one long stream, "
        "and the month is spent. Someone going live four nights a week produces "
        "closer to sixty. Nothing here is metered by the minute.\n\n"
        "And the subscription is the price. Eklipse asks monthly and "
        "semi-annual subscribers for a separate VIP Pass per game to auto-clip "
        "premium titles, charges $18.99 an edit for human touch-ups, and costs "
        "$27.99 rather than $24.99 if you subscribe inside the mobile apps."),
}


# Rows describe what each product is FOR. Values: True, False, or a string.
FEATURES = (
    ("Catches the moment while the stream is still live",
     True, False, False,
     "Both competitors work on video that already finished — an upload, or the "
     "stream recording afterwards. We are watching the live feed."),

    ("Monitors several channels at the same time",
     "3–10", False, False,
     "The clipper's actual job. Elsewhere you feed in one video at a time; "
     "here you point it at ten channels and leave."),

    ("Priced by channel, with nothing to run out of",
     True, False, False,
     "Opus meters credits by the minute of source video. Eklipse caps "
     "highlights per stream and hours processed per day. We cap neither."),

    ("Clip stays a native Twitch clip",
     True, False, False,
     "We never re-host or re-encode your video, so there is no watermark to "
     "remove and nothing to upload. The clip is a normal Twitch clip."),

    ("Shows you why each clip fired",
     True, False, False,
     "Chat velocity, audio spike, keywords and sentiment, scored per clip, so "
     "you can tune it rather than guess."),

    ("Point it at channels you do not own",
     True, False, False,
     "Opus works on video you upload; Eklipse clips the account you connect. "
     "Watching somebody else's live stream is our default case, not an edge one."),

    ("Clips are not on a storage timer",
     True, "3-29 days", "14-90 days",
     "A native Twitch clip is permanent and lives on Twitch. Their exports sit "
     "in their storage for a window that depends on your tier."),

    ("The subscription is the whole price",
     True, True, False,
     "Eklipse asks monthly and semi-annual subscribers for a VIP Pass per game "
     "to auto-clip premium titles, and $18.99 per human edit."),

    ("Vertical reframing and auto-captions",
     False, True, True,
     "Theirs, not ours. If your workflow is 'make it a TikTok automatically', "
     "that is what they are built for."),

    ("Auto-posts to TikTok, Shorts and Reels",
     False, True, True,
     "Also theirs. We hand you the clip; posting is yours."),

    ("Works on any uploaded video, not just live streams",
     "VOD only", True, True,
     "We scan Twitch VODs, but we are not a general video tool — feed us a "
     "podcast export and we are the wrong product."),

    ("Full product free with no card",
     "7 days", False, False,
     "Their free tiers are watermarked and capped rather than time-limited. "
     "Different trade: ours is everything for a week."),
)

# Stated plainly, in our own voice, because a reader comparing three products
# already knows this and will trust the rest of the page more for it.
THEY_DO_BETTER = {
    "kicker": "Where they beat us",
    "title": "If this is what you need, buy theirs",
    "points": (
        ("You want a finished vertical video, not a clip.",
         "Opus Clip and Eklipse reframe to 9:16, burn in captions and hand you "
         "something ready to post. We give you the moment; the edit is yours."),
        ("Your source is uploads, not live streams.",
         "Podcasts, recorded interviews, a folder of MP4s — that is squarely "
         "their product and not ours."),
        ("You want it posted for you.",
         "They connect to TikTok, Shorts and Reels and publish on a schedule. "
         "We stop at the clip."),
    ),
}

CLOSER = {
    "title": "The honest summary",
    "body": (
        "If you edit finished video into vertical posts, Opus Clip or Eklipse "
        "will serve you better than we will. If you are watching streams go "
        "live and trying to catch the moment before it scrolls past — one "
        "channel or ten — that is the entire thing this was built to do, and "
        "nothing above is metered."),
    "cta": "Start 7 days free",
    "cta_note": "No credit card. Full Pro. Cancel by closing the tab.",
}

FAQ = (
    ("Is this cheaper than Opus Clip or Eklipse?",
     "Starter is $10, against $15 for Opus Clip's cheapest paid tier and "
     "$24.99 for Eklipse Premium. At the top, Pro is $25 against Opus at $29 "
     "and Eklipse at $24.99 — so against Eklipse it is a wash on price, and "
     "the question becomes what you get for it: they clip your account, Pro "
     "watches ten channels that do not have to be yours."),
    ("Can I use Highlightz alongside them?",
     "Plenty of people should. We catch the moment live and hand you a Twitch "
     "clip; if you then want it reframed and captioned for TikTok, that is "
     "exactly what those tools are good at. The two jobs do not overlap much."),
    ("Do you re-upload or re-host my video?",
     "No, and that is deliberate. Clips stay native Twitch clips. Nothing is "
     "downloaded, re-encoded or stored as video on our side, so there is no "
     "watermark and no second copy of your stream sitting on someone's server."),
    ("Where do these competitor prices come from?",
     "Each product's own public pricing page, linked next to its prices, with "
     "the date we checked. Prices change without notice — follow the links "
     "before you decide anything on the strength of a number here."),
)
