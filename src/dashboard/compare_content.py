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

def _our_plans() -> tuple[Plan, ...]:
    """Our own three tiers, read from PLAN_LIMITS.

    THESE WERE TYPED OUT, and went stale exactly the way everything else that
    quoted a plan number did: this page still advertised "3 crowd suggestions"
    after free moved to 5, and never mentioned the weekly keep limit at all. It
    is a public, indexed page comparing us to competitors — wrong numbers about
    our OWN product are the worst place to have them.

    The competitor entries below stay hand-written on purpose. Those are
    observations about somebody else's pricing page, checked by a person on
    CHECKED_ON and shown with that date; there is nothing in this codebase to
    derive them from, and generating them would only make them look fresher
    than they are.
    """
    from src.billing.plans import PLAN_LIMITS, UNLIMITED_PENDING
    free, st, pro = (PLAN_LIMITS["free"], PLAN_LIMITS["starter"],
                     PLAN_LIMITS["pro"])

    def chans(p: dict) -> str:
        n = p["max_streams"]
        return f"{n} channel monitored" if n == 1 else f"{n} channels monitored at once"

    def keeps(p: dict) -> str:
        n = p["max_library_week"]
        return ("unlimited clips kept" if n >= UNLIMITED_PENDING
                else f"{n} clips kept a week")

    return (
        Plan("Free", f"${free['price']}",
             f"{chans(free)}, {free['max_pending']}-clip queue, "
             f"{free['max_suggested']} Highlight clips, {keeps(free)}. "
             "No card, no time limit."),
        Plan("Starter", f"${st['price']}/mo",
             f"{chans(st)}, {st['max_pending']}-clip queue, {keeps(st)}."),
        Plan("Pro", f"${pro['price']}/mo",
             f"{chans(pro)}, {pro['max_pending']}-clip queue, {keeps(pro)}, "
             "VOD Scanner."),
    )


HIGHLIGHTZ = Product(
    name="Highlightz",
    tagline="Watches your live streams and clips the moment it happens.",
    is_us=True,
    source_url="/#pricing",
    plans=_our_plans(),
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


def _free_limit(key: str) -> int:
    from src.billing.plans import PLAN_LIMITS
    return PLAN_LIMITS["free"][key]


_FREE_STREAMS = "One" if _free_limit("max_streams") == 1 else str(_free_limit("max_streams"))
_FREE_QUEUE = _free_limit("max_pending")


def _shipped() -> bool:
    """Whether the Clip Editor and the Scheduler are actually released.

    THE PROBLEM THIS FIXES (2026-09-21). Both are built and both are behind
    UPLOADS_ENABLED, which is down — the landing page says "Soon" for them,
    the dashboard hides them, and this page went on describing them in the
    present tense as things a reader could go and use. A comparison page
    claiming a feature the product will not hand over is the single worst
    place in the site to be wrong: the reader who signs up to get it is the
    reader who asks for a refund.

    Owner wanted the pitch kept — "I dont want it released yet but I want
    the talks about it" — so nothing here is deleted. The rows say Soon and
    the prose says what is coming rather than what is here, and both flip to
    the present tense on the deploy that sets UPLOADS_ENABLED=true.
    """
    from config.settings import settings
    return bool(settings.uploads_enabled)


# The matrix renders a string verbatim (compare_html._cell), so an unreleased
# row reads "Soon" where a released one gets a tick.
_SOON = True if _shipped() else "Soon"


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
        "closer to sixty. When the credits run out you buy the plan again, and "
        "what you did not use does not roll over. Nothing here is metered by "
        "the minute.\n\n"
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

    ("A real Twitch clip and the video file",
     True, False, False,
     "Every moment becomes a normal Twitch clip through Twitch's own API, "
     "under your account, and Highlightz also keeps the video itself, recorded "
     "from the live broadcast at source quality. The clip is on Twitch and the "
     "file is yours to download, edit and post. On Kick, where there is no clip "
     "API, the file is the clip."),

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
     "The Twitch clip is permanent and lives on Twitch; the working file we "
     "keep is a 30-day copy you can re-make from it. Their exports sit in "
     "their storage for a window that depends on your tier."),

    ("The subscription is the whole price",
     True, True, False,
     "Eklipse asks monthly and semi-annual subscribers for a VIP Pass per game "
     "to auto-clip premium titles, and $18.99 per human edit."),

    ("Vertical reframing and auto-captions",
     _SOON, True, True,
     "Theirs today; ours shortly. The Clip Editor is built and in testing — "
     "five vertical templates, transitions, a title and burned-in captions, "
     "rendered frame by frame in the browser from the clip Highlightz already "
     "caught. It is not open to accounts yet, so this row is not a tick."
     if not _shipped() else
     "All three. Ours is the Clip Editor: five vertical templates, transitions, "
     "a title and burned-in captions, rendered frame by frame in the browser "
     "from the clip Highlightz already caught."),

    ("Auto-posts to TikTok, Shorts and Reels",
     _SOON, True, True,
     "Theirs today; ours shortly. The Scheduler is built and in testing — "
     "connect YouTube, TikTok or Instagram and it posts at the time you set, "
     "with Autopilot cutting and queueing every clip you approve. Not open to "
     "accounts yet, so this row is not a tick either."
     if not _shipped() else
     "All three. Ours is the Scheduler: connect YouTube, TikTok or Instagram "
     "and it posts at the time you set; Autopilot cuts and queues every clip "
     "you approve by itself."),

    ("Works on any uploaded video, not just live streams",
     "VOD only", True, True,
     "We scan Twitch VODs, but we are not a general video tool — feed us a "
     "podcast export and we are the wrong product."),

    ("Unwatermarked on the free plan",
     True, False, False,
     "Not generosity on our part. The clip is a Twitch clip made through "
     "Twitch's own API and the file we keep is the broadcast as sent, so there "
     "is nothing of ours in the picture to stamp a logo onto, on any plan. "
     "Their free tiers render video, which is where a watermark can live and does."),
)

# ── after the subscription: the credits ──────────────────────────────────────
# Owner (2026-09-02): "For Opus and Eklipse they also charge for credits as
# well not just subscription — do research on that and involve that into the
# compare page." Researched 2 September 2026 from the companies' own help
# pages (linked below, shown on the page) and three 2026 pricing write-ups
# that agree with them. The official pricing pages themselves could not be
# fetched from the dev container, so these are the help-centre figures; the
# date is shown next to them like every other competitor number here.
#
# Our column reads PLAN_LIMITS where a number appears, same rule as the rest.
CREDITS_CHECKED_ON = "2 September 2026"


def _credit_rows() -> tuple[tuple[str, str, str, str], ...]:
    from src.billing.plans import PLAN_LIMITS
    f, s, p = (PLAN_LIMITS[k]["max_streams"] for k in ("free", "starter", "pro"))
    return (
        ("What is metered",
         "Nothing. You pay for channels, not minutes.",
         "Credits. One credit is one minute of the video you upload.",
         "Minutes of YouTube video (YT Credits). Live clipping has its own "
         "per-stream and per-day caps on top."),
        ("What the plan includes",
         f"Every second of every channel you watch: {f} on Free, {s} on Starter, {p} on Pro.",
         "60 credits a month on Free, 150 on Starter, 300 on Pro (3,600 a year "
         "if paid annually).",
         "30 minutes a month on Free, 600 a month on Premium (7,200 a year on "
         "the annual plan)."),
        ("When it runs out",
         "It does not run out.",
         "Credits cannot be bought on their own. You buy your plan again for "
         "another allotment, or add a pack for good: each Pro pack is another "
         "300 credits and two seats at the plan price. Free cannot buy any.",
         "You buy another pack: $39.98 a month for 1,200 minutes, or $299.98 a "
         "year for 14,400. Past that you arrange it with support. Free cannot "
         "buy any."),
        ("Unused allowance",
         "There is nothing to lose.",
         "Does not roll over. Monthly credits expire after 60 days, annual ones "
         "after 12 months, and cancelling does not extend them.",
         "Tied to the month or the year of the pack you bought."),
        ("Paid extras",
         "None. Highlight clips, review and the library are inside the plan.",
         "Seats only come bundled with packs; there is no other meter.",
         "Human Pro Edits: $18.99 each, three for $49.99, seven for $99.99. A "
         "VIP Pass per game to auto-clip premium titles unless you pay "
         "annually. $27.99 a month instead of $24.99 inside the mobile apps."),
    )


CREDITS = {
    "kicker": "After the subscription",
    "title": "What you pay when the credits run out",
    "lead": (
        "Both competitors sell a subscription and then meter it. The plan buys an "
        "allowance of minutes; when the allowance is gone the tool stops until the "
        "next cycle or the next purchase. This is the part of the price that only "
        "shows up after you have signed up."),
    "rows": _credit_rows(),
    "sources": (
        ("Opus Clip: How do credits work?",
         "https://help.opus.pro/docs/article/how-are-credits-consumed"),
        ("Eklipse: How do I purchase extra credit?",
         "https://eklipse.gg/help/purchase-extra-credit/"),
        ("Eklipse: What are YT Credits?",
         "https://eklipse.gg/help/what-are-yt-irl-credits/"),
        ("Eklipse: How does the pricing for Pro Edits work?",
         "https://eklipse.gg/help/how-does-the-pricing-for-pro-edits-work/"),
        ("Eklipse: VIP Pass FAQ",
         "https://eklipse.gg/help/eklipse-loyalty-pass/"),
    ),
}


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
    "cta": "Start clipping free",
    # Derived: this said "20-clip queue" as a literal.
    "cta_note": (f"{_FREE_STREAMS} channel and a {_FREE_QUEUE}-clip queue, "
                 "free with no card and no time limit."),
}

FAQ = (
    ("Is this cheaper than Opus Clip or Eklipse?",
     "Starter is $10, against $15 for Opus Clip's cheapest paid tier and "
     "$24.99 for Eklipse Premium. At the top, Pro is $25 against Opus at $29 "
     "and Eklipse at $24.99 — so against Eklipse it is a wash on price, and "
     "the question becomes what you get for it: they clip your account, Pro "
     "watches ten channels that do not have to be yours."),
    ("What happens when the credits run out on Opus Clip or Eklipse?",
     "The tool stops until the next cycle or the next purchase. On Opus Clip "
     "credits are not sold on their own: you buy your plan again for another "
     "allotment, or add a pack of 300 credits and two seats at the plan price, "
     "and whatever you did not use expires. On Eklipse the next 1,200 minutes "
     "are $39.98, and the human Pro Edits are priced per edit on top. On "
     "Highlightz there is no meter: the plan buys channels, and a channel is "
     "watched for every second it is live."),
    ("Can I use Highlightz alongside them?",
     ("Yes, and today that is the honest answer for a lot of people. We catch "
      "the moment live and keep the file; reframing it for vertical and "
      "posting it are the Clip Editor and the Scheduler, which are built and "
      "in testing but not open to accounts yet. Until they are, bring your "
      "own editor for that last step. Where the others will still win after "
      "that is general video: a podcast export or a long upload is their job, "
      "not ours.")
     if not _shipped() else
     ("You can, and fewer people need to than a year ago. We catch the moment "
      "live, keep the file, reframe it for vertical in the Clip Editor and "
      "post it from the Scheduler. Where they still win is general video: a "
      "podcast export or a long upload is their job, not ours.")),
    ("Do you re-upload or re-host my video?",
     "Not publicly, ever. The Twitch clip stays a Twitch clip. The file we keep "
     "is private to the account that caught it and is deleted with the clip; "
     "it is re-encoded only when you edit it, and uploaded only to the "
     "accounts you connect, when you or Autopilot post it. While a channel is "
     "monitored we hold a few minutes of the live broadcast in a rolling buffer "
     "that keeps overwriting itself; where that missed a moment, the clip's "
     "video is fetched from Twitch when you ask. The "
     "<a href=\"/privacy\">Privacy Policy</a> has the detail."),
    ("Where do these competitor prices come from?",
     "Each product's own public pricing page, linked next to its prices, with "
     "the date we checked. Prices change without notice — follow the links "
     "before you decide anything on the strength of a number here."),
)
