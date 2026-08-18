# IG carousel — "You're not bad at clipping, you're bottlenecked"

7-slide Instagram carousel ad, 1080×1350 (4:5), rendered from HTML.

## Re-render

```bash
npm i playwright          # once
node render.js            # writes slide1.png … slide7.png at 2x (2160×2700)
```

Chromium path is pinned to `/opt/pw-browsers/chromium` for this container; change
`executablePath` if running elsewhere. `render.js` also prints a **safe-margin
audit** — any element closer than 120px to an edge gets flagged. Keep it clean.

## Edit copy

All copy lives in `carousel.html`, one `<div class="slide">` per slide. Design
tokens are at the top of the `<style>` block (dark `#08080B`, accent `#A855F7`,
Inter via Google Fonts, 120px padding = the safe margin).

## Screenshots to drop in

Three slides have dashed placeholders where real product screenshots go:

| Slide | Asset | Crop notes |
|---|---|---|
| 4 | Clip Review queue-full banner | Banner strip only. **Do not** include the stats row — the banner says "full at 200" while the tile says "97 pending", which reads as a contradiction. |
| 6 | VOD / screen recording of the live trigger-score chart | Chart panel only. **Crop out any "Total clips 0" / "Approval rate —"** — reads as "produces nothing" in an ad. Shoot it against a **face-free channel** (institutional//your own test channel) so no streamer's likeness ends up in a paid ad. |
| 7 | Clip Review stats row | The four tiles alone, no banner above. |

## ⚠️ VERIFY PRICING/OFFER BEFORE SPENDING MONEY ON THIS

This ad was first drafted against the $15-single-price / 7-day-self-serve-trial
era and **that model is gone**. Corrected against `src/billing/plans.py`:

| | current |
|---|---|
| Free | $0 — 1 stream, 15 pending, no VOD scanner, no Clip Editor |
| Starter | $10/mo — 3 streams, 50 pending |
| Pro | $25/mo — 10 streams, 200 pending, VOD scanner, Clip Editor |
| Trial | self-serve 7-day trial **removed**; trials are admin-granted only |

Slide 7 now reflects this. **Re-check before every campaign** — pricing has
changed once already and a wrong price in a paid ad is a chargeback magnet.

**Open question the copy does NOT assume:** slides 3/6 are written as though
Highlightz only replaces the *finding* step ("you still edit, you just stop
hunting"). There is now a Clip Editor (trim/reframe/captions) and a Scheduler,
but `uploads_enabled` and `captions_enabled` default to **false** — release
flags. If those are live on prod, slide 6 badly undersells the product and the
whole "the unpaid part" workflow contrast should be rewritten to include
editing. Confirm what users can actually reach before rewriting.

## Rules baked into this ad (don't undo them)

- **No income claims.** Slide 2 carries zero dollar figures and says rates vary
  by program. Meta rejects guaranteed-earnings creative.
- **Slide 3's minute breakdown is the reader's cost, illustrative** — labeled as
  such. It is not a product claim.
- **Slide 4's caption frames 263 as one specific account/day**, not a typical
  result.
- **No faces.** Only face-free screenshots are used (banner, NASA chart, stats
  row). Real streamers' faces in a *paid ad* implies endorsement — right-of-
  publicity risk, and a fast way to sour a streamer partnership.
- **No fabricated proof.** No testimonials, user counts, or star ratings
  anywhere; the site has none, so the ad has none.
- Only real capabilities are named: live clipping, real Twitch clips under the
  user's account, formula-not-AI with a visible score breakdown, review queue,
  multiple channels. **It does not reframe, caption, export, or upload** — the
  ad deliberately attacks only the *finding* step.

## Caption + alt hooks

See the session handoff; caption leads with "Two streams. One day. 263
clippable moments." to mirror slide 1's "you post three" from the other side.
