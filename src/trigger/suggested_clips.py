"""
Suggested clips — the crowd's picks, surfaced without asking what we scored.

WHY THIS EXISTS. Clips went viral off channels we were watching and the bot
never surfaced them. Every existing path into the review queue runs through a
score: the live trigger fires above a learned threshold, the VOD scanner ranks
by its own formula. So a moment the formula undervalues is invisible, and the
measurements in `src/maintenance/analyze_virality.py` say the formula
undervalues plenty — the trigger score correlates -0.060 with human-judged
virality within a labeler, and its AUC within an account is 0.547, which is a
coin flip with a limp. A path that never consults the score is not a nice-to-
have; it is the only way a missed moment can reach the user at all.

WHAT IT WATCHES. Viewers. When somebody watching the stream hits Clip, a human
has decided the moment mattered — which is the judgment every signal in this
project is a weak proxy for. Those clips are already visible to us: the poll in
`viewer_clips.py` has been reading them for learning since Phase 0, ~79k of
them. This module is the same data finally being allowed to act.


THE ONE SUBSTITUTION, AND WHY IT IS NOT A COMPROMISE
────────────────────────────────────────────────────
The obvious build is "see viewers clipping, create our own clip of that
moment". It cannot work, and that is measured rather than assumed. Phase 0
timed how long Twitch takes to make a viewer's clip visible in Helix:

    stableronaldo, 89 clips over 25 clean minutes:
      median 167.4s   p90 369.3s   max 461.1s   min 10.8s

Create Clip reaches back roughly 60-90 seconds from live. By the time a viewer
clip is visible to anybody, the moment is out of the buffer on more than nine
runs in ten, so "clip it ourselves" would reliably capture the aftermath — the
chat reacting to a thing that is no longer on screen. The <45s bar for acting
on this was written down BEFORE that data was collected, precisely so it could
not be argued away afterwards, and it failed by a factor of four.

So we do not create anything. **The viewer's clip is already a real Twitch
clip** — hosted by Twitch, with a slug, an embed URL, a thumbnail and a title.
We surface THAT. It is strictly better than what we would have made:

  * it is the right 30 seconds, because a human framed it
  * latency stops mattering entirely, because the artifact already exists
  * it costs zero Create Clip calls against a globally shared Helix budget
  * it stays inside the compliance model — Twitch hosts it, we store metadata
    and an embed URL, exactly as we already do for our own clips

This is also precisely what the VOD scanner does. `get_clips_for_vod` merges
viewer clips into a scan as first-class moments and the UI badges them "N
clipped it"; the scanner does not create Twitch clips either. This module is
that same idea running live instead of after the broadcast.

WHAT IT COSTS: nothing. `viewer_clips.poll_and_record` already calls Helix
every POLL_INTERVAL per channel. This module is a sink on that existing call —
it adds no request, and the budget is per client-id shared across every user,
so that mattered.


THE DELAY
─────────
A candidate ripens for `SETTLE_SECS` before it can be suggested, which buys two
things that are worth more than promptness on a queue a human reviews in
batches:

1. **Corroboration.** The poll window is wider than the poll interval, so the
   same clip comes back on several consecutive polls. Waiting lets other
   viewers land on the same moment, and a moment three people clipped is a
   different proposition from one somebody clipped by accident. That count
   ships with the suggestion.
2. **View counts.** A clip is minutes old before we ever see it and has
   essentially no views at first sighting. Re-reading it across the next couple
   of polls costs nothing and gives a real number to rank by.

Nothing about the delay is latency-critical: these are suggestions for a review
queue, not a live trigger.


WHAT IT MUST NOT DO
───────────────────
* **Never learn from itself.** Our own clips are real Twitch clips and come
  back in the same Get Clips response. They are excluded by creator id and by
  stored slug, the same two ways `viewer_clips.py` excludes them.
* **Never crowd out real clips.** These land in the same pending queue, which
  is plan-capped, and a full queue drops the NEWEST clip. A clip-happy chat
  could therefore starve the thing the user actually pays for, so suggestions
  are rate-capped per channel AND the caller holds back a reserve of the
  pending queue. A suggestion is a bonus; it never costs a real clip.
* **Never suggest the same moment twice.** Emitted moments are remembered by
  time range, not just by slug — otherwise a fourth viewer clipping the same
  moment 200 seconds later forms a fresh cluster and suggests it again.
"""

import time

import structlog

log = structlog.get_logger(__name__)

# How long a candidate ripens from FIRST SIGHTING before it may be suggested.
# See "THE DELAY" above: this buys corroboration and view counts, and costs
# nothing because these are review-queue suggestions rather than a live
# trigger. Two-ish extra polls at the current 90s interval.
SETTLE_SECS = 180.0

# Clips whose creation times fall within this of each other are treated as the
# same moment. A Twitch clip covers ~30s and viewers hit the button at
# different points in their own reaction, so the spread is roughly one clip
# length plus a few seconds of human variance.
CLUSTER_SECS = 45.0

# Distinct viewers who must have clipped a moment. One is deliberate: on a
# 200-viewer channel almost nothing gets clipped twice, and those are exactly
# the users for whom a missed moment hurts most. The clipper count rides along
# on the suggestion so the UI can say how many, rather than being used to
# refuse the ones that only one person caught.
MIN_CLIPPERS = 1

# Per channel, not per user — five users watching one streamer share the poll,
# so they share the cap too. A busy chat can produce dozens of clips an hour
# and the review queue is a human's attention, not a log.
#
# RAISED 2026-08-26, 6 -> 12. THIS is the number that decides how many
# suggestions a user actually receives; the per-plan `max_suggested` budgets
# only decide how many may sit unreviewed at once, so for anyone who works
# through their queue this cap was the binding one and raising the other alone
# would have changed nothing they could see. Doubling it is deliberate: these
# are moments a human framed, they hold up better than the detector's own
# picks, and the product leans on them on purpose.
#
# WHAT STILL PROTECTS THE QUEUE. Nothing about this can starve a triggered
# clip — suggestions draw on their own budget, not max_pending — so the only
# thing at risk from a higher cap is the user's attention. That is bounded
# separately by max_suggested, which is why both numbers exist.
MAX_PER_HOUR = 12

# A candidate this old is dropped unsuggested. Covers the case where a cluster
# never ripens because the worker restarted, and stops the buffer growing
# across a long broadcast.
MAX_AGE_SECS = 1800.0


class Suggestion:
    """One moment the crowd picked, ready to land in the review queue."""

    __slots__ = ("slug", "url", "embed_url", "thumbnail_url", "title",
                 "creator", "created_at", "view_count", "clipper_count",
                 "duration", "channel")

    def __init__(self, **kw) -> None:
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}

    def __repr__(self) -> str:          # pragma: no cover - debugging aid
        return (f"<Suggestion {self.slug} {self.channel} "
                f"views={self.view_count} clippers={self.clipper_count}>")


class SuggestionBuffer:
    """Ripens viewer clips for one channel and emits settled moments.

    A class rather than the module-level dicts `viewer_clips.py` uses, because
    the interesting behaviour here is the clustering and the re-suggestion
    guard, and both are far easier to test on an instance with an injected
    clock than on process-global state.
    """

    def __init__(self, channel: str) -> None:
        self.channel = channel
        # slug -> candidate dict. Rewritten on every poll the clip appears in,
        # which is how view counts refresh for free.
        self._pending: dict[str, dict] = {}
        # Slugs already suggested, so a clip still inside the poll window
        # cannot come back as a second suggestion.
        self._emitted: set[str] = set()
        # Creation times of moments already suggested. The slug set alone is
        # not enough: a later clip of the SAME moment has a different slug and
        # would form a fresh cluster.
        self._emitted_moments: list[float] = []
        # Emission times, for the per-hour cap.
        self._emissions: list[float] = []

    # ── intake ───────────────────────────────────────────────────────────────

    def offer(self, rows: list[dict], our_creator_ids: set[str],
              our_slugs: set[str], now: float | None = None) -> int:
        """Take a poll's worth of raw Helix clip rows. Returns how many rows
        are being tracked as new candidates.

        Rows for clips already tracked are UPDATED rather than skipped — the
        poll window is wider than the poll interval, so the same clip returns
        several times and each return carries a fresher view count.
        """
        now = time.time() if now is None else now
        added = 0
        for row in rows or []:
            slug = row.get("id")
            if not slug or slug in self._emitted:
                continue
            # Never suggest our own work back to us. Both checks, because a
            # clip we created carries our user's creator_id AND is in our
            # stored slugs, and either can be missing (a slug we failed to
            # record; a user whose twitch_id we do not hold).
            if str(row.get("creator_id") or "") in our_creator_ids:
                continue
            if slug in our_slugs:
                continue
            ts = _parse_ts(row.get("created_at", ""))
            if not ts:
                continue
            # A moment we already suggested. Checked on intake rather than at
            # emission so these never occupy the buffer at all.
            if self._already_suggested_moment(ts):
                continue

            existing = self._pending.get(slug)
            if existing is None:
                self._pending[slug] = {
                    "slug":       slug,
                    "ts":         ts,
                    "first_seen": now,
                    "creator":    (row.get("creator_name") or "").strip(),
                    "creator_id": str(row.get("creator_id") or ""),
                    "title":      (row.get("title") or "").strip()[:120],
                    "url":        row.get("url") or f"https://clips.twitch.tv/{slug}",
                    "embed_url":  row.get("embed_url")
                                  or f"https://clips.twitch.tv/embed?clip={slug}",
                    "thumbnail_url": row.get("thumbnail_url") or "",
                    "duration":   float(row.get("duration") or 30.0),
                    "view_count": int(row.get("view_count") or 0),
                }
                added += 1
            else:
                # Refresh only what can move. first_seen must NOT be touched or
                # a clip that keeps reappearing never ripens.
                existing["view_count"] = max(existing["view_count"],
                                             int(row.get("view_count") or 0))
                if not existing["thumbnail_url"]:
                    existing["thumbnail_url"] = row.get("thumbnail_url") or ""
        return added

    # ── emission ─────────────────────────────────────────────────────────────

    def ready(self, now: float | None = None) -> list[Suggestion]:
        """Suggestions whose moment has settled. Removes what it returns."""
        now = time.time() if now is None else now
        self._expire(now)

        ripe = [c for c in self._pending.values()
                if now - c["first_seen"] >= SETTLE_SECS]
        if not ripe:
            return []

        out: list[Suggestion] = []
        for cluster in _cluster(ripe):
            if len({c["creator_id"] or c["slug"] for c in cluster}) < MIN_CLIPPERS:
                continue
            if not self._room(now):
                # Cap reached. Drop the whole cluster rather than holding it:
                # keeping it would suggest a stale moment an hour later, and
                # the user asked for what the crowd is clipping NOW.
                for c in cluster:
                    self._pending.pop(c["slug"], None)
                continue

            best = _best_of(cluster)
            clippers = len({c["creator_id"] or c["slug"] for c in cluster})
            out.append(Suggestion(
                slug=best["slug"],
                url=best["url"],
                embed_url=best["embed_url"],
                thumbnail_url=best["thumbnail_url"],
                title=best["title"],
                creator=best["creator"],
                created_at=best["ts"],
                view_count=best["view_count"],
                clipper_count=clippers,
                duration=best["duration"],
                channel=self.channel,
            ))
            # Retire every member, not just the one emitted, or the losers of
            # this cluster ripen alone next pass and suggest the same moment.
            for c in cluster:
                self._pending.pop(c["slug"], None)
                self._emitted.add(c["slug"])
            self._emitted_moments.append(best["ts"])
            self._emissions.append(now)

        if out:
            log.info("clips_suggested", channel=self.channel, count=len(out),
                     slugs=[s.slug for s in out],
                     clippers=[s.clipper_count for s in out])
        return out

    # ── housekeeping ─────────────────────────────────────────────────────────

    def _room(self, now: float) -> bool:
        self._emissions = [t for t in self._emissions if now - t < 3600.0]
        return len(self._emissions) < MAX_PER_HOUR

    def _already_suggested_moment(self, ts: float) -> bool:
        return any(abs(ts - m) <= CLUSTER_SECS for m in self._emitted_moments)

    def _expire(self, now: float) -> None:
        for slug, c in list(self._pending.items()):
            if now - c["first_seen"] > MAX_AGE_SECS:
                del self._pending[slug]
        # These two only ever grow, and a channel can stream for many hours.
        if len(self._emitted) > 5000:
            self._emitted.clear()
        cutoff = now - MAX_AGE_SECS - 3600.0
        self._emitted_moments = [m for m in self._emitted_moments if m > cutoff]

    # Exposed for tests and for the worker's log line.
    @property
    def pending_count(self) -> int:
        return len(self._pending)


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_ts(s: str) -> float:
    """Twitch's RFC3339-with-Z. Same shape as viewer_clips._parse_ts; kept
    local so this module has no import-time dependency on the learning path."""
    from datetime import datetime, timezone
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _cluster(candidates: list[dict]) -> list[list[dict]]:
    """Group candidates that describe the same moment.

    Single-linkage over the creation times: sort, then start a new group
    whenever the gap to the previous clip exceeds CLUSTER_SECS. Chaining is the
    behaviour we want — a run of viewers clipping across a 90-second bit is one
    moment, not two — and a moment long enough to chain far past the window is
    a moment worth one suggestion anyway.
    """
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda c: (c["ts"], c["slug"]))
    groups = [[ordered[0]]]
    for c in ordered[1:]:
        if c["ts"] - groups[-1][-1]["ts"] <= CLUSTER_SECS:
            groups[-1].append(c)
        else:
            groups.append([c])
    return groups


def _best_of(cluster: list[dict]) -> dict:
    """The clip that represents its moment: most-viewed, then earliest.

    Most-viewed because view count is the only quality signal on offer that is
    not ours — and being not-ours is the entire point of this feature. Earliest
    breaks ties because the first clipper reacted fastest and their 30 seconds
    is most likely to contain the setup rather than only the payoff; the slug
    breaks the remaining tie so the choice is deterministic and testable.
    """
    return sorted(cluster, key=lambda c: (-c["view_count"], c["ts"], c["slug"]))[0]


# ── per-channel registry ─────────────────────────────────────────────────────
# Shared across every worker watching a channel, exactly like the poll gate in
# viewer_clips.py: five users on one streamer is one buffer and one cap, not
# five of each.

_buffers: dict[str, SuggestionBuffer] = {}


def buffer_for(channel: str) -> SuggestionBuffer:
    buf = _buffers.get(channel)
    if buf is None:
        buf = _buffers[channel] = SuggestionBuffer(channel)
    return buf


def reset(channel: str | None = None) -> None:
    """Drop buffered state. Tests only.

    Deliberately NOT called when a worker stops. The buffer is keyed by
    channel and shared by every worker watching it, so one user stopping their
    stream would wipe candidates another user's worker is still ripening.
    Staleness across a broadcast boundary is handled by MAX_AGE_SECS instead,
    which does not need to know whose worker is running.
    """
    if channel is None:
        _buffers.clear()
    else:
        _buffers.pop(channel, None)
