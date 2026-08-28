# DESIGN_AUDIT.md

Phase 0. Read-only inventory of the current visual system. Every number here was
measured from the **rendered** output — the CSS extracted from the pages the
server actually serves, and computed styles read out of headless Chromium — not
from recall and not by reading the source strings.

---

## 0. Method, and what these numbers do not cover

Extraction script: `scratchpad/audit/extract.py` renders `LANDING_HTML`,
`DASHBOARD_HTML`, `tutorial_html.render()`, `compare_html.render()`,
`ADMIN_HTML`, `PAYWALL_HTML`, `TOS_HTML` and writes each page's `<style>`
contents to disk. Analysis scripts: `colors.py`, `space.py`, `type.py`,
`conflict2.py`, `shift.py`. Browser measurement: `measure.mjs`, `contrast.mjs`,
`rhythm.mjs`, `detail.mjs` against `/opt/pw-browsers/chromium`.

**Three limits, stated up front so nothing below is read as more than it is:**

1. **The CLS numbers are not usable.** Every page measured `CLS = 0.0000`, but
   that is an artifact: under `file://` the images 404 instantly, so there is
   nothing to shift. Real CLS has to be measured against the running server.
   What I report below is the *risk surface* — elements that can shift — which
   is measurable statically and is real.
2. **The dashboard could not be measured in-browser.** React comes from a CDN
   the egress policy blocks, so the page renders zero interactive elements
   (`focusless=0/0`). Every dashboard figure below is static analysis of its CSS
   and JSX. Its runtime numbers need the app running.
3. Admin (`ADMIN_HTML`) is counted in the colour inventory but excluded from the
   spacing/type inventories and from all remediation — it is an internal tool,
   not part of this brief.

---

## 1. Colour

**131 distinct colours. 645 colour literals written out longhand. Zero tokens
for any of them** — `--ink`, `--acc` etc. exist but are themselves defined from
raw hex, and most rules bypass them.

| | count |
|---|---|
| distinct hex values | **89** |
| distinct `rgb()`/`rgba()` base triplets | **42** |
| total colour literals in CSS | **645** |

### 1.1 The structural finding: there are two palettes, not one

The marketing surface and the product surface use **different, near-duplicate
hexes for the same role**. Nothing shares a token.

| role | marketing (landing / tutorial / compare) | product (dashboard / paywall / legal) |
|---|---|---|
| page background | `#0e0b11` | `#08080b` |
| primary text | `#f2eaf7` | `#f6f6f9` |
| secondary text | `#b9aec4` | `#9c9caa` |
| tertiary text | `#9c90a6` | `#5d5d6b` |
| brand purple | `#b86adc` / `#d26afb` | `#a855f7` / `#c79bff` |

A user crossing from the landing page into the product moves between two
slightly different greys and two slightly different purples. It is not visible
side by side; it is visible in sequence, and it is a large part of why the
product does not feel like the same object as the site that sold it.

**This cannot be fixed without changing hex values, which the brief forbids.**
It is listed as open question **Q1**.

### 1.2 Proposed token set (grouping only — no value changes)

Every existing hex maps into one of these roles. Nothing is invented.

```
--bg-base      #0e0b11 / #08080b     page ground
--bg-raised    #1b1221 #171219 #15151c   cards, panels
--bg-sunken    #150f1b #101016 #0d0d12   wells, insets
--bg-band      #f8f5f0 #efe9e1       the two light "sand" bands
--hair         rgba(255,255,255,.06-.10)  1px separators (24 opacity variants today)

--ink          #f2eaf7 / #f6f6f9     primary text
--ink-2        #b9aec4 / #9c9caa     secondary
--ink-3        #9c90a6 / #5d5d6b     tertiary, eyebrows, captions

--brand        #b86adc / #a855f7     primary purple
--brand-lift   #d26afb / #c79bff     hover, emphasis
--brand-glow   #f943ff              the one true accent, score wall only
--twitch       #9146ff              platform, never decorative

--live         #53fc18 #39b515      "watching", live pulse
--good         #2ee08a #3ee08a #7ddba4  approved, positive delta
--warn         #f7a745 #ff9a52 #ffc25c #ffd45e #ff9d00 #ff7700 #ff8a4c #ffc75a #ffcc5c
--bad          #ff5a78 #e91916
```

**`--warn` is nine hexes doing one job.** Those nine are near-identical oranges
and ambers scattered across the dashboard. Collapsing them to two (a fill and a
text tone) is the single biggest reduction available, and it *does* mean
retiring seven hex values — open question **Q2**.

---

## 2. Spacing

| | |
|---|---|
| distinct px spacing values | **52** |
| total px spacing declarations | **932** |
| on a 4/8/12/16/24/32/48/64/96/128 scale | 261 (**28%**) |
| **off any scale** | 671 (**71%**) |

The off-scale values are not outliers, they are the norm:

```
 7px x40    9px x41   10px x71   11px x35   13px x24   14px x57
15px x12   18px x39   22px x31   26px x35   34px x26   38px x11
```

`10px` alone is used 71 times, `9px` 41 times, `7px` 40 times. There are also
`-5.5px` and `-1.5px` margins, and `12.8px`.

The brief predicted this exactly. It is the single largest contributor to
"clunky": nothing lines up with anything because no two values were chosen in
relation to each other.

### 2.1 No vertical rhythm — measured

Six top-level sections on the landing page, **six different top/bottom padding
pairs**, and in five of six the top and bottom **do not match**:

| section | padding-top | padding-bottom |
|---|---|---|
| `.wrap.full.band-sand.seam` | 52px | 46px |
| `.wrap.band-sand.seam` | 74px | 78px |
| `.wrap.wide` | 44px | 52px |
| `.band-sand.seam` | 70px | 72px |
| `.wrap` | 40px | 56px |
| `.band-dark.final-band.seam` | 46px | 46px |

Scrolling is therefore metered at 40, 44, 46, 52, 70, 74, 78 — which reads as
lurching because it is lurching.

---

## 3. Type

| | |
|---|---|
| distinct `font-size` values | **50** |
| distinct `line-height` values | **24** |
| distinct `font-weight` values | **7** |

**Nine of the fifty sizes are fractional pixels:** `9.5 10.5 11.5 12.5 13.5
14.5 15.5 16.5 12.8`. In the browser this produces **285 elements on the landing
page alone** with a fractional computed font-size. Fractional type is the
mechanism behind the "everything is slightly off" feel — it never lands on a
device pixel and never aligns to a baseline.

Weights in use: `600 (x100), 700 (x49), 800 (x31), 400 (x20), 500 (x9), 650
(x4)`. **`650` is not a real step in any scale.**

Line-heights include `1.62, 1.65, 1.66, 1.68, 1.72` — five values within
0.1 of each other, all hand-picked.

### 3.1 Line length

**14 blocks of prose exceed the 65–75 character cap.** Worst offenders:

| element | measured | file |
|---|---|---|
| `.price-tiny` | **~177ch** (1150px @ 13px) | `api.py` pricing section |
| `.faq-more` | **~139ch** (1150px @ 16.5px) | `api.py:7020` region |
| feature body copy | **~104ch** (835px @ 16px) | "What you get" |
| `.faq-a` × 7 | ~77ch each | `api.py:6804` |

### 3.2 The product is in a font it never loads

`DASHBOARD_HTML` declares `--font:'Inter',system-ui,-apple-system,sans-serif`
and contains **zero `@font-face` rules**. Inter is never fetched. The dashboard
silently renders in `system-ui` — a different typeface from the marketing pages,
which correctly load Sora, Plex Mono and Lobster with `font-display:swap`.

Confirmed in browser: landing `body` resolves to `Sora, system-ui, sans-serif`;
dashboard resolves to `Inter, system-ui, …` with Inter absent.

---

## 4. CSS specificity conflicts — **the brief's hypothesis is mostly wrong here**

The brief predicted collapsed spacing caused by selectors cancelling each other.
I tested for it directly: same selector, same property, **same media context**,
different value. A responsive override is intended and does not count.

**Result: 3 true conflicts across the four styled surfaces.**

| file / selector | property | values |
|---|---|---|
| `api.py:6804` and `api.py:7030` — `.faq-a` | `padding` | `0 34px 18px 0` → `0 2px 20px` |
| `aurora_html.py` `@media(max-width:700px)` `.rd-header` | `padding` | `0 12px` → `10px 12px` |
| `aurora_html.py` `@media(max-width:700px)` `.rd-header` | `gap` | `10px` → `8px 10px` |

The `.faq-a` one is the interesting one: **there are two complete FAQ
implementations in the same stylesheet** — `.faq-item` at `api.py:6785`
(border-top, two-column) and again at `api.py:7021` (border-bottom,
`.faq-list`), with a matching duplicate `.faq-a`. One of them is dead.

`!important` count is low and not the problem: landing 4, dashboard 2,
tutorial 2, compare 2.

**So: the spacing is not fighting itself. It was simply never chosen from a
system.** That changes the remedy — Phase 1 is a refactor onto a scale, not an
untangling of specificity. I would rather say so than manufacture findings to
match the brief.

### 4.1 The real obstacle to a token refactor: 359 inline style objects

`aurora_html.py` styles a large share of the product **outside any stylesheet**:

| | count |
|---|---|
| `style={{…}}` objects | **359** |
| property declarations inside them | **1069** |
| — spacing (`padding`, `margin*`, `gap`) | **218** |
| — type (`fontSize`, `fontWeight`, `lineHeight`, `letterSpacing`) | **154** |
| — colour (`color`, `background*`, `border*`) | **218** |
| bare numbers React renders as px | **478** |

A CSS-variable refactor cannot reach any of these. This is a Phase 1 workstream
in its own right and it is the reason Phase 1 will be large.

---

## 5. Layout-shift risk surface

| page | `<img>` missing width+height | `<iframe>` missing width+height | `aspect-ratio` rules |
|---|---|---|---|
| landing | 3 / 3 | 1 / 1 | 3 |
| **dashboard** | **12 / 13** | **3 / 3** | 8 |
| tutorial | 2 / 13 | — | 2 |
| compare | 1 / 1 | — | 2 |
| paywall | 1 / 1 | — | 0 |

The dashboard is the exposure: 12 of 13 images and all three Twitch players
have no reserved box. Clip thumbnails arrive asynchronously into a grid.

Also on the shift surface:
- **`@font-face`: 0 on dashboard and paywall.** Marketing pages: 4 each, all
  with `font-display:swap`.
- **Dashboard transitions animate `all`, `left`, and `width`** — three
  non-composited properties. Marketing pages animate nothing layout-affecting.

### 5.1 Motion inventory

| page | transitions | animations | `@keyframes` | `prefers-reduced-motion` blocks |
|---|---|---|---|---|
| landing | 30 | 2 | 1 | 4 |
| dashboard | 36 | 11 | 8 | 3 |
| tutorial | 6 | 0 | 0 | 1 |
| compare | 6 | 0 | 0 | 1 |
| **paywall** | 1 | 0 | 0 | **0** |

No shared easing token and no shared duration token exists anywhere.

### 5.2 Focus rings — real, and worse on small screens

Interactive elements with neither a visible outline nor a box-shadow when
focused:

| page | @375 | @768 | @1440 |
|---|---|---|---|
| landing | **10 / 28** | 10 / 28 | 4 / 28 |
| tutorial | **27 / 49** | 27 / 49 | 11 / 49 |
| compare | 5 / 18 | 5 / 18 | 0 / 18 |

### 5.3 Contrast — passes, once measured correctly

My first pass reported five failures. That was a **measurement bug**: it read
the first non-transparent `background-color` up the tree, which for a
translucent overlay is the overlay itself, not the composited result. Corrected
to composite every translucent layer down to an opaque base:

| page | text nodes sampled | failures below 4.5:1 (3:1 large) |
|---|---|---|
| landing | 98 | **0** |
| tutorial | 123 | **0** |
| compare | 89 | **0** |

**There are no contrast failures to solve on the marketing pages.** The brief
anticipated some; there are none. Dashboard still unmeasured (limit 2).

---

## 6. The five worst offenders

Named as concrete elements, with evidence.

### 1. The score wall gets 9.8% of the page — `api.py:6472`
Measured at 1440px: the wall's box is **1325 × 490px, sitting at y=465 in a
4999px page**. Four cards of 324px each, `gap:10px`, capped by a `.wall-cap`
strip of 9.5–11px letterspaced micro-type. The product's entire thesis is
compressed into a 490px strip and then never referenced again. It is presented
as a hero decoration, not as the subject.

### 2. "What you get" is nine equal-weight text blocks — the flattest section
Three rows of three columns, every item the same size, same weight, same
treatment, separated only by 11px orange eyebrow labels. Body copy runs to
**~104ch**. Nothing in it tells the eye what matters most. It is the longest
section on the page and the least navigable.

### 3. Pricing CTAs do not share a baseline — `api.py:6828-6830`
`.ptier-a{padding:22px 22px 24px}`, `.ptier-b{padding:26px 26px 28px}`,
`.ptier-c{padding:32px 32px 34px}` with border-radii of `3px / 4px / 7px`. The
escalating scale is deliberate, but the consequence is measured button tops of
**y = 3509 / 3505 / 3499** — a 10px stagger across three buttons the eye reads
as a row.

### 4. "How it works" columns are ragged — the middle one carries a figure
Steps one and three are text-only; step two contains the signal-bar figure. The
three `STEP ONE/TWO/THREE` eyebrows align, but the columns end at wildly
different heights, so the section has no bottom edge.

### 5. The post-login first screen is a 250-word modal — `aurora_html.py:5298`
`WelcomeOverlay` is a fixed-inset blurred overlay containing a logo, a heading,
a lead paragraph, **five numbered steps**, **four feature pills**, and a CTA —
before the user has done anything. It is built from **~40 inline style values**
and no class. It is the opposite of "one input, one button": it is a document
that must be dismissed before the product can be reached.

---

## 7. What the brief predicted that I could not confirm

Stated plainly so the plan is not built on assumptions:

- **Specificity wars causing collapsed spacing** — 3 real instances, not a
  systemic problem. The remedy is a scale, not an untangling.
- **Contrast failures** — none on the marketing pages, measured with proper
  alpha compositing.
- **Numbers jittering without `tabular-nums`** — already handled: 18
  declarations on the dashboard, 10 on landing, including `.thread-score`.
- **Missing `font-display:swap`** — present on all four marketing faces. The
  real font problem is different and worse: the dashboard loads no faces at all.
- **CLS** — cannot be measured from this harness; only the risk surface can.
