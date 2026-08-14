"""Every word and every image on /tutorial, in one place.

WHY A DATA FILE. The page's layout code renders this list and nothing else, so
copy and screenshots can be rewritten without going near the HTML — and, more
importantly, so the tutorial's claims sit somewhere a test can read. The whole
failure mode of a walkthrough is documenting a button that no longer exists;
tests/test_tutorial.py asserts that every label quoted below is still present
in the live dashboard source.

SCOPE IS DELIBERATE. The Clip Editor, the Scheduler and auto-captions are NOT
documented here. Captions are off in production (CAPTIONS_ENABLED is unset, so
settings.captions_enabled is False and the panel does not render); the Editor
and Scheduler were excluded by the owner. Documenting a tab a reader cannot
open is worse than omitting it — they go looking, fail, and stop trusting the
rest of the page.

EVERY LABEL IN **BOLD** IS COPIED FROM THE REAL UI. "Monitor stream", "Scan
VOD", "search a streamer", the nine preset names, the empty-state sentences —
all lifted from src/dashboard/aurora_html.py rather than invented, so a reader
can pattern-match the page against their screen.

MEDIA. Paths are relative to /static/tutorial/. A missing file is not an error:
the renderer draws a labelled placeholder instead, so the page ships and stays
readable before a single screenshot is captured. Regenerate with
`node scripts/capture_tutorial_media.mjs`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Media:
    """One image or video slot.

    `caption` is not decoration — it is the text description that makes a video
    usable with sound off, and it is rendered visibly beneath every video
    rather than hidden in an attribute.
    """
    src:     str                 # "04-add-stream.png" — bare filename
    alt:     str                 # real alt text, never "screenshot"
    kind:    str = "image"       # "image" | "video"
    caption: str = ""            # shown under video; required for kind="video"
    poster:  str = ""            # video poster frame; defaults to <stem>-poster.jpg
    width:   int = 1440          # intrinsic size, so the box reserves space
    height:  int = 900           # and nothing shifts while it loads

    @property
    def poster_src(self) -> str:
        if self.poster:
            return self.poster
        return self.src.rsplit(".", 1)[0] + "-poster.jpg"

    @property
    def stem(self) -> str:
        return self.src.rsplit(".", 1)[0]


@dataclass(frozen=True)
class Section:
    id:    str                          # anchor + TOC target
    nav:   str                          # short label for the TOC
    title: str
    body:  str = ""
    steps: tuple[str, ...] = ()         # numbered, using real UI labels
    media: Media | None = None
    tip:   str = ""                     # the "Tip" callout
    plan:  str = ""                     # "" | "Pro" — renders a plan chip
    note:  str = ""                     # caveat rendered under the steps


# ── Hero ─────────────────────────────────────────────────────────────────────

HERO_TITLE = "How to use Highlightz"
HERO_LEAD = (
    "Highlightz watches a live Twitch stream and clips the good parts by itself. "
    "This page walks through every screen — from connecting your account to "
    "approving your first clip — using the exact buttons you will see."
)
# STILLS, NOT VIDEO. Screen recordings of this dashboard came out soft and
# juddery: Chrome's screencast only emits a frame when the page repaints, so a
# mostly-static UI captures at ~16fps no matter how it is encoded. A screenshot
# has none of those problems — it is pixel-exact, loads instantly, and a reader
# following along wants to compare a still against their own screen anyway.
HERO_MEDIA = Media(
    src="00-overview.png",
    alt="The Highlightz dashboard on the Live Streams tab: a monitored channel with "
        "a live trigger score of 94, its score chart climbing past the threshold, "
        "and the clips it has caught listed below.",
)


# ── Get started in 4 steps ───────────────────────────────────────────────────

QUICKSTART_TITLE = "Get started in 4 steps"
QUICKSTART_LEAD = (
    "Signing in is free and takes about a minute. You do not need a card, and "
    "you can be monitoring a live channel before you finish reading this page."
)

QUICKSTART: tuple[Section, ...] = (
    Section(
        id="qs-signin",
        nav="Sign in",
        title="Sign in with Twitch",
        body=(
            "Highlightz has no separate password. You sign in with the Twitch "
            "account the clips should belong to, because clips are created "
            "under that account through Twitch's official API."
        ),
        steps=(
            "Click **Get started** in the top-right of the homepage.",
            "On the sign-in screen, click **Continue with Twitch**.",
            "Twitch asks you to authorise Highlightz. Approve it.",
        ),
        media=Media(
            src="01-signin.png",
            alt="The Highlightz sign-in card with a Continue with Twitch button and a "
                "badge reading 7 days free, no card required.",
        ),
        tip=(
            "Sign in with the account you want the clips on. Whichever Twitch "
            "account you authorise is the one every clip gets attributed to."
        ),
    ),
    Section(
        id="qs-welcome",
        nav="First look",
        title="Read the welcome card",
        body=(
            "The first time the dashboard loads you get a short explainer of how "
            "the detector works. It is worth thirty seconds — it is the clearest "
            "summary of why clips fire when they do."
        ),
        steps=(
            "Skim the five numbered points.",
            "Click **Start clipping** to dismiss it.",
        ),
        media=Media(
            src="02-welcome.png",
            alt="The welcome overlay listing five numbered points about how Highlightz "
                "scores a stream, with a Start clipping button.",
        ),
    ),
    Section(
        id="qs-add",
        nav="Add a channel",
        title="Add a channel to monitor",
        body=(
            "Nothing happens until Highlightz has a channel to watch. It does not "
            "have to be your own — you can monitor any live Twitch channel that "
            "has not opted out."
        ),
        steps=(
            "Open the **Live Streams** tab.",
            "Type a name into **search a streamer**. Suggestions appear as you type.",
            "Pick a preset that matches the content — see the list below.",
            "Click **Monitor stream**.",
        ),
        media=Media(
            src="03-add-stream.png",
            alt="The Add a stream panel with a channel typed into the search box, the "
                "preset dropdown open, and the Monitor stream button below it.",
        ),
        tip=(
            "The channel has to be live for anything to happen. Adding an offline "
            "channel is fine — it simply waits, and starts scoring the moment "
            "that streamer goes live."
        ),
    ),
    Section(
        id="qs-approve",
        nav="Approve a clip",
        title="Approve your first clip",
        body=(
            "Clips do not go anywhere until you say so. Every capture lands in "
            "**Clip Review** and waits for you."
        ),
        steps=(
            "Open the **Clip Review** tab.",
            "Watch the clip in place.",
            "Click **Approve** to keep it, or **Reject** to bin it.",
        ),
        media=Media(
            src="04-approve.png",
            alt="A pending clip in the review queue with its trigger score, title and "
                "channel, and the Approve and Reject buttons beneath it.",
        ),
        tip=(
            "Rejecting is not wasted effort. Every reject raises that channel's "
            "bar and every approve lowers it, so the detector tunes itself to your "
            "taste as you go."
        ),
    ),
)


# ── One section per feature ──────────────────────────────────────────────────

FEATURES: tuple[Section, ...] = (
    Section(
        id="live-streams",
        nav="Live Streams",
        title="Live Streams",
        body=(
            "This is the control room: every channel you are watching, its live "
            "trigger score, and the chart of how that score has been moving. "
            "A channel scores against **its own** normal, so a quiet chess stream "
            "and a loud FPS stream fire at the same fairness."
        ),
        steps=(
            "Type a channel into **search a streamer** and pick it from the list.",
            "Choose the preset that fits: **Default**, **Small streamer**, **FPS**, "
            "**MOBA**, **Chess / Strategy**, **Casino / Gambling**, **IRL / Outdoor**, "
            "**Variety / Just Chatting** or **Sports**.",
            "Click **Monitor stream**. It appears under **Monitored streams**.",
            "To stop watching a channel, remove it from that list.",
        ),
        media=Media(
            src="05-live-streams.png",
            alt="The Live Streams tab showing a monitored channel with a live trigger "
                "score and a chart of the score over time.",
        ),
        tip=(
            "The preset only sets the starting point. Highlightz keeps learning the "
            "channel after that, so picking the closest match is good enough — you "
            "do not need to get it perfect."
        ),
        note=(
            "Before you see anything move, the channel has to be live. An empty "
            "list reads **No streams yet. Add one to start monitoring.**"
        ),
    ),
    Section(
        id="clip-review",
        nav="Clip Review",
        title="Clip Review",
        body=(
            "Everything the detector catches lands here first. Nothing is published, "
            "deleted or posted anywhere until you decide — this queue is the only "
            "way a clip reaches your library."
        ),
        steps=(
            "Open **Clip Review**.",
            "Filter with **All**, **Pending** or **Approved**.",
            "Re-order with **Newest** or **Top Virality**.",
            "With more than one channel, narrow it using the **All streamers** dropdown.",
            "Click **Approve** or **Reject** on each clip.",
        ),
        media=Media(
            src="06-clip-review.png",
            alt="The Clip Review queue with pending clips, filter buttons for All, "
                "Pending and Approved, and Approve and Reject buttons on each clip.",
        ),
        tip=(
            "**Top Virality** is the fast way to work a big queue — it floats the "
            "clips most likely to travel to the top, so the best ones get seen even "
            "if you never reach the bottom."
        ),
        note=(
            "An empty queue reads **Waiting for clips** — *Add a channel on the Live "
            "Streams tab, clips appear here the moment a highlight fires.*"
        ),
    ),
    Section(
        id="clip-library",
        nav="Clip Library",
        title="Clip Library",
        body=(
            "Everything you approved, newest approval first. This is the keep pile — "
            "clips you rejected never arrive here, and pending clips stay in review "
            "until you rule on them."
        ),
        steps=(
            "Open **Clip Library**.",
            "Filter by channel if you are monitoring several.",
            "Open any clip to watch it or copy its Twitch link.",
        ),
        media=Media(
            src="07-clip-library.png",
            alt="The Clip Library grid showing approved clips with their titles, "
                "channel names and trigger scores.",
        ),
        tip=(
            "Order is by when you **approved** a clip, not when it was captured. "
            "Approve something from last week and it goes to the front, which is "
            "usually where you want it."
        ),
    ),
    Section(
        id="vod-scanner",
        nav="VOD Scanner",
        title="VOD Scanner",
        plan="Pro",
        body=(
            "The detector normally works live. The VOD Scanner runs the same scoring "
            "over a broadcast that has already finished, so you can mine streams from "
            "before you signed up — or catch the ones you missed."
        ),
        steps=(
            "Open the **VOD Scanner** tab.",
            "Paste a Twitch VOD URL, like **https://www.twitch.tv/videos/123456789**.",
            "Pick a preset, same list as Live Streams.",
            "Click **Scan VOD** and let it work — it shows progress as it goes.",
            "Each moment it finds links straight to that timestamp.",
        ),
        media=Media(
            src="08-vod-scanner.png",
            alt="The VOD Scanner tab with a Twitch VOD URL pasted into the field and "
                "the Scan VOD button beside it.",
        ),
        tip=(
            "Scanning a long VOD takes a while, and the job keeps running if you "
            "switch tabs. Start it, go do something else, come back to the results."
        ),
        note=(
            "The VOD Scanner is on **Pro**. On Free or Starter the tab explains "
            "the upgrade rather than failing silently."
        ),
    ),
    Section(
        id="settings",
        nav="Settings",
        title="Settings",
        body=(
            "Where you tune how eager the detector is. If it is catching too much or "
            "too little on a channel, this is the dial — not a setting you have to "
            "touch to get started."
        ),
        steps=(
            "Open the **Settings** tab.",
            "Adjust the trigger sensitivity for a channel.",
            "Review storage and workflow options.",
        ),
        media=Media(
            src="09-settings.png",
            alt="The Settings tab showing trigger, storage and workflow controls.",
        ),
        tip=(
            "Try approving and rejecting for a session before changing anything here. "
            "The detector already moves toward your taste on its own, and that is "
            "usually enough."
        ),
    ),
    Section(
        id="account",
        nav="Account & plans",
        title="Account and plans",
        body=(
            "Your plan, your billing and your connected accounts. Highlightz is free "
            "to start and stays free — paid plans raise the limits, they do not "
            "unlock the basic product."
        ),
        steps=(
            "Open the **Account** tab to see **Plan status** and **Membership**.",
            "**Upgrade to Pro** starts a Stripe checkout.",
            "**Manage billing** opens the Stripe portal to change or cancel a plan.",
            "**Delete my account** removes your data permanently.",
        ),
        media=Media(
            src="10-account.png",
            alt="The Account tab showing plan status, a membership summary, and "
                "buttons to upgrade and manage billing.",
        ),
        tip=(
            "Cancelling does not delete anything. You drop to Free and keep your "
            "library — only the limits change."
        ),
    ),
)


# ── Plans table ──────────────────────────────────────────────────────────────
# Mirrors src/billing/plans.py PLAN_LIMITS. A test asserts these numbers match,
# because a pricing table that drifts from the enforcement is worse than none.

PLANS_TITLE = "What each plan gives you"
PLAN_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("",                  "Free",  "Starter", "Pro"),
    ("Price",             "$0",    "$10/mo",  "$25/mo"),
    ("Channels at once",  "1",     "3",       "10"),
    ("Clips held for review", "15", "50",     "200"),
    ("VOD Scanner",       "—",     "—",       "Yes"),
)


# ── Troubleshooting ──────────────────────────────────────────────────────────
# Sourced from the REAL error responses in src/dashboard/api.py, not imagined.

FAQ_TITLE = "Common questions"
FAQ_LEAD = "The things that actually come up, and what they mean."

FAQ: tuple[tuple[str, str], ...] = (
    ("I added a channel but nothing is happening.",
     "The channel has to be <b>live</b>. An offline channel sits waiting and starts "
     "scoring by itself the moment that streamer goes on air. If they are live and "
     "the score is still flat, give it a few minutes — Highlightz learns what is "
     "normal for a channel before it decides what counts as a spike."),

    ("It says the stream limit is reached.",
     "You are monitoring as many channels as your plan allows — <b>1</b> on Free, "
     "<b>3</b> on Starter, <b>10</b> on Pro. Remove a channel to free a slot, or "
     "upgrade from the Account tab."),

    ("It says that streamer has opted out.",
     "Streamers can ask not to be clipped through Highlightz, and that request is "
     "honoured everywhere. You cannot monitor a channel that has opted out. Your own "
     "channel is never blocked from you."),

    ("It says the stream is already registered.",
     "That channel is already on your Monitored streams list. Check the list on the "
     "Live Streams tab — you do not need to add it twice."),

    ("Why does the VOD Scanner say it is a Pro feature?",
     "Because it is. The VOD Scanner is included on <b>Pro</b>. Live monitoring, "
     "review and your library all work on every plan, including Free."),

    ("The VOD link was not accepted.",
     "It needs a full Twitch VOD URL in the form "
     "<b>https://www.twitch.tv/videos/123456789</b>. A clip link or a channel link "
     "will not work — the scanner reads a past broadcast."),

    ("Are clips actually posted to my Twitch?",
     "A clip is created on Twitch, under your account, through the official API — "
     "the same thing that happens when you press Twitch's own Clip button. It is not "
     "posted publicly or shared anywhere by us. Approving a clip keeps it in your "
     "library; it does not broadcast it."),

    ("Do you record my stream?",
     "No. Highlightz never records, downloads or re-hosts your video. It asks Twitch "
     "to make a clip at the right moment, and Twitch hosts it."),

    ("Can I use this on a channel that is not mine?",
     "Yes, as long as that streamer has not opted out. A lot of people run Highlightz "
     "on channels they clip for rather than their own."),

    ("Is Kick supported?",
     "Not yet. Kick appears in the app but automated Kick clipping is still being "
     "built, so those tabs are closed off rather than half-working."),
)


# ── Closing ──────────────────────────────────────────────────────────────────

CTA_TITLE = "Start clipping"
CTA_BODY = (
    "7 days free, no card required. Add your channels and let it watch a stream — "
    "that is the fastest way to see whether the detector works on your content."
)
CTA_BUTTON = "Start clipping now"

SUPPORT_TITLE = "Still stuck?"
SUPPORT_BODY = (
    "Use the <b>Feedback</b> tab inside the dashboard — pick "
    "<b>Question</b> or <b>Bug report</b> and it comes straight to us. "
    'Not signed in? Email <a href="mailto:support@highlightz.app">support@highlightz.app</a>.'
)


def all_sections() -> tuple[Section, ...]:
    """Everything with an anchor, in page order — what the TOC is built from."""
    return QUICKSTART + FEATURES


def all_media() -> tuple[Media, ...]:
    out = [HERO_MEDIA]
    out += [s.media for s in all_sections() if s.media]
    return tuple(out)
