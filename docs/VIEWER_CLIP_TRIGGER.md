# Viewer-clip trigger — design plan

**Status: measured. Trigger REJECTED by the data. Learning watcher shipped.**

## Phase 0 result (2026-07-29)

    stableronaldo — 89 clips, 25 clean minutes (backlog excluded)
      median 167.4s   p90 369.3s   max 461.1s   min 10.8s   rate 4.05 clips/min

    jynxzi — 5 usable clips before he went offline
      ~21s, but n=5 from a truncated run; the 89-clip sample is the real number

**Verdict: no trigger.** Create Clip reaches back only ~60s, so acting ~3
minutes late captures the aftermath, not the payoff. The <45s bar was set
BEFORE the data was collected precisely so this call could not be
rationalised afterwards, and 167s is not close.

Latency is irrelevant to the learning use, which looks backward instead of
forward — so that is what shipped: `src/trigger/viewer_clips.py`, plus a
~15-minute score history on the engine (`TriggerEngine.score_at`) so a clip
surfacing minutes late still pairs with what we thought when it was made.
Records to `clips/viewer_clips.jsonl`. It observes only — a test asserts the
module cannot create clips or move a score.

The idea: when real viewers clip a moment on a channel we're monitoring, that
is the strongest highlight evidence that exists. Not inference from chat rate
or loudness — a human watched it and decided it was worth keeping. Every other
signal in this project is a proxy for exactly that judgment.

The 1001 human training scores told us why this matters: `trigger_score`
correlates **+0.081** with human-judged virality. Our proxies barely work.
Viewer clips are not a proxy.

---

## What the research established

| Fact | Consequence for the design |
|---|---|
| `started_at`/`ended_at` accept RFC3339, **seconds ignored** | Minute granularity only; pad windows generously |
| `started_at` **without** `ended_at` defaults the window to **one week** | Always send both, or every poll returns the channel's whole history |
| Results sort by **view count desc**, not recency | A brand-new clip has 0 views and can be buried; keep windows narrow so the result set is small |
| Pagination is unstable when view counts shift | Never paginate for the live path — one tight window, one page |
| `vod_offset` is **null** for clips made during a live broadcast | Irrelevant here — live uses `created_at` (wall clock), which IS populated |
| Clip-appearance latency | **Undocumented. Must be measured.** See Phase 0 |

The `created_at` point is what makes this easier live than in VODs: we don't
need Twitch to resolve a VOD offset, we just need to know *when*, and we're
already watching the stream in real time.

---

## Phase 0 — Measure first (BUILT, not yet run)

`src/maintenance/measure_clip_latency.py`

```
venv/bin/python -m src.maintenance.measure_clip_latency jynxzi --minutes 20
```

Read-only. Polls Get Clips every 5s against a live channel and reports:

* **Latency distribution** — `created_at` vs. when the clip became visible to us
* **Clip rate baseline** — clips/min, and the busiest minute
* **How many clips were ours** — proving the self-exclusion filter works

### The latency number decides the whole design

Create Clip captures roughly the last 60s of broadcast at call time. If we
learn about a viewer's clip `L` seconds late, our capture window has slid
forward by `L`.

* **L < 20s** → the moment is still inside our window. Build the **trigger**.
* **L = 20–45s** → we'd catch the reaction, not the payoff. Strong **scoring
  signal**; trigger only on multi-viewer consensus, where the evidence is
  worth the timing cost.
* **L > 45s** → too late to re-clip. **Learning signal only** — score the
  moments viewers clipped and auto-tune the channel threshold toward them.
  Do not wire it to a trigger.

**Do not skip this.** Every alternative design is defensible; which one is
correct is decided by one measured number.

---

## Phase 1 — The watcher (safe, additive)

A per-channel `ViewerClipWatcher`, polling Get Clips on the existing
`_viewer_poll_loop` cadence in `stream_worker.py` (already runs every 60s).

**Deduped per channel, not per user.** If five users monitor jynxzi that must
be one poller feeding five engines. Without this, rate limits and cost scale
with users instead of channels.

**Exclusion set (non-negotiable):** our own clips are real Twitch clips and
appear in this same API. Filter by `creator_id ∈ {our users' twitch_ids}` **and**
`slug ∈ {twitch_clip_id in clips.json}`. Miss this and the bot detects its own
clip, fires again, clips again — an infinite self-triggering loop that spams
every user's queue. This is the single most dangerous failure mode in the
feature.

**Normalise against the channel's own baseline.** A channel that always gets
3 clips/min is not interesting at 3 clips/min. The signal is the *spike*, same
philosophy as every other signal here.

**Live gives count, not quality.** A new clip has 0 views, so `view_count`
cannot rank anything live (unlike the VOD path, where it can). The live signal
is consensus — *how many* distinct viewers clipped at once.

---

## Phase 2 — Entering the formula

Two mechanisms, deliberately separate:

### (a) `VIEWER_CLIP` signal — gradual

A normal weighted signal (spike-vs-baseline, 0–1). **The pool must stay at
110** — that is the volume guarantee established by the July retune, and
`test_weight_pool_is_preserved_at_110` enforces it. Weight comes out of the
signals the human data showed are noise (AUDIO_SPIKE, SILENCE_BURST,
EMOTE_HOMOGENEITY, SENTIMENT, KEYWORD).

**The exact weight is set from measured data, not guessed** — same process as
the last retune, validated through `simulate_weights.py` before deploying.

### (b) Consensus tripwire — the game-changer

Mirrors the existing `clip_it_tripwire` (3+ unique chatters saying "clip it"
forces score to `CLIP_IT_FLOOR`). If **N distinct viewers** clip within a short
window, force the score to a high floor that overrides cooldown.

Distinct *creators*, not clips — one person spamming the clip button is not
consensus.

Because the moment already happened `L` seconds ago, a viewer-triggered clip
must use **`post_roll = 0`**: the normal post-roll wait exists to let a moment
finish, and here it has already finished. Getting this wrong throws away the
entire latency budget.

---

## Phase 3 — Validation (the part that makes it trustworthy)

Every detection logs: channel, timestamp, distinct clippers, **our score at
that moment**, and whether we also clipped. That dataset gives us:

* **Precision/recall against crowd ground truth** — the accuracy benchmark this
  project has never had. "Viewers clipped 40 moments; we caught 31."
* **Per-channel threshold auto-tuning** — if viewers consistently clip moments
  we score 45 while our bar sits at 60, the bar is wrong. This is a principled
  fix for the dead-clipping channels (lacy at 80, 225 clips, zero firing).
* **Weight fitting** — the same correlation analysis as the human scores, but
  on thousands of free labels instead of 1001 hand-scored ones.

---

## Risks and how each is handled

| Risk | Mitigation |
|---|---|
| **Self-triggering loop** | Dual exclusion: `creator_id` + our clip slugs. Highest-severity item |
| **Rate limits** (800 pts/min, shared across all users) | Per-channel dedup; adaptive interval (fast when hot, slow when quiet); global call budget |
| **Latency makes clips miss** | Measured in Phase 0; design branches on the result |
| **Clip spam / bot clippers** | Baseline normalisation + distinct-creator counting |
| **Small channels get nothing** | Purely additive — no viewer clips means signal 0 and unchanged behaviour |
| **Viral moment → 20 duplicate clips** | Tripwire still respects a minimum gap |
| **Twitch API down** | Fail soft: signal contributes 0, never blocks normal clipping |

---

## Order of work

1. **Phase 0** — run the latency probe on a busy live channel *(ready now)*
2. Branch on the result: trigger / scoring signal / learning-only
3. **Phase 1** — watcher with dedup + exclusion, logging only, no formula change
4. Let it log for a few days, then measure precision against our own clips
5. **Phase 2** — wire the tripwire and/or signal, weight set from that data
6. Validate with `simulate_weights.py` before deploying, as always

Steps 3–4 are deliberately inert: the watcher runs and logs without changing
what gets clipped, so we learn how good the signal is before trusting it.
