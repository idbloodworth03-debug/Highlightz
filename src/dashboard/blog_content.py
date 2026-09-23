"""Every claim on /blog, in one place.

WHY A CONTENT MODULE. Same split as /compare and /tutorial: the renderer only
lays out what is here. This matters more on the blog than anywhere: it names
five real companies, quotes their pay rates and their rules, and tells readers
what to expect from TikTok and YouTube. A reader who acts on a wrong number
here loses money, so the rules are the /compare rules, tightened
(tests/test_blog.py enforces all of them):

  1. EVERY platform profile carries sources, and the page renders them, with
     the date they were checked. Rates and terms change; a dated, linked
     claim is a quotation the reader can check, an undated one is a promise.
  2. EVERY platform answers the SAME requirement questions (REQ_FIELDS), in
     the same order, so five profiles can be compared line by line. Owner,
     2026-09-23: "add in the different requirements for each as well. I need
     it to be transparent for the user."
  3. WHAT WE COULD NOT CONFIRM, WE SAY. A requirement no reachable source
     states is written NOT_PUBLISHED — never guessed, never left blank. Where
     sources disagree (Ssemble's marketing vs its help centre; Vyro's minimum
     payout) the page says so and the reader gets the stricter reading.
  4. THE FIT RATING IS HONEST. Highlightz watches LIVE streams on Twitch and
     Kick. Marketplaces built on pre-recorded creator videos or brand briefs
     are rated "Limited" for it, and the page says why, because a reader who
     signs up expecting help Highlightz cannot give is the reader who leaves.
  5. NOTHING UNRELEASED IS PITCHED AS AVAILABLE. The Clip Editor and the
     Scheduler are gated on the same switch /compare uses
     (compare_content._shipped): while they are held back the blog says
     they are coming, not that they are here.
  6. HOW HIGHLIGHT CLIPS ARE FOUND NEVER APPEARS. That is the owner's secret;
     the blog says what they are worth, never where they come from.

SOURCES. The build environment's proxy blocks almost every site named here,
so figures were gathered through search on the date below, from each
platform's own pages where their text was retrievable and from independent
guides otherwise. Several guides are written by a competing marketplace
ranking itself first; nothing here rests on one of those alone.
"""

from __future__ import annotations

from dataclasses import dataclass

CHECKED_ON = "23 September 2026"
PUBLISHED = "2026-09-23"          # ISO, for the structured data

NOT_PUBLISHED = "Not published"

# The questions every platform profile answers, in this order.
REQ_FIELDS = (
    "Audience needed",
    "Accounts and verification",
    "Views before a clip earns",
    "Minimum payout",
    "Fees",
    "When you get paid",
    "Where views count",
    "Campaign rules",
)


@dataclass(frozen=True)
class Source:
    label: str
    url:   str


@dataclass(frozen=True)
class Platform:
    slug:       str
    name:       str
    url:        str
    summary:    str              # one sentence: what it is
    who_posts:  str              # streamers, creators, brands — honestly
    pay:        str              # how the money works
    reqs:       tuple            # ((REQ_FIELDS label, answer), ...) in order
    watch_outs: tuple            # things a reader should know first
    fit:        str              # "Strong" | "Good" | "Limited"
    fit_why:    str              # why, in terms of what Highlightz does
    tip:        str              # our suggestion for using it
    sources:    tuple            # (Source, ...)


FITS = ("Strong", "Good", "Limited")


# ── the five platforms ───────────────────────────────────────────────────────

PLATFORMS_INTRO = (
    "These are the five marketplaces that come up most consistently across "
    "2026 guides to clipping. They are listed in no particular order and it "
    "is not a ranking by earnings — none of them publish comparable "
    "numbers. None of the links on this page are paid or affiliate links."
)

PLATFORMS = (
    Platform(
        slug="whop",
        name="Whop Content Rewards",
        url="https://whop.com/contentrewards/",
        summary=("Whop's campaign marketplace. Streamers, creators, brands and "
                 "music labels fund campaigns; you post clips on your own "
                 "accounts and are paid for verified views."),
        who_posts=("A mix of streamers, creators, brands and music labels — the "
                   "largest pool of campaigns of the five. Filter for creator and "
                   "streamer campaigns if that is what you clip."),
        pay=("Per 1,000 verified views, at a rate each campaign sets. Guides "
             "report rates from $0.20 to $6 per 1,000, averaging around $1."),
        reqs=(
            ("Audience needed", "None stated. Views are what is paid for."),
            ("Accounts and verification",
             "Link the social accounts you post from — views are checked "
             "against the live post. Whop reviews accounts at milestones such "
             "as a first withdrawal, and runs any identity checks (KYC) under "
             "its own terms."),
            ("Views before a clip earns",
             "Set per campaign through its minimum payout. At a $6 minimum on a "
             "$3-per-1,000 campaign, a clip under 2,000 views earns nothing."),
            ("Minimum payout", "Set per campaign, and shown on it."),
            ("Fees",
             "Campaign owners pay a 10% platform fee (8% if verified) plus "
             "Whop's processing fee. Withdrawal timing and fees are Whop's: "
             "about 3–5 business days, or instant for a fee."),
            ("When you get paid",
             "After approval, a clip keeps earning for 7 days, then a 3-day "
             "hold — about 10 days after approval. A credited but unsettled "
             "payout can be reversed if the clip is later rejected or "
             "fraud-flagged."),
            ("Where views count", "Whatever each campaign lists — check the brief."),
            ("Campaign rules",
             "Every submission is reviewed by whoever runs the campaign and can "
             "be rejected, including after the views arrive. Each gets a 0–100 "
             "bot score; high scores go to human review. A campaign stops "
             "paying when its budget runs out."),
        ),
        watch_outs=(
            "A clip can be rejected after it has already got its views.",
            "Budgets run out mid-campaign; the earlier you post, the safer.",
            "Some campaigns promote gambling or crypto sponsors — see our note on those.",
        ),
        fit="Strong",
        fit_why=("The biggest pool of streamer campaigns. Once you have joined a "
                 "streamer's campaign, Highlightz watches that channel live and "
                 "catches the moment while it is happening, so you are not the "
                 "twentieth person to post it."),
        tip=("Join two or three streamer campaigns with budgets left, add those "
             "channels to Highlightz, and post the moments while the stream is "
             "still live — early posts collect views before the budget drains."),
        sources=(
            Source("Whop — Content Rewards", "https://whop.com/contentrewards/"),
            Source("Whop — Content Rewards Terms of Service", "https://whop.com/content-rewards-terms-of-service/"),
            Source("Whop Docs — Content Rewards", "https://docs.whop.com/memberships-and-access/third-party-apps/content-rewards"),
            Source("Whop — Getting paid on Whop", "https://whop.com/blog/getting-paid-on-whop/"),
            Source("OpusClip — What is Whop Content Rewards?", "https://www.opus.pro/blog/whop-content-rewards"),
            Source("ClipAffiliates — Is Whop Content Rewards legit?", "https://www.clipaffiliates.com/blog/is-whop-content-rewards-legit"),
        ),
    ),
    Platform(
        slug="vyro",
        name="Vyro",
        url="https://vyro.com/",
        summary=("A clipping marketplace backed by MrBeast. Creators and brands "
                 "post campaigns with source videos; you clip them and post to "
                 "your own accounts."),
        who_posts=("Verified creators and brands. Most campaigns are pre-recorded "
                   "creator videos rather than live streams."),
        pay=("Advertised at $3 per 1,000 views. Each campaign sets its own rate, "
             "and independent reviews put the realistic range at about $1–$2 per "
             "1,000 eligible views. One review reports a $1,000 cap per clip."),
        reqs=(
            ("Audience needed", "None. Anyone can join."),
            ("Accounts and verification",
             "Verify you own your social accounts, link them to Vyro, and "
             "onboard with its payment providers before you are paid "
             "(Vyro's Clipper Terms)."),
            ("Views before a clip earns", NOT_PUBLISHED),
            ("Minimum payout",
             "Not published by Vyro. One review reports $10, withdrawable once "
             "a week."),
            ("Fees", NOT_PUBLISHED),
            ("When you get paid",
             "Earnings update hourly. Withdraw to PayPal, bank or crypto; "
             "payouts go through Stripe or PayPal depending on your country."),
            ("Where views count",
             "TikTok, Instagram Reels and YouTube Shorts, combined. Some "
             "sources also list X."),
            ("Campaign rules",
             "Vyro publishes Clipper Content Requirements — read them before "
             "posting."),
        ),
        watch_outs=(
            "The $3 headline is not the typical rate; plan on the campaign's own number.",
            "Minimum payout and fees are not on any Vyro page we could reach.",
        ),
        fit="Limited",
        fit_why=("Highlightz watches live streams on Twitch and Kick. Most Vyro "
                 "campaigns are pre-recorded videos, so it only helps on the "
                 "ones for live streamers."),
        tip=("Use Vyro for creator campaigns you clip by hand, and Highlightz for "
             "any streamer campaigns on it."),
        sources=(
            Source("Vyro", "https://vyro.com/"),
            Source("Vyro — Clipper Terms", "https://www.vyro.com/clipper-terms"),
            Source("Vyro — Clipper Content Requirements", "https://vyro.com/content-requirements"),
            Source("Vyro — What is Vyro?", "https://vyro.com/help/getting-started/what-is-vyro"),
            Source("OpusClip — Vyro vs Whop Content Rewards", "https://www.opus.pro/blog/vyro-vs-whop"),
            Source("Ssemble — Vyro review 2026", "https://www.ssemble.com/blog/vyro-review-2026"),
            Source("ClipAffiliates — Vyro review", "https://www.clipaffiliates.com/blog/vyro-review"),
        ),
    ),
    Platform(
        slug="vues",
        name="Vues",
        url="https://vues.app/",
        summary=("A pay-per-view clipping marketplace. The rate is printed on "
                 "each campaign brief before you post."),
        who_posts=("Mainly brands — Vues reports more than 60 funded brands. Its "
                   "own guides say crypto, betting and finance briefs pay the most."),
        pay=("Per 1,000 views at the rate on the brief. By Vues' own account, "
             "general entertainment briefs are often under $2, and crypto, "
             "betting and finance briefs several dollars."),
        reqs=(
            ("Audience needed", "None — no follower minimum."),
            ("Accounts and verification",
             "Paste the post link; views are read automatically. Identity "
             "checks: " + NOT_PUBLISHED.lower() + "."),
            ("Views before a clip earns",
             "None — every view accrues at the campaign rate."),
            ("Minimum payout",
             "A withdrawal minimum applies; the amount is not published in any "
             "source we could reach."),
            ("Fees", "A processing fee per withdrawal, shown before you confirm."),
            ("When you get paid",
             "Automatically when the campaign ends. Withdraw to crypto (USDT, "
             "SOL, BTC), PayPal or bank transfer."),
            ("Where views count", "TikTok, Instagram Reels, YouTube Shorts and X."),
            ("Campaign rules", "Set on each brief."),
        ),
        watch_outs=(
            "You are paid when the campaign ends, not as views come in.",
            "Its scale figures (over $3M paid, 301,000+ approved clips) are its own.",
            "Its best-paying briefs are crypto and betting — see our note on those.",
        ),
        fit="Limited",
        fit_why=("Vues is built around brand briefs, not streamers. Highlightz "
                 "helps only when a brief is for a live streamer."),
        tip=("Worth adding for X, which it counts and most others do not — but "
             "do not expect many streamer briefs."),
        sources=(
            Source("Vues", "https://vues.app/"),
            Source("Vues — Campaigns", "https://vues.app/campaigns"),
            Source("Vues — Best clipping platforms for beginners", "https://vues.app/blog/best-clipping-platforms-for-beginners"),
            Source("Vues — Best clipping platforms that pay via PayPal", "https://vues.app/blog/best-clipping-platforms-that-pay-paypal"),
        ),
    ),
    Platform(
        slug="ssemble",
        name="Ssemble Clip Rewards",
        url="https://www.ssemble.com/clip-rewards",
        summary=("A clipping marketplace built into Ssemble's video editor, so "
                 "you can cut and submit in one place."),
        who_posts="Streamers, podcasters and brands.",
        pay="About $0.35 to $4 per 1,000 verified views, depending on the campaign.",
        reqs=(
            ("Audience needed",
             "None — no follower minimum or watch-hour gate, and free to join."),
            ("Accounts and verification",
             "To withdraw, connect a payout account through Stripe (identity "
             "and bank). You can clip and earn before setting it up."),
            ("Views before a clip earns",
             "1,000 views, per Ssemble's help centre. Its marketing says "
             "“from view 1”; plan on the stricter."),
            ("Minimum payout", "$20."),
            ("Fees", "No membership fee. Withdrawal fees: " + NOT_PUBLISHED.lower() + "."),
            ("When you get paid",
             "Earnings become payable 5 days after the clip is posted, after "
             "bot and abuse review."),
            ("Where views count", NOT_PUBLISHED),
            ("Campaign rules", "Set per campaign."),
        ),
        watch_outs=(
            "Payouts are through Stripe, available in 39 countries — check yours first.",
            "Its own pages disagree on when a clip starts earning.",
        ),
        fit="Good",
        fit_why=("It has streamer campaigns alongside podcasts and brands. "
                 "Highlightz catches the streamer moments; Ssemble's editor is "
                 "there if you want to cut in it."),
        tip=("Pick its streamer campaigns, let Highlightz find the moments, and "
             "check your country is on Stripe's list before you put in the hours."),
        sources=(
            Source("Ssemble — Clip Rewards", "https://www.ssemble.com/clip-rewards"),
            Source("Ssemble — For clippers", "https://www.ssemble.com/clipper"),
            Source("Ssemble Help — How to monetize clips", "https://help.ssemble.com/how-to-monetize-clips/"),
            Source("Ssemble — Best clipping platforms 2026", "https://www.ssemble.com/blog/best-clipping-platforms-2026"),
        ),
    ),
    Platform(
        slug="clipify",
        name="Clipify",
        url="https://docs.sxbot.io/clipify/get-paid-to-clip-on-discord",
        summary=("A Discord-based clipping community. Campaigns are posted and "
                 "tracked inside Discord, run with the Sx Bot."),
        who_posts=("Streamers, creators, music artists and brands. Its own pages "
                   "name MrBeast, iShowSpeed, Kai Cenat, Marlon, FaZe Clan, Adin "
                   "Ross and xQc — and the gambling brands Stake and RainBet."),
        pay=("Performance-based, set per campaign. Campaign types are streamer "
             "moments, logo shout-outs and music."),
        reqs=(
            ("Audience needed", NOT_PUBLISHED),
            ("Accounts and verification",
             "Join its Discord; campaigns and submissions run through the bot."),
            ("Views before a clip earns", "Set per campaign; " + NOT_PUBLISHED.lower() + " centrally."),
            ("Minimum payout", "Set per campaign; " + NOT_PUBLISHED.lower() + " centrally."),
            ("Fees",
             "Not published for clippers. Clipify's docs describe premium "
             "access arranged through its support server — confirm what "
             "applies to you before joining."),
            ("When you get paid", NOT_PUBLISHED),
            ("Where views count", NOT_PUBLISHED),
            ("Campaign rules", "Set per campaign."),
        ),
        watch_outs=(
            "The fewest published terms of the five — get each campaign's rules in writing.",
            "Gambling sponsors: TikTok and YouTube restrict gambling promotion.",
        ),
        fit="Strong",
        fit_why=("Its campaigns are for live streamers — exactly the moments "
                 "Highlightz is built to catch as they happen."),
        tip=("Take streamer campaigns, skip the gambling ones, and add the "
             "streamers to Highlightz so you have the moment before the rest "
             "of the server does."),
        sources=(
            Source("Sx Bot Docs — Get paid to clip on Discord", "https://docs.sxbot.io/clipify/get-paid-to-clip-on-discord"),
            Source("Sx Bot Docs — Clipify clipping campaigns", "https://docs.sxbot.io/clipify/clipify-clipping-campaigns"),
            Source("Sx Bot Docs — Make a clipping server", "https://docs.sxbot.io/clipify/how-to-make-a-clipping-server-for-content-creators"),
        ),
    ),
)


# ── what Highlightz does, for the "how it fits" sections ────────────────────

def _shipped() -> bool:
    """The same switch /compare uses, so the two pages can never disagree
    about whether the editor and the scheduler are out."""
    from src.dashboard.compare_content import _shipped as shipped
    return shipped()


def _limit(plan: str) -> int:
    from src.billing.plans import PLAN_LIMITS
    return PLAN_LIMITS[plan]["max_streams"]


def highlightz_points() -> tuple:
    """(title, body) pairs describing what Highlightz does for a clipper.
    Computed, not constant: plan numbers come from the real plans, and the
    editor/scheduler line changes tense on the deploy that releases them."""
    pts = [
        ("It watches live channels for you",
         f"Add Twitch or Kick channels and Highlightz watches them while they "
         f"are live — {_limit('free')} on Free, {_limit('starter')} on Starter, "
         f"{_limit('pro')} on Pro — so you are not sitting on six streams at once."),
        ("It catches the moment while it is happening",
         "Clips are made during the stream and land in a review queue. In a "
         "per-view campaign the first good post usually gets the views, and "
         "budgets run out."),
        ("Highlight clips",
         "Some clips arrive marked Highlight, in purple — usually the "
         "higher-quality ones — so you know which to look at first."),
        ("You keep the file",
         "Every clip is a video file you can download, then edit and post "
         "wherever the campaign counts views."),
    ]
    if _shipped():
        pts.append(("Edit and schedule in one place",
                    "The Clip Editor turns a clip vertical with captions, and the "
                    "Scheduler posts it to your connected accounts."))
    else:
        pts.append(("Editing and scheduling are coming",
                    "A Clip Editor and a Scheduler are on the way; today you "
                    "download the file and post it yourself."))
    return tuple(pts)


# Real product captures already used on the landing page, so every picture on
# the blog shows a shipped screen.
SHOTS = {
    "live":    ("tour-live.webp", "Highlightz watching several live channels at once"),
    "review":  ("tour-review.webp", "The review queue, where caught clips land during a stream"),
    "library": ("tour-library.webp", "The clip library, where every approved clip is kept as a file"),
}


# ── what each route pays, for the chart ──────────────────────────────────────
# (label, low $ per 1,000 views, high $, what it applies to, source index)

PAY_PER_1K = (
    ("YouTube Shorts (gaming)", 0.02, 0.08,
     "Shorts ad-pool share, gaming channels"),
    ("TikTok Creator Rewards", 0.40, 1.00,
     "Per 1,000 qualified views, eligible videos only"),
    ("Clipping campaigns", 0.50, 4.00,
     "Typical per-view campaign rates"),
    ("YouTube long-form (gaming)", 2.00, 6.00,
     "Ad revenue, gaming videos over the Shorts length"),
)
PAY_SOURCES = (
    Source("Fluxnote — Gaming Shorts RPM 2026", "https://fluxnote.io/guides/youtube-shorts-rpm-gaming-niche"),
    Source("Elev8or — TikTok Creator Rewards RPM 2026", "https://www.elev8or.io/blog/tiktok-creator-rewards-rpm-2026"),
    Source("Posthype — Clipping pays up to $5 per 1,000 views", "https://www.posthype.news/article/clipping-ftc-disclosure"),
    Source("AIR Media-Tech — Shorts RPM vs long-form", "https://air.io/en/air-data-findings/youtube-shorts-rpm-vs-long-form-how-much-do-shorts-earn-in-2026"),
)


# ── articles ─────────────────────────────────────────────────────────────────
# A block is ("p", text) | ("h", heading) | ("list", (item, ...)) |
# ("req", ((label, value), ...)) | ("chart",) | ("platforms",) |
# ("highlightz",) | ("shot", key) | ("note", text).
# Text may carry [label](/path-or-url) links; everything else is escaped.

@dataclass(frozen=True)
class Article:
    slug:        str
    title:       str
    description: str
    kicker:      str
    lead:        str
    blocks:      tuple
    sources:     tuple


ARTICLES = (
    Article(
        slug="how-clippers-make-money",
        title="How clippers actually make money in 2026",
        description=("The four ways clippers get paid — per-view campaigns, "
                     "official clip channels, TikTok Creator Rewards and YouTube "
                     "— what each pays per 1,000 views, and what each one requires."),
        kicker="The overview",
        lead=("Four routes pay clippers. They pay very different amounts, and "
              "they ask very different things of you. Start here."),
        blocks=(
            ("h", "What 1,000 views is worth"),
            ("chart",),
            ("p", "The same thousand views can pay a few cents or a few dollars "
                  "depending on where the money comes from. For most clippers, "
                  "per-view campaigns pay the most per view and ask the least "
                  "of a new account."),
            ("h", "1. Per-view clipping campaigns"),
            ("p", "A streamer, creator or brand funds a campaign on a marketplace "
                  "or in their Discord and pays per 1,000 verified views of clips "
                  "you post on your own accounts. Typical rates run $0.50 to $4 "
                  "per 1,000. No follower count is needed — only views."),
            ("req", (("Audience", "None on the main marketplaces"),
                     ("Rights", "The campaign is the permission to clip that footage"),
                     ("Catch", "Clips can be rejected, budgets run out, and paid clips are ads that need disclosing"))),
            ("p", "The five biggest marketplaces, and every requirement they "
                  "publish, are in [the platform guide](/blog/clipping-platforms). "
                  "Where to find campaigns for streamers rather than products is "
                  "in [finding streamer campaigns](/blog/find-streamer-campaigns)."),
            ("h", "2. Running a streamer's official clip channel"),
            ("p", "Many streamers pay an editor to run their clips account, for a "
                  "flat fee or a share of its revenue. It is the steadiest income "
                  "of the four, and the one with the fewest platform problems: "
                  "the clips are posted with the permission of the person who "
                  "owns them."),
            ("req", (("Audience", "None — but you need a streamer who says yes"),
                     ("Rights", "Written permission from the streamer"),
                     ("Catch", "One streamer's schedule is your income"))),
            ("h", "3. TikTok Creator Rewards"),
            ("p", "TikTok pays $0.40 to $1.00 per 1,000 qualified views, but only "
                  "on original videos longer than one minute — and a plain clip "
                  "of someone else's stream is not original by TikTok's rules. "
                  "[The Creator Rewards guide](/blog/tiktok-creator-rewards) has "
                  "the full requirements and what counts as original."),
            ("req", (("Audience", "10,000 followers and 100,000 views in 30 days"),
                     ("Rights", "Original content, judged video by video"),
                     ("Catch", "Five ineligible videos in 30 days can cost the account"))),
            ("h", "4. YouTube"),
            ("p", "Shorts pay very little for gaming — a few cents per 1,000 "
                  "views — and from February 2027 only channels with 10 million "
                  "Shorts views every 90 days earn from Shorts ads at all. "
                  "Long-form compilations pay far more. [The YouTube guide](/blog/youtube-for-clippers) "
                  "has the thresholds and the rules on reused content."),
            ("req", (("Audience", "1,000 subscribers plus watch hours or Shorts views"),
                     ("Rights", "Reused content must add significant value"),
                     ("Catch", "A violation demonetizes the whole channel"))),
            ("h", "Where Highlightz fits"),
            ("highlightz",),
            ("shot", "live"),
            ("note", "Figures change. Everything here was checked on " + CHECKED_ON
                     + " and is linked to its source below. This is general "
                       "information, not financial or legal advice."),
        ),
        sources=PAY_SOURCES + (
            Source("Whop — Content Rewards", "https://whop.com/contentrewards/"),
            Source("TikTok — Creator Rewards Program", "https://www.tiktok.com/creator-academy/article/creator-rewards-program"),
            Source("YouTube Help — Shorts monetization policies", "https://support.google.com/youtube/answer/12504220?hl=en"),
            Source("Tubefilter — Stricter Shorts ad eligibility (August 2026)", "https://www.tubefilter.com/2026/08/10/youtube-partner-program-ad-eligibility-requirements-shorts/"),
            Source("ClipSpeed — How to make money clipping streamers", "https://www.clipspeed.ai/blog/make-money-clipping-streamers.html"),
        ),
    ),
    Article(
        slug="clipping-platforms",
        title="The 5 biggest clipping platforms, and every requirement they publish",
        description=("Whop Content Rewards, Vyro, Vues, Ssemble Clip Rewards and "
                     "Clipify compared: who posts campaigns, how pay works, "
                     "minimum views and payouts, fees, and which suit clipping "
                     "live streamers."),
        kicker="Platform guide",
        lead=("Who funds the campaigns, what you need to join, when you get "
              "paid, and what each platform does not tell you — side by side."),
        blocks=(
            ("p", PLATFORMS_INTRO),
            ("platforms",),
            ("h", "Before you join any of them"),
            ("list", (
                "Paid clips are ads. US FTC guidance treats a clip you are paid "
                "per view for as an endorsement that has to be disclosed, "
                "clearly and on the video itself; TikTok requires its "
                "commercial-content setting on posts that promote a brand.",
                "Only clip footage the campaign gives you, or a channel whose "
                "program you have joined.",
                "Gambling and crypto campaigns pay the most and carry the most "
                "risk: TikTok and YouTube restrict gambling promotion, and many "
                "places regulate it.",
                "Rejections after views, drained budgets and bot flags are "
                "normal on every platform. Spread your work across two or three.",
            )),
            ("h", "How Highlightz fits"),
            ("highlightz",),
            ("shot", "review"),
            ("note", "Checked on " + CHECKED_ON + ". Rates and terms change — "
                     "each profile links its sources, and the platform's own "
                     "page wins over anything here."),
        ),
        sources=(
            Source("Posthype — Clipping and FTC disclosure", "https://www.posthype.news/article/clipping-ftc-disclosure"),
            Source("Lumina — Is clipping legal?", "https://luminaclippers.com/blog/is-clipping-legal"),
            Source("Ssemble — Best clipping platforms 2026", "https://www.ssemble.com/blog/best-clipping-platforms-2026"),
            Source("Cut.Pro — Clipping campaign platforms compared", "https://cut.pro/en/blog/clipping-campaign-platforms-comparison-2026"),
            Source("Growthr — Best clipping platforms", "https://growthr.com/resources/best-clipping-platforms/"),
            Source("ClipAffiliates — Whop and Vyro alternatives", "https://www.clipaffiliates.com/blog/whop-vyro-clipping-alternatives-2026"),
        ),
    ),
    Article(
        slug="tiktok-creator-rewards",
        title="TikTok Creator Rewards for clippers: the requirements, plainly",
        description=("What TikTok's Creator Rewards Program requires — followers, "
                     "views, video length, qualified views and originality — and "
                     "why most plain clips of someone else's stream do not qualify."),
        kicker="TikTok",
        lead=("TikTok pays for original videos longer than a minute. Here is "
              "every requirement, and what TikTok counts as original."),
        blocks=(
            ("h", "The requirements"),
            ("req", (
                ("Age", "18 or older"),
                ("Followers", "At least 10,000"),
                ("Views", "At least 100,000 in the last 30 days"),
                ("Video length", "Longer than one minute — a 60-second video does not qualify"),
                ("Per video", "At least 1,000 views from the For You feed"),
                ("Formats", "Not a Duet, Stitch, Photo Mode post or ad"),
                ("Qualified view", "A real account in an eligible region that watches at least 5 seconds"),
            )),
            ("h", "What TikTok counts as original"),
            ("p", "Original content is “designed, filmed, and produced by you”, "
                  "or content that “adds new ideas to preexisting content”. "
                  "Reposts, watermarked content and videos “reproduced from "
                  "others with only slight modifications” — sped up, filtered, "
                  "fixed text or stickers — are not. Captions and transitions "
                  "alone do not make someone else's stream yours."),
            ("p", "What does add new ideas is you: your voice over the clip with "
                  "your take, your face reacting with real commentary, or an edit "
                  "that tells a story rather than trimming one."),
            ("h", "How it is enforced"),
            ("list", (
                "Each video is reviewed on its own and can be appealed within 80 "
                "days; reviews take about three days.",
                "Five or more ineligible videos in 30 days can disqualify the account.",
                "Pay is $0.40 to $1.00 per 1,000 qualified views, weighted by "
                "originality, watch time, search value and engagement.",
            )),
            ("p", "For clipping someone else's stream, TikTok is usually better "
                  "as the place a campaign counts your views than as the thing "
                  "that pays you. See [how clippers make money](/blog/how-clippers-make-money)."),
            ("note", "Checked on " + CHECKED_ON + ". TikTok's own Creator Rewards "
                     "page in the app is the authority."),
        ),
        sources=(
            Source("TikTok — Creator Rewards Program", "https://www.tiktok.com/creator-academy/article/creator-rewards-program"),
            Source("TikTok — Creator Rewards eligibility", "https://www.tiktok.com/creator-academy/article/eligibility"),
            Source("TikTok — Eligible videos", "https://www.tiktok.com/creator-academy/article/monetization-creativity-program-video-eligible"),
            Source("TikTok Newsroom — The new Creator Rewards Program", "https://newsroom.tiktok.com/en-us/introducing-the-new-creator-rewards-program"),
            Source("Elev8or — Creator Rewards RPM 2026", "https://www.elev8or.io/blog/tiktok-creator-rewards-rpm-2026"),
            Source("MonetizedNow — Appealing an unoriginal-content flag", "https://monetizednow.com/creator-rewards-unoriginal-content-appeal"),
        ),
    ),
    Article(
        slug="youtube-for-clippers",
        title="YouTube and Shorts for clippers: thresholds, pay and reused content",
        description=("YouTube Partner Program thresholds now and from February "
                     "2027, what Shorts pay for gaming clips, the reused-content "
                     "rule, and how Content ID claims affect clip channels."),
        kicker="YouTube",
        lead=("YouTube allows clips that add value, pays little for Shorts, and "
              "judges the whole channel. What that means for a clip channel."),
        blocks=(
            ("h", "Getting into the Partner Program"),
            ("req", (
                ("Today", "1,000 subscribers, plus 4,000 long-form watch hours in 12 months or 10 million Shorts views in 90 days"),
                ("New channels from 1 February 2027", "1,000 subscribers, plus 8,000 watch hours or 20 million Shorts views in 90 days"),
                ("Shorts ads from 1 February 2027", "Every channel needs 10 million qualified Shorts views every 90 days"),
                ("Already in", "Existing members keep their place in the program"),
            )),
            ("h", "What Shorts pay"),
            ("p", "Ads between Shorts go into one pool per country, split by each "
                  "channel's share of engaged views; the channel keeps 45% of its "
                  "share. Each music track in a Short reduces its share. For "
                  "gaming that works out to about $0.02–$0.08 per 1,000 views, "
                  "against $2–$6 for long-form gaming videos."),
            ("p", "Shorts can be up to three minutes for vertical video. Pay is "
                  "based on engaged views, not the public view count, which since "
                  "2025 counts every start or replay."),
            ("h", "The reused-content rule"),
            ("p", "Clips, compilations and reactions can be monetized when they add "
                  "“significant original commentary, substantive modifications, "
                  "or educational or entertainment value”. Compilations “with "
                  "little or no narrative” are not. A violation switches off "
                  "monetization for the whole channel, with a 30-day wait to "
                  "reapply. Since July 2025 YouTube also names content that looks "
                  "“made with a template with little to no variation”."),
            ("h", "Content ID"),
            ("p", "If a streamer or a record label has Content ID, a claim can send "
                  "a clip's ad revenue to them. Shorts between one and three "
                  "minutes with a claim used to be blocked everywhere; from 24 "
                  "September 2026 they stay playable. Streamers who play "
                  "copyrighted music are the usual source of claims."),
            ("h", "What clip channels do on YouTube"),
            ("list", (
                "Use Shorts to grow an audience, not as the income.",
                "Earn from long-form: weekly or per-stream compilations with a story to them.",
                "Run an official clip channel with the streamer's permission, which also settles Content ID.",
            )),
            ("note", "Checked on " + CHECKED_ON + ". YouTube Help is the authority."),
        ),
        sources=(
            Source("YouTube Help — Channel monetization policies", "https://support.google.com/youtube/answer/1311392?hl=en"),
            Source("YouTube Help — Shorts monetization policies", "https://support.google.com/youtube/answer/12504220?hl=en"),
            Source("YouTube Help — Three-minute Shorts", "https://support.google.com/youtube/answer/15424877?hl=en"),
            Source("Tubefilter — Stricter Shorts ad eligibility (August 2026)", "https://www.tubefilter.com/2026/08/10/youtube-partner-program-ad-eligibility-requirements-shorts/"),
            Source("Social Media Today — Inauthentic content clarified", "https://www.socialmediatoday.com/news/youtube-clarifies-monetization-update-inauthentic-repeated-content/752892/"),
            Source("Creatipi — How the Shorts Creator Pool works", "https://www.creatipi.com/blog/youtube-shorts-revenue-sharing-explained/"),
            Source("AIR Media-Tech — Shorts RPM vs long-form", "https://air.io/en/air-data-findings/youtube-shorts-rpm-vs-long-form-how-much-do-shorts-earn-in-2026"),
            Source("vidIQ — YouTube view count update", "https://vidiq.com/blog/post/youtube-view-count-update/"),
        ),
    ),
    Article(
        slug="find-streamer-campaigns",
        title="Where to find clipping campaigns for streamers, not products",
        description=("Where clippers find paid campaigns from streamers rather "
                     "than brands: marketplaces, streamers' own clipping programs, "
                     "Discord hubs and direct deals — and what each pays."),
        kicker="Finding work",
        lead=("Most streamer clipping runs through Discord, not marketplaces. "
              "Four places to look, and what to expect from each."),
        blocks=(
            ("h", "1. Marketplaces, filtered for creators"),
            ("p", "Whop Content Rewards has the most streamer campaigns, mixed in "
                  "with brands. Clipify is built around streamers. Ssemble lists "
                  "streamers alongside podcasts. [The platform guide](/blog/clipping-platforms) "
                  "has each one's requirements."),
            ("h", "2. Streamers' own clipping programs"),
            ("p", "Bigger streamers and organisations run open programs: join "
                  "their Discord, clip from their streams or VODs, and submit "
                  "against a published rate card. Look for a “clipping program” "
                  "or “clip team” link in their Discord, X or stream panels."),
            ("req", (
                ("Per view", "Typically $0.50–$2 per 1,000, often with a 100,000-view minimum before payout"),
                ("Per clip", "A flat fee for each accepted, posted clip"),
                ("Bounty pool", "A fixed prize split among the best clips of a stream or week"),
            )),
            ("h", "3. Clipping Discord hubs"),
            ("p", "Servers such as Clip Money, Clipster and /clipping post "
                  "streamer jobs, and DISBOARD's “clipping” tag lists more."),
            ("h", "4. Ask a streamer directly"),
            ("p", "Offer a mid-size streamer — a few hundred to a few thousand "
                  "viewers — to run their official clips account. It is the "
                  "steadiest arrangement there is, and the clips are posted with "
                  "the owner's permission."),
            ("h", "Two warnings"),
            ("list", (
                "Kick gambling campaigns advertise $10 or more per 1,000 views. "
                "They are streamer campaigns that promote casino sponsors, and "
                "TikTok and YouTube restrict gambling promotion.",
                "Paid clips are ads. Disclose them on the video, and turn on "
                "TikTok's commercial-content setting where it applies.",
            )),
            ("h", "Where Highlightz fits"),
            ("highlightz",),
            ("shot", "library"),
            ("note", "Checked on " + CHECKED_ON + "."),
        ),
        sources=(
            Source("OpenClip — Get paid to clip streamers", "https://openclip.app/guides/get-paid-to-clip-streamers"),
            Source("FindClout — Streamer clipping in 2026", "https://findclout.com/blog/streamer-clipping-guide"),
            Source("ClipSpeed — How to make money clipping streamers", "https://www.clipspeed.ai/blog/make-money-clipping-streamers.html"),
            Source("Sx Bot Docs — Get paid to clip on Discord", "https://docs.sxbot.io/clipify/get-paid-to-clip-on-discord"),
            Source("DISBOARD — Servers tagged clipping", "https://disboard.org/servers/tag/clipping"),
            Source("ClipAffiliates — Whop and Vyro alternatives", "https://www.clipaffiliates.com/blog/whop-vyro-clipping-alternatives-2026"),
            Source("Posthype — Clipping and FTC disclosure", "https://www.posthype.news/article/clipping-ftc-disclosure"),
        ),
    ),
)

INDEX_TITLE = "Making money clipping"
INDEX_LEAD = ("Plain guides to getting paid for clips: the platforms, what they "
              "pay, what they require, and where Highlightz helps. Every figure "
              "is dated and linked to its source.")
INDEX_DESC = ("Guides to making money clipping streams: clipping marketplaces "
              "and their requirements, TikTok Creator Rewards, YouTube Shorts, "
              "and where to find streamer campaigns.")


def article(slug: str) -> Article | None:
    return next((a for a in ARTICLES if a.slug == slug), None)


def paths() -> tuple:
    """Every public blog URL — the index and each article. The router, the
    auth allowlist, the sitemap and the tests all read this one list."""
    return ("/blog",) + tuple("/blog/" + a.slug for a in ARTICLES)
