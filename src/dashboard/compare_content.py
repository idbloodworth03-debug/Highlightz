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
PRICES_CONFIRMED = False

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
        Plan("Free", "$0", "60 processing minutes a month, watermarked."),
        Plan("Starter", "$15/mo", "150 processing minutes a month, 720p."),
        Plan("Pro", "$29/mo", "300 processing minutes a month, 1080p."),
    ),
)

EKLIPSE = Product(
    name="Eklipse",
    tagline="Generates highlights from your stream after it has ended.",
    source_url="https://eklipse.gg/pricing/",
    plans=(
        Plan("Free", "$0", "Capped clips per stream, 720p, watermarked."),
        Plan("Premium", "$19.99/mo", "Higher caps, HD, watermark removed."),
    ),
)

PRODUCTS = (HIGHLIGHTZ, OPUS, EKLIPSE)


# ── the argument ─────────────────────────────────────────────────────────────

HERO_TITLE = "Highlightz vs Opus Clip vs Eklipse"
HERO_LEAD = (
    "They price by the minute of video you feed them. We price by the channel "
    "we watch. If you clip live streams, that difference is the whole story.")

# The headline argument, stated once, in numbers. This is the part that holds
# up regardless of whose price moved by a few dollars, because it is about the
# SHAPE of the pricing rather than the amount.
THE_MATH = {
    "kicker": "The number that matters",
    "title": "A processing-minute budget is a few hours of stream",
    "body": (
        "Opus Clip's Pro tier is 300 processing minutes a month. That is five "
        "hours of video — one long stream, or two short ones, and the month is "
        "spent. A streamer going live four nights a week produces something "
        "like sixty hours in that same month.\n\n"
        "Highlightz does not meter minutes. Pro watches ten channels for as "
        "long as they are live, every day of the month, for $25. The question "
        "is not whether we are cheaper per month — it is that per-minute "
        "pricing and live streaming are the wrong shape for each other."),
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

    ("Priced by channel, not by processing minute",
     True, False, False,
     "No credit balance, no minute budget, nothing to run out of mid-stream."),

    ("Clip stays a native Twitch clip",
     True, False, False,
     "We never re-host or re-encode your video, so there is no watermark to "
     "remove and nothing to upload. The clip is a normal Twitch clip."),

    ("Shows you why each clip fired",
     True, False, False,
     "Chat velocity, audio spike, keywords and sentiment, scored per clip, so "
     "you can tune it rather than guess."),

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
     "At the monthly figure, Starter is $10 against their paid tiers, and Pro "
     "is $25. But the real difference is the unit: they sell processing "
     "minutes, we sell monitored channels. If you stream a lot, a minute "
     "budget runs out and a channel does not."),
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
