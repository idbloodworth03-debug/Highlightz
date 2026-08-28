# DESIGN_PLAN.md

Phase 0 deliverable, part two. Built on the measurements in `DESIGN_AUDIT.md`.
No code has been written.

The audit changed two things about this plan before it was written. The spacing
is not fighting itself — it was simply never chosen from a system (3 real
specificity conflicts, not a systemic tangle), so Phase 1 is a refactor onto a
scale rather than an untangling. And a third of the product's styling lives in
**359 inline `style={{…}}` objects** that no CSS-variable refactor can reach, so
Phase 1 has a second workstream the brief did not anticipate.

---

## 1. Token system

### 1.1 Spacing — one scale, no exceptions

```
--s-1:   4px      hairline gaps, icon-to-label
--s-2:   8px      inside a control
--s-3:  12px      between related lines
--s-4:  16px      between elements in a group
--s-5:  24px      between groups
--s-6:  32px      card padding
--s-7:  48px      between subsections
--s-8:  64px      section padding, small screens
--s-9:  96px      section padding, desktop
--s-10:128px      reserved: the score wall only
```

671 declarations currently sit off this scale. Every one snaps to the nearest
step. Where a value sits exactly between two steps, it goes **down** — the
current design is uniformly too tight, and rounding up everywhere would inflate
the page by roughly a third.

`--s-10` exists for exactly one purpose and is used exactly once. If it ends up
used twice, the second use is wrong.

### 1.2 Colour — grouping only

The roles are as set out in `DESIGN_AUDIT.md §1.2`. **No hex value changes in
any phase** unless Q1/Q2 below are answered yes. The token layer is introduced
so that a future answer to those questions is a one-line change instead of a
645-literal sweep.

One addition the brief permits: neutral surface and border tokens derived by
opacity from existing colours.

```
--hair:      rgba(255,255,255,.07)    one value, replacing 24 near-identical ones
--hair-firm: rgba(255,255,255,.12)
--sunken:    rgba(0,0,0,.24)
```

### 1.3 Type — 7 steps, no fractional pixels

Fifty sizes become seven. Nine fractional sizes (`9.5 … 16.5`, `12.8`) are
eliminated — they are the mechanism behind "everything is slightly off".

| token | size / line-height | weight | use |
|---|---|---|---|
| `--t-display` | `clamp(40px, 6vw, 72px)` / 1.02 | 800 | hero only |
| `--t-h1` | `clamp(30px, 4vw, 44px)` / 1.1 | 800 | section heads |
| `--t-h2` | `24px` / 1.25 | 700 | subsection heads |
| `--t-h3` | `17px` / 1.4 | 700 | card titles |
| `--t-body` | `16px` / 1.6 | 400 | prose |
| `--t-small` | `14px` / 1.55 | 400 | secondary |
| `--t-caption` | `12px` / 1.4 | 600, `.12em` tracking | eyebrows, axis labels |

Weights collapse to **400 / 600 / 700 / 800**. `650` is retired. Line-heights
collapse from 24 values to the six above.

`--measure: 68ch` caps every prose block. This alone fixes all 14 over-length
blocks, including `.price-tiny` at 177ch and `.faq-more` at 139ch — **no copy
changes needed**, they are width problems.

Numerals: `font-variant-numeric: tabular-nums` is already present in 28 places
and stays. Every live-updating number additionally gets `min-width` in `ch`, so
the container cannot resize when 9 becomes 10.

### 1.4 Motion — one curve, two durations

```
--ease:      cubic-bezier(.22, .61, .36, 1)
--dur-fast:  150ms     hover, focus, press, toggle
--dur-slow:  400ms     entrance, reveal, state change
--dur-event: 900ms     the score wall trigger only
```

Rules, enforced by review:
- `transform` and `opacity` only. The dashboard's transitions on `all`, `left`
  and `width` are rewritten.
- Scroll reveals use `IntersectionObserver`, fire once, translate ≤ 12px.
- `@media (prefers-reduced-motion: reduce)` sets every duration to `1ms` and
  every transform to none, at the token level, in one block per page. The
  paywall currently has zero such blocks and gets one.

No animation library. Everything above is CSS transitions plus one
`IntersectionObserver`; no dependency can be justified.

---

## 2. The score wall

**One paragraph, as asked.**

Today the wall is four independent cards, each with its own sparkline and its
own implicit vertical scale, sitting in a 490px strip that is 9.8% of the page.
The change is to stop drawing four charts and start drawing **one instrument**.
All four channels share a single horizontal threshold datum that spans the full
width of the wall, drawn once and labelled once at the right edge — because each
channel's threshold is different in absolute terms but identical in meaning, and
putting them on one line is what makes "every channel gets its own normal,
judged on the same fairness" a visible fact instead of a caption. Below that
line the traces are quiet hairlines; above it they are solid. A trigger is then
one choreographed event rather than three unrelated animations: the crossing
trace thickens and its numeral scales up 4% (0–200ms), the shared datum pulses
once across the entire wall so you see *which* channel fired relative to the
other three (200–500ms), and a clip chip translates into a rail beneath the wall
(500–900ms) — 900ms total on `--dur-event`, transform and opacity only, and
under reduced motion the chip simply appears with no pulse and no scale. The
wall grows from 490px to roughly 660px, gets `--s-10` of air above and below it,
and everything else on the page is deliberately quieter so this is the only
place on the site where anything moves on its own.

---

## 3. Landing page — reworked rhythm

Same sections, same order, same copy. The change is metering and hierarchy.

```
┌────────────────────────────────────────────────────────────────┐
│ nav                                          [score 51] sign in │  56px, unchanged
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│   AUTOMATIC TWITCH CLIPPING              --t-caption            │
│   [NO AI] A formula you can read.                               │
│                                                                 │  --s-9 (96)
│   Never miss a          --t-display, measure 16ch               │
│   highlight again.                                              │
│                                                                 │
│   Ten streams are live. …          --t-body, --measure          │
│                                                                 │  --s-6 (32)
│   [ Start clipping now ]  [ See the plans ]                     │
│   Free to start · no card · no time limit    --t-caption        │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
                              --s-10 (128)          <- the only 128 on the page
┌════════════════════════════════════════════════════════════════┐
║  THE SCORE WALL                                    ~660px tall  ║
║  ┌──────────┬──────────┬──────────┬──────────┐                 ║
║  │ novafps  │ tessplays│ kettle…  │ arcade…  │                 ║
║  │    48    │    33    │    38    │    51    │  --t-display     ║
║  │          │          │          │      ▲   │  tabular, ch-fixed║
║  ╠══════════╪══════════╪══════════╪══════════╣ ← ONE datum,     ║
║  │  ~~~~    │   ~~~    │  ~~~~    │   ╱      │   spans the wall ║
║  │          │          │          │  ╱       │   labelled once  ║
║  └──────────┴──────────┴──────────┴──────────┘        THRESHOLD ║
║  ┌ just clipped ─────────────────────────────┐                  ║
║  │ ▸ arcadeghost · 51 · 2s ago               │ ← chip slides in ║
║  └───────────────────────────────────────────┘                  ║
╚════════════════════════════════════════════════════════════════╝
                              --s-10 (128)
┌────────────────────────────────────────────────────────────────┐
│         10                7                 1s                  │  stat row
│  channels watched   signals blended   every second scored       │  --s-9 / --s-9
└────────────────────────────────────────────────────────────────┘
                              --s-9 (96)
┌────────────────────────────────────────────────────────────────┐
│  How it works                                    --t-h1         │
│                                                                 │
│  ┌───────────────────────┬──────────────────────────────────┐   │
│  │ STEP ONE              │                                   │   │
│  │ Add the channels      │      [ the signal figure ]        │   │
│  │ …                     │                                   │   │  figure is
│  │                       │      CHAT SPEED    ▰▰▰▰▱▱         │   │  ONE column,
│  │ STEP TWO              │      AUDIO SPIKES  ▰▰▰▱▱▱         │   │  steps stack
│  │ Every second scores   │      KEYWORDS      ▰▰▱▱▱▱         │   │  in the other
│  │ …                     │      VIEWER SURGE  ▰▰▰▰▰▱         │   │
│  │                       │      HYPE          ▰▰▰▱▱▱         │   │  → both
│  │ STEP THREE            │                84                 │   │  columns end
│  │ It clips. You decide. │      one live score               │   │  level
│  │ …                     │                                   │   │
│  └───────────────────────┴──────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
                              --s-9 (96)
┌────────────────────────────────────────────────────────────────┐
│  What you get                                    --t-h1         │
│                                                                 │
│  ── Clips are made by Twitch, not by us ──   LEAD, full width   │
│     --t-h2, prose at --measure                 (the one that    │
│                                                 carries risk)   │
│                              --s-7 (48)                         │
│  ── HOW MUCH IT WATCHES ──                    --t-caption rule  │
│     Ten channels at once   One queue for all   Streams ended    │
│     --t-h3 + --t-small, three across, equal                     │
│                              --s-7 (48)                         │
│  ── HOW IT DECIDES WHAT IS GOOD ──                              │
│     Every channel gets…    A preset for…                        │
│                              --s-7 (48)                         │
│  ── WHAT STAYS UNDER YOUR CONTROL ──                            │
│     Nothing leaves…        Work a busy day…                     │
└────────────────────────────────────────────────────────────────┘
                              --s-9 (96)
┌────────────────────────────────────────────────────────────────┐
│  Pricing                                                        │
│  Start on the free plan…              --t-body at --measure     │
│  ┌───────────┬───────────┬───────────┐                          │
│  │ Free  $0  │ Starter$10│ Pro   $25 │  same padding (--s-6)    │
│  │ 1 channel │ 3 channels│ 10 chan.  │  same radius             │
│  │ 30 kept   │ 100 kept  │ Unlimited │  emphasis via border +   │
│  │ …         │ …         │ …         │  --brand-glow, not size  │
│  ├───────────┼───────────┼───────────┤                          │
│  │[Start free]│[Get Start]│[ Get Pro ]│ ← ONE baseline           │
│  └───────────┴───────────┴───────────┘                          │
│  Move between them whenever…      --measure, was 177ch          │
└────────────────────────────────────────────────────────────────┘
                              --s-9 (96)
┌────────────────────────────────────────────────────────────────┐
│  Questions                                                      │
│  USING IT              │  THE FINE PRINT                        │
│  ▸ Can I clip channels…│  ▸ Is this allowed on Twitch?          │
│  answers at --measure  │                                        │
└────────────────────────────────────────────────────────────────┘
                              --s-9 (96)
┌────────────────────────────────────────────────────────────────┐
│              Ten streams are live right now.                    │
│              You can only watch one.        --t-h1              │
│              [ Start clipping now ] [ Read the walkthrough ]    │
└────────────────────────────────────────────────────────────────┘
```

**The one deliberate rhythm break** is `--s-10` (128px) above and below the
score wall, where every other section boundary is `--s-9` (96px). That extra air
is the entire mechanism by which the wall reads as the subject rather than as
one more block. Nothing else on the page is allowed to break the rhythm.

**Pricing:** the escalating padding (22/26/32) and radii (3/4/7) that produce the
current 10px CTA stagger are removed. All three cards get `--s-6` padding and
the same radius; Pro's emphasis comes from a `--brand-glow` border and its
existing larger price figure, not from being a physically bigger box. The CTAs
land on one baseline via `margin-top:auto` in a flex column.

---

## 4. Post-login states

### 4A — First run, no channels

```
┌────────────────────────────────────────────────────────────────┐
│ HIGHLIGHTZ                                       ● connected    │
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│                                                                 │
│                    Add a channel to watch.       --t-h1         │
│                                                                 │
│         ┌──────────────────────────────┐ ┌──────────┐           │
│         │ twitch.tv/                   │ │  Watch   │           │
│         └──────────────────────────────┘ └──────────┘           │
│                                                                 │
│         Any live channel. Yours or someone else's.  --t-small   │
│                                                                 │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

One input, one button, one line of context. Nothing else renders — no nav
tabs, no zeroed stat row, no empty panels. The 250-word `WelcomeOverlay` is
retired; what it explained is on the landing page, the tutorial, and — for the
one claim that matters — in the sequence the user is about to watch.

### 4B — Channel just added (the 1.5s sequence)

```
 0ms ──────────────────────────────────────────────────────── 1500ms
 │            │                    │                          │
 beat 1       beat 2               beat 3                     resolved
 0–400        400–900              900–1500

 beat 1   the input row translates up and settles as the channel
          chip in the header. transform + opacity only.

 beat 2   the wall frame draws: grid hairlines fade in, then the
          threshold datum sweeps left→right (scaleX 0→1).

 beat 3   the score numeral counts 0 → first real value while the
          trace begins drawing. tabular-nums, ch-fixed width, so
          nothing reflows as digits land.

┌────────────────────────────────────────────────────────────────┐
│ HIGHLIGHTZ            [● novafps]                ● connected    │
├────────────────────────────────────────────────────────────────┤
│  novafps                                          WATCHING      │
│                                                                 │
│      31                                          --t-display    │
│  ════════════════════════════════════════════  threshold 46     │
│      ~~~~~~~~                                                   │
│                                                                 │
│  Watching. Clips appear here the moment one fires.  --t-small   │
└────────────────────────────────────────────────────────────────┘
```

Reduced motion: all three beats resolve instantly to the final frame; the
numeral does not count, it appears.

### 4C — Returning, clips waiting

```
┌────────────────────────────────────────────────────────────────┐
│ HIGHLIGHTZ   Streams  Review•12  Library  Settings              │
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│    12   clips waiting                            --t-display    │
│                                                                 │
│    ▰▰▰▰▰▰▰▰▰▰▰▰▱▱▱▱▱▱  18 of 30 kept this week   --t-caption    │
│                        ↑ only when within 20% of the cap        │
│                                                                 │
│    [ Review them ]                          ONE primary action  │
│                                                                 │
│    3 channels watching · novafps, tessplays, kettlebrook        │
└────────────────────────────────────────────────────────────────┘
```

The count leads at display size. The weekly meter appears **only** when near
the cap — a full-width bar that always says "18 of 30" is a report, and the
brief's rule is one primary action per state.

### 4D — Returning, nothing waiting

```
┌────────────────────────────────────────────────────────────────┐
│ HIGHLIGHTZ   Streams  Review  Library  Settings                 │
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│    Watching 3 channels.                          --t-h1         │
│    Queue is clear.                               --t-small      │
│                                                                 │
│    ┌──────────┬──────────┬──────────┐                           │
│    │ novafps  │tessplays │kettlebrk │  the live wall, working   │
│    │    31    │    22    │    44    │                           │
│    ╞══════════╪══════════╪══════════╡  shared datum             │
│    │  ~~~~    │   ~~     │   ~~~╱   │                           │
│    └──────────┴──────────┴──────────┘                           │
│                                                                 │
│    Last clip 2 hours ago · arcadeghost                          │
│    [ Add another channel ]                  ONE primary action  │
└────────────────────────────────────────────────────────────────┘
```

Reassurance is the live wall itself — visibly moving, which is proof the system
is running. Not an illustration of emptiness.

---

## 5. Self-review — what I changed before showing you this

I went back over the plan looking for anything I would have produced for any
SaaS product. Three things failed that test and were revised:

1. **I had "What you get" as a three-column icon grid.** That is the exact
   AI-default the brief names, and it is also close to what is there now, which
   is the flattest section on the page. Replaced with a **priority ladder**: the
   compliance claim ("Clips are made by Twitch, not by us") gets the full width
   and `--t-h2` because it is the one differentiator a competitor cannot copy
   and the one a cautious buyer needs; the other eight items sit below it in
   their existing groups at equal, smaller weight. Hierarchy by importance, not
   by grid.

2. **I had each wall card drawing its own threshold line.** Generic charting.
   Revised to **one datum spanning all four cards**, because the product's
   actual claim is that different channels are judged on the same fairness — and
   one shared line states that, where four separate lines state the opposite.
   This is the single most product-specific decision in the plan.

3. **I had a dimmed demo score wall running behind the empty first-run state**
   as "the visible promise". Cut. It is decoration, it competes with the one
   input that state exists for, and running fake numbers at somebody who has not
   given us a channel yet is a small dishonesty. The promise is delivered 1.5
   seconds later, for real, in state 4B.

**Per the brief's rule, the decorative element I am cutting in this phase** is
the four `.hero-note` feature pills in `WelcomeOverlay` ("Formula-based — not
AI", "Adapts to each streamer", …). They restate the five steps directly above
them in shorter words, inside a modal that is itself being retired.

---

## 6. Open questions — I need answers before Phase 1

**Q1 — Two palettes.** The marketing pages and the product use different hexes
for the same six roles (`#0e0b11` vs `#08080b`, `#b86adc` vs `#a855f7`,
`#f2eaf7` vs `#f6f6f9`, and three more — audit §1.1). Unifying them is the
single biggest coherence win available, and it is impossible without changing
hex values, which the brief forbids. **May I unify onto one set?** If yes, which
side wins — I would pick the marketing values, since that is what a visitor sees
first and what the brand assets are built from.

**Q2 — Nine oranges.** `--warn` currently resolves to nine near-identical hexes
(`#f7a745 #ff9a52 #ffc25c #ffd45e #ff9d00 #ff7700 #ff8a4c #ffc75a #ffcc5c`).
**May I collapse these to two** — one fill, one text tone — which means retiring
seven values?

**Q3 — The dashboard loads no fonts at all.** It asks for Inter and ships zero
`@font-face` rules, so it silently renders in `system-ui` while the marketing
site is in Sora. Fixing it means **self-hosting one woff2** alongside the four
already in `static/fonts/`. No package, no CDN, ~15KB. Do you want Inter, or
should the product simply use Sora and match the site?

**Q4 — Copy.** Nothing blocks. All 14 over-length blocks are width problems
solved by `--measure`, and the long eyebrow labels ("FIRST, THE PART MOST TOOLS
SKIP") wrap acceptably at every width. **I am not requesting any copy change.**

**Q5 — Phase 1 scope.** The 359 inline `style={{…}}` objects in
`aurora_html.py` are unreachable by a CSS token refactor. Converting them is
mechanical but touches ~1069 declarations across the product. Do you want that
inside Phase 1 (large, boring, one commit, as the brief describes) or split
into Phase 1a (CSS) and Phase 1b (inline styles) so each is reviewable?

---

**Stopping here for approval, as instructed. No code written.**
