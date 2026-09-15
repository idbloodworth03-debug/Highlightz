"""Aurora dashboard HTML — single-file React SPA served at GET /."""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Highlightz</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>
/* Self-hosted, like the four faces the marketing pages already load. This
   was an @import of Google Fonts from inside <style> — the slowest possible
   way to load a face, because the stylesheet has to parse before the request
   even starts, and a third-party request on every dashboard load. Same
   typeface, same weight range: nothing about the rendering changes. */
@font-face{font-family:'Inter';font-style:normal;font-weight:400 800;
  font-display:swap;src:url(/static/fonts/inter-var.woff2) format('woff2')}
:root {
  --rd-bg: #0e0b11; --rd-bg-2: #17131c;
  --panel: rgba(255,255,255,.035); --panel-2: rgba(255,255,255,.055); --panel-hi: rgba(255,255,255,.08);
  --hair: rgba(255,255,255,.08); --hair-2: rgba(255,255,255,.14);
  --fg: #f2eaf7; --fg-2: #b9aec4; --fg-3: #9c90a6;
  --acc: #c489e4; --acc-2: #b86adc;
  --grad: linear-gradient(135deg,#f943ff 0%,#b86adc 52%,#7c6bff 100%);
  --grad-soft: linear-gradient(135deg,rgba(249,67,255,.18),rgba(124,107,255,.18));
  --glow: 0 0 0 1px rgba(196,137,228,.35),0 8px 30px -6px rgba(184,106,220,.45);
  --live: #2ee08a; --live-soft: rgba(46,224,138,.14);
  --pending: #ffc25c; --pending-soft: rgba(255,194,92,.14);
  /* The highlight identity — the crowd-suggested cards. Purple by the
     owner's call (it was gold), and still its OWN name rather than a reuse
     of --acc: the constraint that survives the recolour is that this badge
     must be visibly unlike the viral badge (orange-to-pink) and the crowd
     -clipped badge (green-to-teal) sitting next to it. */
  --sug: #c489e4; --sug-deep: #b86adc;
  --danger: #ff5a78; --danger-soft: rgba(255,90,120,.14);
  --r-sm:10px; --r-md:14px; --r-lg:18px; --r-xl:24px; --r-pill:999px;
  --font:'Inter',system-ui,-apple-system,sans-serif;
  --shadow-1:0 1px 2px rgba(0,0,0,.4); --shadow-2:0 10px 30px -10px rgba(0,0,0,.6);
  --shadow-card:0 18px 40px -16px rgba(0,0,0,.65);
  /* SPACING SCALE. Every margin, padding and gap in this file now resolves to
     one of these. 779 values across the four styled surfaces were off any
     scale before this; nothing gets an arbitrary number again. */
  --s-1:4px; --s-2:8px; --s-3:12px; --s-4:16px; --s-5:24px;
  --s-6:32px; --s-7:48px; --s-8:64px; --s-9:96px; --s-10:128px;
  /* TYPE SCALE. Seven steps, no fractional pixels — a fractional size never
     lands on a device pixel, which is most of why the product read as
     slightly-off everywhere. */
  --t-caption:12px; --t-small:14px; --t-body:16px; --t-h3:17px;
  --t-h2:24px; --t-h1:30px; --t-display:44px;
  --measure:68ch;
  /* MOTION. One curve, matching the marketing site's, and three durations.
     --dur-event is reserved for the score wall trigger and nothing else. */
  --ease:cubic-bezier(.16,1,.3,1);
  --dur-fast:150ms; --dur-slow:400ms; --dur-event:900ms;
}
/* ══ FIRST RUN ════════════════════════════════════════════════════════════
   THE PROBLEM THIS REPLACES. A brand-new account's first sight of the product
   was a fixed-inset blurred overlay carrying a logo, a heading, a lead
   paragraph, five numbered steps and a CTA — about 250 words — with the
   dashboard greyed out behind it. Everything it said is already on the landing
   page and in the walkthrough, both of which the user has just had the chance
   to read. It was a document standing between somebody and the thing they had
   just signed up for.

   Behind it was the second half of the problem: a full dashboard chrome — nav
   rail, header, platform switch, live pill, user chip — wrapped around an
   empty panel that said there was nothing to report. An empty screen is an
   invitation to act, not a report that there is nothing to report.

   So this state does exactly one thing: get a channel name entered. One input,
   one button, one line saying what happens next. No nav, no tabs, no zeroed
   stats. Everything else in the app appears the moment there is something for
   it to hold. ══ */
.fr{min-height:100vh;display:grid;place-items:center;padding:var(--s-5);
  background:var(--rd-bg)}
.fr-in{width:100%;max-width:520px;text-align:center}
.fr-mark{height:32px;margin-bottom:var(--s-6);opacity:.9}
.fr h1{font-size:var(--t-h1);font-weight:800;letter-spacing:-.025em;
  line-height:1.15;margin-bottom:var(--s-3)}
.fr-sub{font-size:var(--t-small);color:var(--fg-2);line-height:1.6;
  margin-bottom:var(--s-6)}
.fr-row{display:flex;gap:var(--s-2);align-items:stretch}
.fr-row .rd-input{flex:1;font-size:var(--t-body);padding:var(--s-3) var(--s-4)}
.fr-note{margin-top:var(--s-4);font-size:var(--t-caption);color:var(--fg-3);
  letter-spacing:.02em}
.fr-err{margin-top:var(--s-3);font-size:var(--t-small);color:var(--danger)}
@media(max-width:520px){
  .fr-row{flex-direction:column}
}

/* ══ THE WAKE ═════════════════════════════════════════════════════════════
   What happens between "a channel was added" and "the dashboard is running".
   It used to be nothing: the panel simply swapped. Three beats over 1.5s, all
   transform and opacity, so the system reads as coming up rather than as a
   screen being replaced.

   beat 1  0-400ms   the channel name settles in as a chip
   beat 2  400-900ms the frame draws: hairlines, then the threshold sweeps
   beat 3  900-1500  the score counts up from zero and the trace begins  ══ */
.wake{position:fixed;inset:0;z-index:40;display:grid;place-items:center;
  background:var(--rd-bg);padding:var(--s-5)}
.wake-in{width:100%;max-width:560px}
.wake-chip{display:inline-flex;align-items:center;gap:var(--s-2);
  padding:var(--s-1) var(--s-3);border-radius:var(--r-pill);
  border:1px solid var(--hair-2);font-size:var(--t-caption);font-weight:600;
  letter-spacing:.06em;text-transform:uppercase;color:var(--acc);
  opacity:0;transform:translate3d(0,8px,0);
  transition:opacity var(--dur-slow) var(--ease),transform var(--dur-slow) var(--ease)}
.wake.b1 .wake-chip{opacity:1;transform:none}
.wake-frame{margin-top:var(--s-5);border:1px solid var(--hair);border-radius:var(--r-md);
  padding:var(--s-5);opacity:0;transform:translate3d(0,12px,0);
  transition:opacity var(--dur-slow) var(--ease),transform var(--dur-slow) var(--ease)}
.wake.b2 .wake-frame{opacity:1;transform:none}
.wake-score{font-size:var(--t-display);font-weight:800;letter-spacing:-.03em;
  font-variant-numeric:tabular-nums;line-height:1;min-width:3ch;display:inline-block}
.wake-rule{height:1px;background:var(--acc);margin:var(--s-4) 0;
  transform:scaleX(0);transform-origin:left center;
  transition:transform var(--dur-slow) var(--ease)}
.wake.b2 .wake-rule{transform:scaleX(1)}
.wake-lab{font-size:var(--t-caption);letter-spacing:.14em;text-transform:uppercase;
  color:var(--fg-3)}
.wake-status{margin-top:var(--s-4);font-size:var(--t-small);color:var(--fg-2);
  opacity:0;transition:opacity var(--dur-slow) var(--ease)}
.wake.b3 .wake-status{opacity:1}
.wake{cursor:pointer}
.wake-skip{margin-top:var(--s-4);font-size:var(--t-caption);color:var(--fg-3);letter-spacing:.06em;
  text-transform:uppercase;opacity:0;transition:opacity var(--dur-slow) var(--ease)}
.wake.b1 .wake-skip{opacity:1}

/* ══ THE RETURNING HEADER ═════════════════════════════════════════════════
   One fact, at the size of the fact. Coming back to the product, the thing
   that matters is how many clips are waiting and whether the week's keep limit
   is close — not a row of panels each holding a number. ══ */
.today{display:flex;align-items:flex-end;gap:var(--s-5);flex-wrap:wrap;
  padding:var(--s-5) 0 var(--s-6)}
.today-n{font-size:var(--t-display);font-weight:800;letter-spacing:-.03em;
  line-height:.9;font-variant-numeric:tabular-nums;color:var(--fg)}
.today-k{font-size:var(--t-body);color:var(--fg-2);margin-bottom:4px}
.today-act{margin-left:auto}
.today-meter{width:100%;margin-top:var(--s-2);font-size:var(--t-caption);
  color:var(--fg-3);letter-spacing:.02em}
.today-bar{height:4px;border-radius:var(--r-pill);background:var(--hair);
  overflow:hidden;margin-top:var(--s-2);max-width:280px}
.today-bar i{display:block;height:100%;background:var(--pending);
  transform-origin:left center;transform:scaleX(var(--v,0));
  transition:transform var(--dur-slow) var(--ease)}
@media(prefers-reduced-motion:reduce){
  .wake-chip,.wake-frame,.wake-rule,.wake-status,.wake-skip,.today-bar i{transition:none}
  .wake-chip,.wake-frame,.wake-status,.wake-skip{opacity:1;transform:none}
  .wake-rule{transform:scaleX(1)}
}
/* ══ KEYBOARD FOCUS ═══════════════════════════════════════════════════════
   THE PRODUCT HAD NONE. Not a thin one, not an inherited one — zero
   :focus-visible rules in the whole dashboard, against five on the marketing
   pages. Measured in Chromium on the Clip Review screen: 49 visible
   interactive elements with neither an outline nor a ring when focused. The
   nav rail, every action button, every link button. Tabbing through the app
   moved an invisible cursor.

   :focus-visible rather than :focus, so a mouse click does not leave a ring
   behind — that is the reason the default outline gets removed in the first
   place, and removing it without putting this back is how the product ended
   up here.

   One rule, from the tokens, on the base element types plus the classes the
   app actually uses. outline-offset keeps the ring clear of the border on
   controls that already have one. ══ */
:where(a,button,input,select,textarea,summary,[tabindex]):focus-visible,
.rd-btn:focus-visible,.rd-navitem:focus-visible,.rd-menu-btn:focus-visible,
.rd-dir:focus-visible,.rd-input:focus-visible,.rd-filter:focus-visible,
.rd-user-chip:focus-visible,.plat-sw-btn:focus-visible{
  outline:2px solid var(--acc);outline-offset:2px;border-radius:var(--r-sm)}
/* Inside the nav rail the items are flush to the edge, so the ring needs to
   sit inside the box rather than outside it or the left half is clipped. */
.rd-nav .rd-navitem:focus-visible{outline-offset:-2px}
/* Windows high-contrast replaces colours wholesale; a transparent outline is
   the documented way to keep a visible ring there. */
@media (forced-colors: active){
  :where(a,button,input,select,textarea,summary,[tabindex]):focus-visible{
    outline:2px solid CanvasText}
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{font-family:var(--font);color:var(--fg);background:var(--rd-bg);-webkit-font-smoothing:antialiased;overflow:hidden}
button{font-family:inherit;cursor:pointer}
::selection{background:rgba(196,137,228,.3)}
/* grid-template-rows is NOT optional here. Without it the single row is
   implicit and `auto`, which sizes to the frame's max-content — so a tall
   screen made the frame taller than the 100vh app and the bottom fell off the
   window (measured: a 1000px frame in a 975px viewport, and 790 in 700).
   minmax(0,1fr) pins the row to the app's own height and lets the flex column
   inside do the scrolling, which is what it is there for. */
.rd-app{position:relative;height:100vh;display:grid;grid-template-columns:104px 1fr;
  grid-template-rows:minmax(0,1fr);isolation:isolate}
/* A COLUMN, NOT A ROW TEMPLATE, and that is the whole fix.
   This was `grid-template-rows:68px 1fr` with THREE children: the header, the
   trial banner, and the screen. A two-row template gives row 2 — the banner —
   the 1fr, and drops the screen into an implicit auto row. What that did
   depended entirely on how tall the screen's content happened to be:

     Clip Review   rows resolved 68 /  44 / 863   looked fine, by luck
     Settings      rows resolved 68 / 381 / 526   the 44px banner became 381px
     Feedback      rows resolved 68 / 436 / 470   and 436px

   — hundreds of pixels of empty purple under the header on any screen whose
   content did not fill the window, and the screen itself squashed to match.
   Worse, below ~790px of viewport the auto row pushed the frame PAST 100vh
   (measured: a 790px frame in a 700px window) and `overflow:hidden` clipped
   the difference, so the bottom of every page became unreachable — no scroll,
   no scrollbar, just gone.

   A flex column cannot get this wrong: children keep their natural height and
   the screen takes what is left, whether the banner is there or not. */
.rd-frame{display:flex;flex-direction:column;min-height:0;overflow:hidden}
.rd-frame > *{flex:0 0 auto}
.rd-frame > .rd-screen{flex:1 1 auto}
/* The header's 68px used to come from the row template, so it needs its own
   height now — otherwise it collapses to its content. */
.rd-header{height:68px}
.rd-screen{min-height:0;overflow:hidden;display:flex;flex-direction:column}
.rd-app::before{content:'';position:fixed;inset:0;z-index:-2;
  background:radial-gradient(900px 480px at 18% -8%,rgba(184,106,220,.20),transparent 60%),
    radial-gradient(760px 420px at 92% 6%,rgba(249,67,255,.13),transparent 55%),
    radial-gradient(700px 600px at 60% 110%,rgba(124,107,255,.12),transparent 60%),var(--rd-bg)}
.rd-app::after{content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:radial-gradient(120% 120% at 50% 0%,transparent 60%,rgba(0,0,0,.55))}
.glass{background:var(--panel);border:1px solid var(--hair);-webkit-backdrop-filter:blur(22px) saturate(140%);backdrop-filter:blur(22px) saturate(140%)}
.rd-header{display:flex;align-items:center;gap:16px;padding:0 24px;border-bottom:1px solid var(--hair);
  background:rgba(10,10,14,.55);-webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px);z-index:5}
.rd-live{display:inline-flex;align-items:center;gap:8px;font-size:12px;font-weight:600;color:var(--live);
  background:var(--live-soft);padding:4px 12px;border-radius:var(--r-pill);border:1px solid rgba(46,224,138,.25)}
.rd-live .dot{width:7px;height:7px;border-radius:50%;background:var(--live);animation:ping 2s infinite}
@keyframes ping{0%{box-shadow:0 0 0 0 rgba(46,224,138,.5)}70%{box-shadow:0 0 0 7px rgba(46,224,138,0)}100%{box-shadow:0 0 0 0 rgba(46,224,138,0)}}
@keyframes spin{to{transform:rotate(360deg)}}
.rd-search{flex:1;max-width:420px;position:relative}
.rd-search input{width:100%;background:rgba(255,255,255,.04);border:1px solid var(--hair);border-radius:var(--r-pill);
  color:var(--fg);font-size:12px;padding:8px 12px 8px 32px;outline:none;transition:var(--dur-fast)}
.rd-search input::placeholder{color:var(--fg-3)}
.rd-search input:focus{border-color:rgba(196,137,228,.5);background:rgba(255,255,255,.06);box-shadow:0 0 0 4px rgba(184,106,220,.12)}
.rd-search .si{position:absolute;left:13px;top:50%;transform:translateY(-50%);color:var(--fg-3)}
.rd-header .spacer{flex:1}
.rd-iconbtn{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;
  background:rgba(255,255,255,.04);border:1px solid var(--hair);color:var(--fg-2);transition:var(--dur-fast)}
.rd-iconbtn:hover{color:var(--fg);background:rgba(255,255,255,.08)}
.rd-avatar{width:38px;height:38px;border-radius:50%;background:var(--grad);display:grid;place-items:center;
  font-weight:700;font-size:14px;color:#14021c;border:none;box-shadow:var(--glow)}
.rd-user-chip{display:flex;align-items:center;gap:8px;padding:4px 12px 4px 4px;border-radius:999px;
  background:rgba(255,255,255,.05);border:1px solid var(--hair)}
.rd-user-chip img{width:32px;height:32px;border-radius:50%;object-fit:cover}
.rd-user-chip .uc-init{width:32px;height:32px;border-radius:50%;background:var(--grad);display:grid;
  place-items:center;font-weight:700;font-size:12px;color:#14021c}
.rd-user-chip .uc-name{font-size:12px;font-weight:600;color:var(--fg-2)}
.rd-body{display:grid;grid-template-columns:322px 1fr;gap:16px;padding:16px 24px;overflow:hidden;min-height:0}
.rd-col{min-height:0;display:flex;flex-direction:column;gap:16px}
/* Clip Review has no side rail any more — adding streams moved to Live
   Streams — so the grid takes the full width instead of leaving a gap. */
.rd-body-full{grid-template-columns:1fr}
.rd-streampick{cursor:pointer;border-radius:15px;transition:var(--dur-fast)}
.rd-streampick.on{box-shadow:0 0 0 1px var(--acc-2)}
.rd-rail{border-radius:var(--r-lg);padding:16px;display:flex;flex-direction:column;gap:16px;overflow:hidden}
.rd-rail-head{display:flex;align-items:center;justify-content:space-between}
.rd-eyebrow{font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--fg-3)}
.rd-count{font-size:12px;font-weight:600;color:var(--fg-2);background:rgba(255,255,255,.05);padding:4px 8px;border-radius:var(--r-pill)}
.rd-addrow{display:flex;gap:8px}
.rd-suggwrap{position:relative;flex:1;min-width:0;display:flex}
.rd-suggwrap .rd-input{width:100%}
.rd-sugg{position:absolute;top:calc(100% + 6px);left:0;z-index:60;background:#101016;
  border:1px solid var(--hair-2);border-radius:12px;box-shadow:0 14px 36px rgba(0,0,0,.55);
  max-height:320px;overflow-y:auto;overflow-x:hidden;padding:4px;
  /* Wider than the input on purpose: names + LIVE + viewers/game must fit on
     one line with no horizontal scrolling. Caps to the viewport on phones. */
  width:340px;max-width:calc(100vw - 44px)}
.rd-sugglabel{font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--fg-3);padding:8px 8px 4px}
.rd-suggitem{display:flex;align-items:center;gap:8px;padding:8px 8px;border-radius:8px;cursor:pointer;font-size:12px;color:var(--fg)}
.rd-suggitem:hover{background:rgba(255,255,255,.06)}
.rd-suggitem .meta2{color:var(--fg-3);font-size:12px;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rd-sugglive{font-size:12px;font-weight:800;letter-spacing:.05em;color:#fff;background:#e91916;border-radius:4px;padding:4px 4px;flex-shrink:0}
.rd-suggempty{padding:12px 8px;font-size:12px;color:var(--fg-3)}
/* The label row carries the "Clear all" action, so it stops being padding-only
   and becomes a flex row. Same padding as before so nothing shifts. */
.rd-sugglabelrow{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 8px 4px}
.rd-sugglabelrow .rd-sugglabel{padding:0}
.rd-suggclear{background:none;border:0;cursor:pointer;font:inherit;font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--fg-3);padding:4px 4px;border-radius:5px}
.rd-suggclear:hover{color:var(--fg);background:rgba(255,255,255,.07)}
/* Per-row dismiss. Hidden until the row is hovered so eight of these do not
   read as a column of buttons, but kept focusable for keyboard users. */
.rd-suggx{margin-left:auto;flex-shrink:0;background:none;border:0;cursor:pointer;color:var(--fg-3);opacity:0;padding:4px;border-radius:5px;display:flex;align-items:center}
.rd-suggitem:hover .rd-suggx{opacity:1}
.rd-suggx:focus{opacity:1}
.rd-suggx:hover{color:var(--fg);background:rgba(255,255,255,.1)}
.rd-input{flex:1;min-width:0;background:rgba(255,255,255,.04);border:1px solid var(--hair);border-radius:var(--r-md);
  color:var(--fg);font-size:12px;padding:12px 12px;outline:none;transition:var(--dur-fast)}
.rd-input::placeholder{color:var(--fg-3)}
.rd-input:focus{border-color:rgba(196,137,228,.5);box-shadow:0 0 0 4px rgba(184,106,220,.1)}
.rd-select{background:rgba(255,255,255,.04);border:1px solid var(--hair);border-radius:var(--r-md);
  color:var(--fg);font-size:12px;padding:0 8px;outline:none;cursor:pointer}
.rd-select option{background:#15151c}
.rd-btn{border:none;border-radius:var(--r-md);padding:12px 16px;font-size:12px;font-weight:600;
  display:inline-flex;align-items:center;justify-content:center;gap:8px;color:#fff;
  background:rgba(255,255,255,.06);border:1px solid var(--hair);transition:var(--dur-fast);white-space:nowrap}
.rd-btn:hover{background:rgba(255,255,255,.1)}
.kick-theme{--acc:#53fc18;--acc-2:#39b515;--grad:linear-gradient(135deg,#53fc18 0%,#39b515 100%);--grad-soft:linear-gradient(135deg,rgba(83,252,24,.14),rgba(57,181,21,.10));--glow:0 0 0 1px rgba(83,252,24,.3),0 8px 30px -6px rgba(57,181,21,.4)}
.kick-theme .rd-btn.grad{box-shadow:0 6px 18px -6px rgba(83,252,24,.5)}
.kick-theme .rd-filter.active{box-shadow:0 4px 14px -4px rgba(83,252,24,.5)}
.kick-theme .rd-navitem.active::before{background:rgba(83,252,24,.1)}
.kick-theme .rd-navitem.active .ic{color:#53fc18}
.rd-btn.grad{background:var(--grad);border:none;color:#fff;box-shadow:0 6px 18px -6px rgba(184,106,220,.6)}
.rd-btn.grad:hover{filter:brightness(1.08);box-shadow:0 8px 24px -6px rgba(184,106,220,.75)}
.rd-btn.live{background:var(--live);color:#052012;border:none}
.rd-btn.live:hover{filter:brightness(1.08)}
.rd-btn.danger{background:var(--danger-soft);color:var(--danger);border:1px solid rgba(255,90,120,.3)}
.rd-btn.danger:hover{background:rgba(255,90,120,.22)}
.rd-btn.ghost-force{background:rgba(255,138,76,.14);color:#f7a745;border:1px solid rgba(255,138,76,.3)}
.rd-btn.ghost-force:hover{background:rgba(255,138,76,.24)}
.rd-btn.sm{padding:8px 12px;font-size:12px;border-radius:10px}
.rd-streams{display:flex;flex-direction:column;gap:8px;overflow-y:auto;padding-right:4px;min-height:0}
.rd-stream{border-radius:var(--r-md);padding:12px;background:rgba(255,255,255,.025);border:1px solid var(--hair);transition:var(--dur-fast)}
.rd-stream:hover{border-color:var(--hair-2);background:rgba(255,255,255,.045)}
.rd-stream-top{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
.rd-stream-top>div:first-child{min-width:0;flex:1;overflow:hidden}
.rd-stream .nm{font-size:14px;font-weight:700;letter-spacing:-.01em;display:flex;align-items:center;gap:8px;overflow:hidden}
.rd-stream .nm .plat{width:6px;height:6px;border-radius:50%;flex-shrink:0;background:var(--acc);box-shadow:0 0 8px var(--acc)}
.rd-stream .mt{font-size:12px;color:var(--fg-2);margin-top:4px;display:flex;gap:4px;align-items:center;flex-wrap:wrap}
.rd-chip{font-size:12px;font-weight:600;padding:4px 8px;border-radius:var(--r-pill);background:rgba(255,255,255,.06);color:var(--fg-2);text-transform:capitalize}
.rd-stream-actions{display:flex;gap:4px;align-items:center;flex-shrink:0}
.rd-x{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;background:transparent;border:none;color:var(--fg-3);transition:var(--dur-fast)}
.rd-x:hover{color:var(--danger);background:var(--danger-soft)}
.rd-score{margin-top:12px}
.rd-score-top{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:8px}
.rd-score-top .lbl{font-size:12px;color:var(--fg-2);font-weight:500}
.rd-score-top .val{font-size:17px;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums;line-height:1}
.rd-track{height:8px;border-radius:var(--r-pill);background:rgba(255,255,255,.07);overflow:hidden;position:relative}
.rd-fill{height:100%;border-radius:var(--r-pill);transition:background .6s;position:relative}
.rd-fill::after{content:'';position:absolute;right:0;top:0;bottom:0;width:14px;background:rgba(255,255,255,.5);filter:blur(5px);opacity:.7}
.rd-thr{position:absolute;top:-2px;bottom:-2px;width:2px;background:rgba(255,255,255,.65);box-shadow:0 0 5px rgba(255,255,255,.45);border-radius:1px;pointer-events:none}
/* A sweep across the whole track, not a fill animation, because the percentage
   can legitimately sit still for minutes: the audio decode reports every 30s of
   decoded audio, and a bar that has not moved since the last update is
   indistinguishable from a hung job. This keeps moving regardless of progress,
   and works at 0% where a fill-based shimmer would have nothing to shimmer. */
.rd-track.working::after{content:'';position:absolute;top:0;bottom:0;width:36%;
  background:linear-gradient(90deg,transparent,rgba(196,137,228,.5),transparent);
  animation:rdScan 1.7s ease-in-out infinite;pointer-events:none}
@keyframes rdScan{0%{left:-36%}100%{left:100%}}
.rd-livedot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--acc);
  margin-right:8px;vertical-align:middle;animation:rdBreathe 1.4s ease-in-out infinite}
@keyframes rdBreathe{0%,100%{opacity:.35;transform:scale(.82)}50%{opacity:1;transform:scale(1)}}
@media(prefers-reduced-motion:reduce){
  /* Still legible without motion: the elapsed counter alone proves liveness. */
  .rd-track.working::after{animation:none;opacity:.25}
  .rd-livedot{animation:none;opacity:.9}
}
.rd-sigs{display:flex;gap:4px;margin-top:8px;flex-wrap:wrap}
.rd-sig{font-size:12px;padding:4px 8px;border-radius:6px;background:rgba(255,255,255,.05);color:var(--fg-2);font-variant-numeric:tabular-nums}
/* Training studio sliders */
.tr-dim-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px}
.tr-dim-label{font-size:14px;font-weight:700}
.tr-dim-hint{font-size:12px;font-weight:500;color:var(--fg-3);margin-left:8px}
.tr-dim-val{font-size:17px;font-weight:800;color:var(--acc);font-variant-numeric:tabular-nums;min-width:26px;text-align:right}
.tr-slider{width:100%;height:6px;-webkit-appearance:none;appearance:none;background:rgba(255,255,255,.09);border-radius:99px;outline:none;cursor:pointer}
.tr-slider::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:18px;height:18px;border-radius:50%;background:linear-gradient(135deg,#f943ff,#b86adc);box-shadow:0 0 10px rgba(184,106,220,.6);cursor:pointer}
.tr-slider::-moz-range-thumb{width:18px;height:18px;border:none;border-radius:50%;background:linear-gradient(135deg,#f943ff,#b86adc);box-shadow:0 0 10px rgba(184,106,220,.6);cursor:pointer}
.rd-profile{margin-top:12px;padding:12px;border-radius:var(--r-md);background:rgba(0,0,0,.25);border:1px solid var(--hair)}
.rd-pgrid{display:grid;grid-template-columns:1fr 1fr;gap:8px 12px}
.rd-pcell .k{font-size:12px;color:var(--fg-3);text-transform:uppercase;letter-spacing:.06em;font-weight:600}
.rd-pcell .v{font-size:14px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:4px}
.rd-learn{margin-top:8px;font-size:12px;font-weight:600;display:flex;align-items:center;gap:4px}
.rd-learnbar{flex:1;height:4px;border-radius:var(--r-pill);background:rgba(255,255,255,.08);overflow:hidden}
.rd-learnbar>div{height:100%;background:var(--grad);border-radius:var(--r-pill);transition:transform var(--dur-slow) var(--ease)}
.rd-empty{text-align:center;color:var(--fg-3);font-size:12px;padding:32px 12px;line-height:1.6}
.rd-empty .ic{color:var(--fg-3);display:flex;justify-content:center;margin-bottom:8px}
.rd-main{min-height:0;display:flex;flex-direction:column;gap:16px;overflow:hidden}
.rd-toolbar{display:flex;align-items:center;gap:12px}
.rd-toolbar h2{font-size:17px;font-weight:700;letter-spacing:-.02em}
/* The title line now carries the count itself, because the screen name is
   already in the page header two inches above and saying it twice cost a whole
   row. Sized up from 12px accordingly: it is the first thing on the line. */
/* LEAD WITH IT. This is the single fact that decides what a returning user
   does, and Clip Review is the screen they land on — but it was set at 14px
   in a row of controls, the same size as the sort labels beside it. At
   --t-h2 it is the first thing the eye lands on and the rest of the toolbar
   reads as what it is: the controls for the thing the number counts.
   tabular-nums and a fixed min-width so 9 becoming 10 cannot shift the
   controls to its right. */
.rd-toolbar-count{font-size:var(--t-h2);font-weight:800;color:var(--fg);
  letter-spacing:-.025em;line-height:1;font-variant-numeric:tabular-nums;
  min-width:5ch;margin-right:var(--s-2)}
@media(max-width:700px){ .rd-toolbar-count{font-size:var(--t-h3)} }
.rd-toolbar-meta{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--fg-3);
  font-variant-numeric:tabular-nums}
.rd-toolbar-meta svg{color:var(--live)}
/* The last few of the weekly library allowance. Amber rather than red: they
   have not done anything wrong and nothing is broken, they are just near the
   end of what the plan keeps. Colour AND the wording change, so the state does
   not rest on hue alone. */
.rd-toolbar-meta.warn{color:var(--pending)}
.rd-toolbar-meta.warn svg{color:var(--pending)}
.rd-toolbar-acts{display:flex;gap:8px;align-items:center;margin-left:auto}
/* The controls row: what you are LOOKING at, kept apart from the row above,
   which is what you can DESTROY. */
.rd-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.rd-sortwrap{display:flex;align-items:center;gap:0;margin-left:auto}
/* Sort field and direction read as one control, so they are welded: the field
   menu loses its right radius and the direction button its left, and they
   share the seam. Two separate pills invited people to read the arrow as
   unrelated to the menu beside it. */
.rd-sortwrap .rd-menu-btn{border-top-right-radius:0;border-bottom-right-radius:0;border-right-color:transparent}
.rd-dir{display:inline-flex;align-items:center;gap:4px;font:inherit;font-size:12px;font-weight:600;
  color:var(--fg-2);background:rgba(255,255,255,.04);border:1px solid var(--hair);
  border-top-left-radius:0;border-bottom-left-radius:0;
  border-top-right-radius:var(--r-md);border-bottom-right-radius:var(--r-md);
  padding:8px 12px;cursor:pointer;transition:var(--dur-fast);white-space:nowrap}
.rd-dir:hover{color:var(--fg);background:rgba(255,255,255,.07);border-color:var(--hair-2)}

/* ── RdMenu ── a dropdown that obeys this stylesheet, unlike <select>. */
.rd-menu{position:relative}
.rd-menu-btn{display:inline-flex;align-items:center;gap:8px;font:inherit;font-size:12px;font-weight:600;
  color:var(--fg-2);background:rgba(255,255,255,.04);border:1px solid var(--hair);
  border-radius:var(--r-md);padding:8px 12px;cursor:pointer;transition:var(--dur-fast);white-space:nowrap}
.rd-menu-btn:hover{color:var(--fg);background:rgba(255,255,255,.07);border-color:var(--hair-2)}
.rd-menu-btn.open{color:var(--fg);border-color:var(--acc);background:rgba(184,106,220,.10)}
.rd-menu-lbl{color:var(--fg-3);font-weight:600}
.rd-menu-val{color:var(--fg);font-weight:700;max-width:15ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rd-menu-caret{display:flex;color:var(--fg-3);transition:transform var(--dur-fast)}
.rd-menu-caret.open{transform:rotate(180deg)}
.rd-menu-pop{position:absolute;top:calc(100% + 6px);left:0;z-index:60;min-width:100%;
  max-height:290px;overflow-y:auto;padding:4px;border-radius:12px;
  background:#15151f;border:1px solid var(--hair-2);
  box-shadow:0 18px 40px -12px rgba(0,0,0,.7);display:flex;flex-direction:column;gap:4px}
.rd-menu-pop.right{left:auto;right:0}
.rd-menu-item{display:flex;align-items:center;gap:8px;width:100%;text-align:left;
  font:inherit;font-size:12px;font-weight:600;color:var(--fg-2);background:none;border:0;
  padding:8px 8px;border-radius:8px;cursor:pointer;transition:var(--dur-fast)}
.rd-menu-item:hover{background:rgba(255,255,255,.06);color:var(--fg)}
.rd-menu-item.on{color:var(--acc);background:rgba(184,106,220,.12)}
.rd-menu-item-l{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rd-menu-item-s{font-size:12px;color:var(--fg-3);font-weight:500}
.rd-menu-tick{display:flex;flex-shrink:0}
.cull-panel{position:absolute;top:calc(100% + 8px);right:0;z-index:40;width:260px;padding:16px;border-radius:12px;display:flex;flex-direction:column;gap:8px}
.cull-row{display:flex;justify-content:space-between;align-items:baseline}
.cull-lbl{font-size:12px;color:var(--fg-2);font-weight:600}
.cull-val{font-size:24px;font-weight:800;font-variant-numeric:tabular-nums}
.cull-slider{width:100%;accent-color:var(--acc);cursor:pointer}
.cull-preview{display:flex;justify-content:space-between;font-size:12px;font-weight:700}
.plat-switch{position:relative;display:flex;gap:0;background:rgba(255,255,255,.06);border:1px solid var(--hair);border-radius:99px;padding:4px;user-select:none}
.plat-sw-pill{position:absolute;top:3px;bottom:3px;left:3px;width:calc(50% - 3px);border-radius:99px;pointer-events:none;transition:transform var(--dur-slow) var(--ease-spring),background var(--dur-slow) ease,box-shadow var(--dur-slow) ease}
.plat-sw-pill.kick{transform:translateX(100%);background:#53fc18;box-shadow:0 2px 14px -3px rgba(83,252,24,.7)}
.plat-sw-pill.twitch{transform:translateX(0);background:#9146ff;box-shadow:0 2px 14px -3px rgba(145,70,255,.7)}
.plat-sw-btn{position:relative;z-index:1;flex:1;border:none;border-radius:99px;padding:8px 16px;font-size:12px;font-weight:700;cursor:pointer;background:transparent;transition:color var(--dur-slow) ease,transform var(--dur-fast) ease;-webkit-tap-highlight-color:transparent}
.plat-sw-btn:active{transform:scale(.93)}
.plat-sw-btn.sw-on-twitch{color:#fff}.plat-sw-btn.sw-on-kick{color:#0a0a0e}.plat-sw-btn.sw-off{color:var(--fg-2)}
.rd-filters{display:flex;gap:4px;background:rgba(255,255,255,.04);padding:4px;border-radius:var(--r-pill);border:1px solid var(--hair)}
.rd-filter{border:none;background:transparent;color:var(--fg-2);font-size:12px;font-weight:600;
  display:inline-flex;align-items:center;gap:8px;
  padding:8px 12px;border-radius:var(--r-pill);transition:var(--dur-fast)}
.rd-filter:hover{color:var(--fg)}
.rd-filter.active{color:#fff;background:var(--grad);box-shadow:0 4px 14px -4px rgba(184,106,220,.6)}
/* .rd-filter-n (the count badge on a chip) was removed with Clip Review's
   status chips — Review is pending-only now, so All/Pending/Approved was one
   live chip and two that selected nothing. The chip styles above stay: the
   Training mode toggle and the admin sort still use them, without counts. */
/* SPLIT QUEUE. Two independently sorted sections, inside ONE scroller.
   .rd-grid is itself the scrolling box (flex:1 + overflow-y:auto), so stacking
   two of them gives the screen two scrollbars and pins each half inside its own
   little viewport. The scroll moves out to the wrapper and the sections are
   plain grids with the same columns. */
.rd-sects{flex:1;overflow-y:auto;padding-right:4px;min-height:0;
  display:flex;flex-direction:column;gap:24px}
.rd-sect-g{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));
  gap:16px;align-items:stretch}
.rd-sect-h{display:flex;align-items:center;gap:8px;margin-bottom:12px;
  padding-bottom:8px;border-bottom:1px solid var(--hair)}
.rd-sect-t{font-size:12px;font-weight:700;letter-spacing:.12em;
  text-transform:uppercase;color:var(--fg-3)}
/* Same pill as .rd-count, down to the padding: it is the same idea (a count
   beside a label) and two near-identical pills read as a mistake. */
.rd-sect-n{font-size:12px;font-weight:600;color:var(--fg-2);
  background:rgba(255,255,255,.05);padding:4px 8px;border-radius:var(--r-pill)}
/* In a section header the picker sits right; in the toolbar it already does. */
.rd-sortwrap.compact{margin-left:auto}
.rd-grid{flex:1;overflow-y:auto;padding-right:4px;display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:16px;align-content:start;align-items:stretch;min-height:0}
/* min-height, not height. The thumbnail is 16:9 of the COLUMN width, so a card in
   a wide column is taller than one in a narrow column — a fixed 360px fits at the
   310px grid minimum and clips the action row (the "Open on Twitch" button) once
   columns get wider. The grid still stretches every card in a row to the tallest,
   so rows stay level. */
.rd-clip{border-radius:var(--r-lg);overflow:hidden;background:var(--panel);border:1px solid var(--hair);transition:transform var(--dur-slow) var(--ease),border-color var(--dur-slow),box-shadow var(--dur-slow);display:flex;flex-direction:column;min-height:360px}
.rd-clip:hover{transform:translateY(-4px);border-color:rgba(196,137,228,.35);box-shadow:var(--shadow-card)}
/* aspect-ratio, not the height:0 + padding-bottom:56.25% hack. Percentage padding
   resolves to ZERO while a grid row is being intrinsically sized, so the row came
   out shorter than the card it had to hold and the thumbnail pushed the buttons
   out through the bottom edge. aspect-ratio is counted during intrinsic sizing,
   which is what makes min-height above actually reach the content. */
.rd-media{position:relative;width:100%;aspect-ratio:16/9;overflow:hidden}
.rd-thumb{position:absolute;inset:0}
.rd-thumb::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 40%,rgba(0,0,0,.55))}
.rd-media::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 40%,rgba(0,0,0,.55));pointer-events:none;z-index:1}
.rd-play{position:absolute;inset:0;display:grid;place-items:center;z-index:2}
.rd-play .ring{width:56px;height:56px;border-radius:50%;display:grid;place-items:center;padding-left:4px;
  background:rgba(20,12,30,.4);border:1.5px solid rgba(255,255,255,.85);color:#fff;backdrop-filter:blur(4px);transition:transform var(--dur-fast),background var(--dur-fast)}
.rd-clip:hover .rd-play .ring{transform:scale(1.08);background:var(--grad);border-color:transparent;box-shadow:var(--glow)}
/* Both badges overlay the clip player, so a backdrop-filter on them means the
   browser re-blurs that patch of video on every decoded frame. They also sit
   one-per-card in the review grid, each its own blur layer, which is what made
   scrolling a full queue heavy. An opaque background gives the same contrast
   over a bright thumbnail for none of the per-frame cost. */
.rd-scorebadge{position:absolute;top:10px;right:10px;z-index:2;display:inline-flex;align-items:center;gap:4px;
  font-size:12px;font-weight:700;padding:4px 8px;border-radius:var(--r-pill);color:#fff;
  background:rgba(10,8,14,.82);border:1px solid rgba(255,255,255,.16);font-variant-numeric:tabular-nums}
.rd-scorebadge .pip{width:6px;height:6px;border-radius:50%}
.rd-viralbadge{position:absolute;top:10px;left:10px;z-index:2;display:inline-flex;align-items:center;gap:4px;
  font-size:12px;font-weight:800;padding:4px 8px;border-radius:var(--r-pill);color:#fff;
  background:rgba(10,8,14,.82);border:1px solid rgba(255,255,255,.16);font-variant-numeric:tabular-nums}
.rd-viralbadge.hot{background:linear-gradient(135deg,#f7a745,#f943ff);border-color:transparent;box-shadow:0 3px 14px -3px rgba(247,167,69,.65)}
.rd-viralbadge.warm{color:#ffc25c;border-color:rgba(255,194,92,.35)}
/* HIGH INTEREST. Highlightz measured an unusual spike of audience activity at
   this timestamp — used by both the crowd suggester and the VOD scanner, which
   are the same finding arrived at two ways. Sits under the virality badge so
   both are readable. */
.rd-clippedbadge{position:absolute;top:38px;left:10px;z-index:2;display:inline-flex;align-items:center;gap:4px;
  font-size:12px;font-weight:800;letter-spacing:.02em;padding:4px 8px;border-radius:99px;color:#0b0b12;
  background:linear-gradient(135deg,#3ee08a,#2ee0c8);box-shadow:0 3px 12px -3px rgba(62,224,138,.6)}
/* ── Crowd suggestions ────────────────────────────────────────────────────
   A moment VIEWERS clipped, surfaced without consulting our score. It has to
   look unlike everything else in the grid, because it means something else:
   every other card is the detector saying "I found this", and this one is the
   detector saying "I did not, they did".

   Its own purple, --sug (it was gold; recoloured on the owner's call). The
   viral badge runs orange-to-pink and the crowd-clipped badge is green-to-
   teal, so this still reads as its own thing at a glance rather than as a
   variant of either.

   NO backdrop-filter, deliberately — see the note above the badge rules. These
   sit over a playing clip and a blur layer there costs a re-blur of that patch
   on every decoded frame, which is what made scrolling a full queue stutter.

   WHAT ONLY THE SUGGESTED CARDS DID, AND WHAT IT COST. The first version of
   this ran a full-card pulse — a ::after ring animating opacity from 0 to .75
   on a 2.6s loop — over a 30px-blur glow on the card itself. The comment here
   claimed the compositor could do that "without repainting the card". It could
   not: the pseudo-element was never promoted to its own layer, so every frame
   repainted the card region, and that repaint dragged the expensive glow in
   with it. The two compounded, and the cost scaled with how many suggestions
   were on screen — which is why it was ONLY ever the suggested clips.

   MEASURED on the real page at 1440x1000 with 20 suggested cards, isolating
   one change at a time:
       as it shipped                     47.6 fps   p95 33.4ms   23% dropped
       pulse off                         54.8 fps   p95 33.3ms    7%
       pulse off + glow off              60.0 fps   p95 16.8ms    0%
   So BOTH were real, the pulse the larger half. `will-change:opacity` on the
   ring was tried and made it WORSE (45.6 fps, 23%) — promoting twenty layers
   costs more than the repaint it saves.

   WHAT IT DOES NOW. The ring is gone, the glow keeps a 14px blur instead of
   30, and the pulse moved to the BADGE — one small element per card rather
   than the whole card's area, which is cheap enough to be free:
       static glow + badge pulse         60.0 fps   p95 16.8ms    0% dropped
   The card still reads as glowing and alive; it just stopped repainting
   a 310x323 region twenty times over on every frame to do it. */
.rd-clip.suggested{position:relative;border-color:rgba(196,137,228,.7);
  box-shadow:0 0 0 1px rgba(196,137,228,.3),0 4px 14px -6px rgba(184,106,220,.45)}
.rd-clip.suggested:hover{border-color:rgba(196,137,228,.95);
  box-shadow:0 0 0 1px rgba(196,137,228,.45),0 8px 22px -8px rgba(184,106,220,.6)}
.rd-sugbadge{position:absolute;top:10px;right:10px;z-index:2;display:inline-flex;align-items:center;gap:4px;
  font-size:12px;font-weight:800;letter-spacing:.02em;padding:4px 8px;border-radius:var(--r-pill);
  color:#221129;background:linear-gradient(135deg,var(--sug),var(--sug-deep));
  animation:sugpulse 2.6s ease-in-out infinite}
/* The pulse, on the badge and nothing else. ~90x26px of repaint per card
   instead of the whole card, which is the entire difference between 23% of
   frames dropped and none. */
@keyframes sugpulse{
  0%,100%{box-shadow:0 3px 10px -3px rgba(184,106,220,.5)}
  50%{box-shadow:0 3px 20px -2px rgba(184,106,220,.95)}
}
/* A player is open: stop animating. Same reasoning as the blur rules below —
   anything repainting on a timer competes with video decode. */
body.hz-player .rd-sugbadge{animation:none;box-shadow:0 3px 14px -3px rgba(184,106,220,.7)}
@media(prefers-reduced-motion:reduce){
  .rd-sugbadge{animation:none;box-shadow:0 3px 14px -3px rgba(184,106,220,.7)}
}
/* .rd-sugby (a chip naming the viewer whose clip this is) was removed. The
   queue now describes what HIGHLIGHTZ did — it detected the moment from a
   spike in audience interest — rather than crediting individual clippers,
   which read as though the product had outsourced the work. The name is no
   longer stored either: once nothing displayed it, `suggested_by` was a third
   party's identity kept for no reason, so the field is gone from the record. */
/* Amber, matching the weekly-allowance warning: not an error and not a
   verdict on the clip, just a fact that changes what you can do with it. Sits
   bottom-right so it never collides with the score or suggested badge. */
.rd-apbadge{position:absolute;left:10px;bottom:10px;z-index:2;display:inline-flex;align-items:center;gap:4px;
  font-size:12px;font-weight:700;letter-spacing:.02em;padding:4px 8px;border-radius:7px;
  color:#fff;background:rgba(124,107,255,.92)}
.rd-apbadge.failed{background:rgba(255,138,76,.92);color:#0a0a0a}
.rd-apbadge.rendering{background:rgba(196,137,228,.92);color:#0a0a0a}
.rd-apbadge.waiting_file{background:rgba(255,255,255,.18)}
.ap{margin-top:12px;padding:16px}
.ap.on{border-color:rgba(184,106,220,.35)}
.ap-head{display:flex;align-items:center;gap:12px}
.ap-title{flex:1;min-width:0;display:flex;align-items:center;gap:12px}
.ap-title h3{margin:0}
.ap-title .desc{font-size:12px;color:var(--fg-3)}
.ap-switch{all:unset;box-sizing:border-box;cursor:pointer;width:48px;height:28px;border-radius:99px;background:rgba(255,255,255,.12);
  border:1px solid var(--hair-2);position:relative;flex-shrink:0;transition:background var(--dur-fast)}
.ap-switch i{position:absolute;top:2px;left:2px;width:22px;height:22px;border-radius:50%;background:#fff;transition:transform var(--dur-fast)}
.ap-switch.on{background:var(--grad);border-color:transparent}
.ap-switch.on i{transform:translateX(20px)}
.ap-switch:disabled{opacity:.6;cursor:default}
.ap-body{margin-top:16px;display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}
.ap-grp{display:flex;flex-direction:column;gap:8px;min-width:0}
.ap-grp label{font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--fg-3)}
.ap-row{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--fg-3)}
.ap-row .ed-in{width:auto;flex:0 1 160px}
.ap-toggles{flex-direction:row;flex-wrap:wrap;align-items:center;grid-column:1/-1}
.rd-agebadge{position:absolute;right:10px;bottom:10px;z-index:2;display:inline-flex;align-items:center;gap:4px;
  font-size:12px;font-weight:700;letter-spacing:.02em;padding:4px 8px;border-radius:7px;
  color:#0a0a0a;background:rgba(250,204,21,.92)}
.rd-dur{position:absolute;left:10px;bottom:10px;z-index:2;font-size:12px;font-weight:600;color:#fff;
  background:rgba(10,8,14,.6);padding:4px 8px;border-radius:7px;font-variant-numeric:tabular-nums}
.rd-clip-body{padding:12px;flex:1;display:flex;flex-direction:column}
.rd-clip-head{display:flex;align-items:center;justify-content:space-between;gap:8px}
.rd-clip-ch{font-size:14px;font-weight:700;letter-spacing:-.01em;display:flex;align-items:center;gap:8px}
.rd-clip-ch .av{width:22px;height:22px;border-radius:7px;background:var(--grad);display:grid;place-items:center;font-size:12px;font-weight:800;color:#1a0322}
.rd-status{font-size:12px;font-weight:600;padding:4px 8px;border-radius:var(--r-pill);display:inline-flex;align-items:center;gap:4px;text-transform:capitalize}
.rd-status.pending{background:var(--pending-soft);color:var(--pending)}
.rd-status.approved{background:var(--live-soft);color:var(--live)}
.rd-status.rejected{background:var(--danger-soft);color:var(--danger)}
.rd-clip-title{font-size:12px;color:var(--fg);margin-top:8px;font-weight:500;line-height:1.4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rd-clip-meta{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;overflow:hidden;max-height:48px}
.rd-tag{font-size:12px;color:var(--fg-2);background:rgba(255,255,255,.05);padding:4px 8px;border-radius:var(--r-pill)}
/* "Preparing" wears the button's shape so the row does not reflow when it
   becomes the real download, but it is visibly not pressable. */
.rd-dl-wait{opacity:.6}
/* The explanation that stands in for a download button. Quiet — it is an
   answer, not an error, and the clip itself is fine. */
.rd-dl-note{margin-top:8px;padding:12px;border-radius:10px;font-size:12px;
  line-height:1.5;color:var(--fg-2);background:rgba(255,255,255,.03);
  border:1px solid var(--hair)}
.rd-dl-note b{color:var(--fg)}
.rd-clip-actions{display:flex;gap:8px;margin-top:auto;padding-top:12px;flex-wrap:wrap}
.rd-clip-actions .rd-btn{flex:1}
.rd-resolved{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--fg-2);padding:4px 0;flex-wrap:wrap}
.rd-grid-empty{grid-column:1/-1;text-align:center;padding:64px 0;color:var(--fg-3)}
.rd-grid-empty .ic{display:flex;justify-content:center;margin-bottom:16px;color:var(--fg-3)}
.rd-grid-empty .big{font-size:17px;font-weight:700;color:var(--fg);margin-bottom:8px;letter-spacing:-.01em}
/* A <button> now, not an <a> — it switches route inside the SPA rather than
   navigating away. font:inherit and the background reset are what stop a
   button inheriting the browser's chrome instead of this style. */
.rd-emptylink{display:inline-block;margin-top:16px;font-size:12px;font-weight:600;color:var(--acc);
  padding:8px 16px;border-radius:9px;border:1px solid var(--hair-2);transition:var(--dur-fast);
  font-family:inherit;background:none;cursor:pointer}
.rd-emptylink:hover{background:rgba(255,255,255,.05);border-color:var(--acc);color:var(--fg)}
.rd-toast{position:fixed;bottom:26px;left:50%;transform:translate(-50%,90px);opacity:0;
  display:inline-flex;align-items:center;gap:8px;padding:12px 16px;border-radius:var(--r-pill);
  background:rgba(18,14,24,.85);border:1px solid rgba(196,137,228,.35);color:var(--fg);font-size:12px;font-weight:500;
  -webkit-backdrop-filter:blur(20px);backdrop-filter:blur(20px);box-shadow:0 16px 40px -12px rgba(0,0,0,.7);z-index:50;transition:transform var(--dur-fast) var(--ease),opacity var(--dur-fast) var(--ease),background var(--dur-fast) var(--ease),border-color var(--dur-fast) var(--ease),color var(--dur-fast) var(--ease)}
.rd-toast.show{transform:translate(-50%,0);opacity:1}
.rd-undo{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:80;
  display:flex;align-items:center;gap:12px;padding:12px 12px;border-radius:12px;
  background:rgba(18,18,24,.96);border:1px solid var(--hair-2);
  box-shadow:0 10px 34px rgba(0,0,0,.5);font-size:12px;color:var(--fg);
  -webkit-backdrop-filter:blur(18px);backdrop-filter:blur(18px)}
.rd-undo .ico{display:flex;color:var(--fg-3)}
.rd-undo .msg{font-weight:600}
.rd-undo .act{background:rgba(184,106,220,.18);border:1px solid var(--acc);color:var(--acc);
  font-weight:700;font-size:12px;padding:4px 12px;border-radius:8px;cursor:pointer}
.rd-undo .act:hover{background:rgba(184,106,220,.3);color:var(--fg)}
.rd-undo .left{font-size:12px;color:var(--fg-3);font-variant-numeric:tabular-nums;min-width:26px}
.rd-undo .x{background:none;border:none;color:var(--fg-3);cursor:pointer;font-size:14px;line-height:1;padding:0 4px}
.rd-undo .x:hover{color:var(--fg)}
@media(max-width:600px){.rd-undo{left:12px;right:12px;transform:none;justify-content:center}}
.rd-toast .ico{width:24px;height:24px;border-radius:50%;background:var(--grad);display:grid;place-items:center;color:#fff}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:99px;border:2px solid transparent;background-clip:padding-box}
::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.18);background-clip:padding-box}
.rd-nav{display:flex;flex-direction:column;align-items:center;gap:4px;padding:16px 0;
  border-right:1px solid var(--hair);background:rgba(10,10,14,.5);
  -webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px);z-index:6}
.rd-nav .logo{margin-bottom:16px;display:flex}
/* The mark is a transparent PNG cropped to its own ink, so `height` is now the
   height of the GLYPH — under the old plated JPEG the same 44px was mostly
   empty background with a ~19px mark floating in it. Sizes here and everywhere
   else were re-picked against the visible mark, not carried over. */
.rd-nav .logo img{height:34px;filter:drop-shadow(0 0 12px rgba(196,137,228,.45))}
.rd-navitem{width:88px;height:64px;border-radius:16px;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:4px;background:transparent;border:none;
  color:var(--fg-3);font-size:12px;font-weight:600;letter-spacing:.01em;transition:var(--dur-fast);position:relative}
.rd-navitem:hover{color:var(--fg-2);background:rgba(255,255,255,.05)}
/* Closed off on Kick. The button is really `disabled`; this only makes that
   legible — and the not-allowed cursor plus killed hover stops it reading as
   an unresponsive app. */
.rd-navitem.blocked{opacity:.32;cursor:not-allowed;filter:saturate(.4)}
.rd-navitem.blocked:hover{color:var(--fg-3);background:transparent}
.rd-navitem.active{color:#fff}
.rd-navitem.active::before{content:'';position:absolute;inset:0;border-radius:16px;
  background:var(--grad-soft);border:1px solid rgba(196,137,228,.3)}
.rd-navitem.active .ic{color:var(--acc)}
.rd-navitem .ic,.rd-navitem span{position:relative;z-index:1}
.rd-nav .sp{flex:1}
/* Drawer affordances exist only at the mobile breakpoint (see @media below). */
.rd-menubtn,.rd-navscrim{display:none}
.rd-nav .navbadge{position:absolute;top:7px;right:9px;min-width:16px;height:16px;padding:0 4px;
  border-radius:99px;background:var(--grad);color:#fff;font-size:12px;font-weight:800;display:grid;place-items:center;z-index:2}
.rd-header .htitle{font-size:17px;font-weight:700;letter-spacing:-.02em}
.rd-header .hsub{font-size:12px;color:var(--fg-3);margin-top:4px}
.rd-scroll{flex:1;overflow-y:auto;min-height:0;padding:16px 24px}
.rd-section-title{display:flex;align-items:center;gap:12px;margin-bottom:16px}
/* The Clip Review header rows, reused on a screen whose container has no flex
   gap of its own. Same two rows, same order, so the two clip screens read the
   same way. */
.rd-cliphead{display:flex;flex-direction:column;gap:12px;margin-bottom:16px}
.rd-section-title h2{font-size:17px;font-weight:800;letter-spacing:-.025em}
.rd-section-title .cnt{font-size:12px;color:var(--fg-3)}
/* 322px matches the old Clip Review rail: the add-stream box moved here and
   the search input needs the same room, otherwise the placeholder truncates
   next to the preset dropdown. */
.rd-streams-layout{display:grid;grid-template-columns:322px 1fr;gap:16px;flex:1;min-height:0;padding:16px 24px}
/* Search above preset — side by side leaves the input too narrow to read. */
.rd-addrow{flex-direction:column}
.rd-chanlist{display:flex;flex-direction:column;gap:8px;overflow-y:auto;min-height:0;padding-right:4px}
.rd-chanlist .rd-eyebrow{padding:4px 4px 4px}
.rd-chan{text-align:left;padding:12px;border-radius:15px;background:rgba(255,255,255,.025);
  border:1px solid var(--hair);transition:var(--dur-fast);display:flex;align-items:center;gap:12px;width:100%}
.rd-chan:hover{background:rgba(255,255,255,.05)}
.rd-chan.active{background:var(--grad-soft);border-color:rgba(196,137,228,.32)}
.rd-chan .av{width:38px;height:38px;border-radius:12px;background:var(--grad);display:grid;place-items:center;
  font-weight:800;color:#1a0322;font-size:12px;flex-shrink:0}
.rd-chan .nm{font-weight:700;font-size:14px;letter-spacing:-.01em}
.rd-chan .mt{font-size:12px;color:var(--fg-2);margin-top:4px}
.rd-chan .mini{margin-left:auto;font-size:16px;font-weight:800;font-variant-numeric:tabular-nums}
.rd-detail{display:flex;flex-direction:column;gap:16px;overflow-y:auto;min-height:0;padding-right:4px}
.rd-detail-head{display:flex;align-items:center;gap:16px}
.rd-detail-head .av{width:54px;height:54px;border-radius:16px;background:var(--grad);display:grid;place-items:center;
  font-weight:800;color:#1a0322;font-size:17px;box-shadow:var(--glow)}
.rd-detail-head h2{font-size:24px;font-weight:800;letter-spacing:-.025em}
.rd-detail-head .mt{font-size:12px;color:var(--fg-2);margin-top:4px;display:flex;gap:8px;align-items:center}
.rd-detail-head .sp{flex:1}
.rd-card2{border-radius:18px;padding:16px}
.rd-chart-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px}
.rd-chart-head .lbl{font-size:12px;font-weight:600;color:var(--fg-2)}
.rd-chart-head .big{font-size:30px;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.rd-chart{width:100%;height:150px;display:block}
.rd-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.rd-metric{border-radius:15px;padding:16px}
.rd-metric .k{font-size:12px;color:var(--fg-2);font-weight:500}
.rd-metric .v{font-size:24px;font-weight:800;letter-spacing:-.03em;margin-top:8px;font-variant-numeric:tabular-nums}
.rd-weight{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.rd-weight:last-child{margin-bottom:0}
.rd-weight .wl{width:130px;font-size:12px;color:var(--fg-2)}
.rd-weight .wt{flex:1;height:8px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
.rd-weight .wf{height:100%;border-radius:99px;background:var(--grad);transition:transform var(--dur-slow) var(--ease)}
.rd-weight .wv{width:46px;text-align:right;font-size:12px;font-weight:700;font-variant-numeric:tabular-nums}
.rd-settings{max-width:900px;margin:0 auto;display:flex;flex-direction:column;gap:16px;width:100%}
/* ── Tutorial tab ── two columns: a sticky contents rail and the prose. The
   rail is position:sticky inside the scroller, so it follows without a scroll
   listener moving it. */
.rd-tut{max-width:1060px;margin:0 auto;width:100%;display:grid;grid-template-columns:186px minmax(0,1fr);gap:32px;align-items:start}
.tut-toc{position:sticky;top:0;display:flex;flex-direction:column;gap:4px;padding-top:4px}
.tut-toc-k{font-size:12px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--fg-3);padding:0 8px 8px}
.tut-toc-l{text-align:left;background:none;border:0;cursor:pointer;font:inherit;font-size:12px;color:var(--fg-2);padding:4px 8px;border-radius:8px;border-left:2px solid transparent}
.tut-toc-l:hover{color:var(--fg);background:rgba(255,255,255,.04)}
.tut-toc-l.on{color:var(--acc);border-left-color:var(--acc);background:rgba(184,106,220,.10);font-weight:600}
.tut-toc-out{margin-top:12px;font-size:12px;color:var(--fg-3);text-decoration:none;padding:4px 8px}
.tut-toc-out:hover{color:var(--acc)}
.tut-main{min-width:0;display:flex;flex-direction:column;gap:24px;padding-bottom:64px}
/* scroll-margin so a jumped-to heading is not welded to the top edge */
.tut-sec{scroll-margin-top:12px;min-width:0}
.tut-title{font-size:24px;font-weight:800;letter-spacing:-.025em;margin-bottom:8px}
.tut-lead{font-size:14px;color:var(--fg-2);line-height:1.6;max-width:66ch}
.tut-h{font-size:16px;font-weight:700;margin-bottom:8px;display:flex;align-items:center;gap:8px}
.tut-plan{font-size:12px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:var(--acc);background:rgba(184,106,220,.16);padding:4px 8px;border-radius:999px}
.tut-body{font-size:14px;color:var(--fg-2);line-height:1.7;max-width:66ch}
.tut-body b,.tut-steps b,.tut-note b,.tut-tip b{color:var(--fg);font-weight:700}
.tut-steps{margin:12px 0 0;padding-left:16px;display:flex;flex-direction:column;gap:8px;font-size:14px;color:var(--fg-2);line-height:1.6;max-width:66ch}
.tut-note{margin-top:12px;font-size:12px;color:var(--fg-3);line-height:1.6;max-width:66ch}
.tut-fig{margin:16px 0 0}
.tut-media{width:100%;height:auto;border-radius:12px;border:1px solid var(--hair);display:block;background:rgba(255,255,255,.02)}
.tut-cap{margin-top:8px;font-size:12px;color:var(--fg-3);line-height:1.5}
.tut-ph{margin-top:16px;border:1px dashed var(--hair);border-radius:12px;padding:24px 24px;display:flex;flex-direction:column;gap:4px;background:rgba(255,255,255,.02)}
.tut-ph-k{font-size:12px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--fg-3)}
.tut-ph-a{font-size:12px;color:var(--fg-2);line-height:1.6}
.tut-tip{margin-top:12px;border-left:2px solid var(--acc);background:rgba(184,106,220,.07);border-radius:0 10px 10px 0;padding:12px 16px}
.tut-tip-k{font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--acc)}
.tut-tip p{margin-top:4px;font-size:14px;color:var(--fg-2);line-height:1.6}
.tut-tablewrap{margin-top:12px;overflow-x:auto;border:1px solid var(--hair);border-radius:12px}
.tut-table{width:100%;border-collapse:collapse;font-size:14px;min-width:460px}
.tut-table th,.tut-table td{padding:12px 12px;text-align:left;border-bottom:1px solid var(--hair)}
.tut-table thead th{font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:var(--fg-3);font-weight:700}
.tut-table tbody th{font-weight:600;color:var(--fg-2)}
.tut-table tbody tr:last-child th,.tut-table tbody tr:last-child td{border-bottom:0}
.tut-faq{margin-top:12px;border-top:1px solid var(--hair)}
.tut-q{border-bottom:1px solid var(--hair)}
.tut-q-h{width:100%;display:flex;align-items:center;justify-content:space-between;gap:16px;background:none;border:0;cursor:pointer;font:inherit;font-size:14px;font-weight:600;color:var(--fg);text-align:left;padding:12px 4px}
.tut-q-h:hover{color:var(--acc)}
.tut-q-c{color:var(--fg-3);font-size:17px;flex-shrink:0}
.tut-q.on .tut-q-c{color:var(--acc)}
.tut-q-a{padding:0 4px 16px;font-size:14px;color:var(--fg-2);line-height:1.7;max-width:70ch}
.tut-q-a b{color:var(--fg)}
.tut-q-a a{color:var(--acc)}
@media(max-width:820px){
  /* The rail becomes a scrolling strip above the prose rather than vanishing —
     on a phone the contents list is how you skip to the part you need. */
  .rd-tut{grid-template-columns:1fr;gap:16px}
  .tut-toc{position:static;flex-direction:row;overflow-x:auto;gap:4px;padding-bottom:4px}
  .tut-toc-k{display:none}
  .tut-toc-l{white-space:nowrap;border-left:0;border-bottom:2px solid transparent;border-radius:8px 8px 0 0}
  .tut-toc-l.on{border-left:0;border-bottom-color:var(--acc)}
  .tut-toc-out{margin-top:0;white-space:nowrap}
  .tut-title{font-size:17px}
}
.rd-card{border-radius:18px;padding:24px}
.rd-card h3{font-size:14px;font-weight:700;display:flex;align-items:center;gap:8px;letter-spacing:-.01em}
.rd-card h3 .si{width:30px;height:30px;border-radius:9px;background:var(--grad-soft);color:var(--acc);display:grid;place-items:center}
.rd-card .desc{font-size:12px;color:var(--fg-3);margin:4px 0 16px 32px}
/* ── Clip Editor ── */
.rd-drop{border:2px dashed var(--hair);border-radius:16px;padding:32px 16px;text-align:center;
  cursor:pointer;transition:border-color var(--dur-fast),background var(--dur-fast);background:rgba(255,255,255,.015)}
.rd-drop:hover{border-color:var(--acc-2);background:rgba(184,106,220,.05)}
.rd-drop.over{border-color:var(--acc);background:rgba(184,106,220,.11)}
.rd-drop .di{color:var(--acc);margin-bottom:8px}
.rd-drop .dt{font-size:14px;font-weight:700;margin-bottom:4px}
.rd-drop .ds{font-size:12px;color:var(--fg-3)}
.rd-quota{height:7px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden;margin:8px 0 4px}
.rd-quota i{display:block;height:100%;border-radius:99px;background:var(--grad);transition:transform var(--dur-slow) var(--ease)}
.rd-quota-full i{background:linear-gradient(135deg,#ff5a78,#f7a745)}
.rd-up{border-radius:14px;border:1px solid var(--hair);background:rgba(255,255,255,.02);overflow:hidden;
  display:flex;flex-direction:column}
.rd-up video{width:100%;aspect-ratio:16/9;background:#000;display:block;object-fit:contain}
.rd-up .ub{padding:12px 12px;display:flex;align-items:center;gap:8px}
.rd-up .un{font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.rd-up .um{font-size:12px;color:var(--fg-3);margin-top:4px}
/* Twitch clip import cards */
.rd-tw{border-radius:14px;border:1px solid var(--hair);background:rgba(255,255,255,.02);
  overflow:hidden;display:flex;flex-direction:column}
.rd-tw iframe{width:100%;aspect-ratio:16/9;border:none;display:block;background:#000}
.rd-tw .tw-thumb{position:relative;display:block;width:100%;aspect-ratio:16/9;padding:0;border:none;
  background:#0b0b12;cursor:pointer;overflow:hidden}
.rd-tw .tw-thumb img{width:100%;height:100%;object-fit:cover;display:block;transition:transform var(--dur-slow)}
.rd-tw .tw-thumb:hover img{transform:scale(1.04)}
.rd-tw .tw-noimg{width:100%;height:100%;display:grid;place-items:center;color:var(--fg-3)}
.rd-tw .tw-play{position:absolute;inset:0;margin:auto;width:40px;height:40px;border-radius:50%;
  display:grid;place-items:center;background:rgba(0,0,0,.55);color:#fff;
  -webkit-backdrop-filter:blur(4px);backdrop-filter:blur(4px);transition:var(--dur-fast)}
.rd-tw .tw-thumb:hover .tw-play{background:var(--acc-2);transform:scale(1.08)}
.rd-tw .tw-dur{position:absolute;right:7px;bottom:7px;font-size:12px;font-weight:700;color:#fff;
  background:rgba(0,0,0,.7);padding:4px 4px;border-radius:6px}
.rd-tw .tw-meta{padding:8px 12px;display:flex;flex-direction:column;gap:4px;min-width:0}
.rd-tw .tw-title{font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rd-tw .tw-sub{font-size:12px;color:var(--fg-3)}
.rd-tw .tw-link{font-size:12px;color:var(--acc);font-weight:600;margin-top:4px}
.rd-tw .tw-link:hover{text-decoration:underline}
/* ── Editor ── */
.tw-box{width:min(900px,100%);border-radius:18px;padding:16px}
.tw-frame{position:relative;width:100%;aspect-ratio:16/9;border-radius:12px;overflow:hidden;background:#000}
.tw-frame iframe{position:absolute;inset:0;width:100%;height:100%;border:none}
/* Announcement: above the editor (200) and every other layer, because the
   one job of this surface is to be impossible to miss. */
.rd-ann-bg{position:fixed;inset:0;z-index:300;background:rgba(5,4,8,.82);display:grid;place-items:center;padding:24px}
.rd-ann{width:min(520px,100%);padding:24px;border-radius:20px;display:flex;flex-direction:column;gap:12px}
.rd-ann-k{display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--acc)}
.rd-ann h3{margin:0;font-size:20px;font-weight:800;letter-spacing:-.01em}
.rd-ann-body{white-space:pre-wrap;font-size:14px;line-height:1.6;color:var(--fg-2);max-height:50vh;overflow:auto}
.rd-ann .rd-btn{align-self:flex-end}
.ed-bg{position:fixed;inset:0;z-index:200;background:rgba(4,4,8,.86);display:flex;
  align-items:center;justify-content:center;padding:16px;-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px)}
/* The editor is a flex column so the side panel can scroll on its own while
   the stage, transport and timeline stay put — the old single scrolling box
   pushed the export button below the fold on every laptop. */
/* A grid: header across the top, main | side in the middle, and the export
   footer as its OWN row under the side column. It is never inside a
   scroller, so it can never cover a control on a phone. */
.ed{width:min(1220px,100%);max-height:94vh;display:grid;grid-template-columns:minmax(0,1fr) 340px;
  grid-template-rows:auto minmax(0,1fr) auto;border-radius:20px;
  background:var(--rd-bg-2);border:1px solid var(--hair);outline:none;overflow:hidden}
/* Solid, not glass: the late @supports .glass rule paints a near-transparent
   gradient, and through it the library page bled into the editor on a phone. */
.ed.glass{background:var(--rd-bg-2)}
.ed-head{grid-column:1/-1;display:flex;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid var(--hair);flex-shrink:0}
.ed-ico{color:var(--acc);display:flex}
.ed-title{flex:1;min-width:0}
.ed-head h3{font-size:16px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ed-sub{font-size:12px;color:var(--fg-3);font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ed-x{width:32px;height:32px;border-radius:var(--r-sm);border:1px solid var(--hair);background:rgba(255,255,255,.05);
  color:var(--fg-2);display:grid;place-items:center;cursor:pointer;flex-shrink:0;transition:background var(--dur-fast),color var(--dur-fast)}
.ed-x:hover{background:rgba(255,255,255,.1);color:#fff}
.ed-x:disabled{opacity:.4;cursor:default}
.ed-body{display:contents}
.ed-main{grid-column:1;grid-row:2/4;display:flex;flex-direction:column;gap:8px;padding:16px;min-width:0;min-height:0;overflow:auto}
/* The stage is the framing control: drag pans, wheel and pinch zoom.
   touch-action:none so a finger on the picture moves the picture, not the page. */
.ed-stage{position:relative;background:#000;border-radius:14px;overflow:hidden;display:block;
  height:min(58vh,620px);cursor:grab;touch-action:none;user-select:none;-webkit-user-select:none;flex-shrink:0}
.ed-stage:active{cursor:grabbing}
.ed-stage.busy{cursor:progress}
/* The canvas is pinned to the stage's box and letterboxes its own bitmap.
   As a grid item with max-width/max-height it did NOT scale in Chromium: the
   row was auto-sized, so a percentage height resolved to auto and a 720x1280
   canvas sat at native size, cropped to the top of the stage — hiding the
   captions and the bottom of every frame. Absolute against a definite-height
   parent cannot resolve any other way. */
.ed-stage canvas{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;display:block;pointer-events:none}
.ed-loading{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;gap:8px;font-size:12px;color:var(--fg-3)}
.ed-loading span{width:16px;height:16px;border-radius:50%;border:2px solid var(--hair-2);border-top-color:var(--acc);animation:edspin .8s linear infinite}
@keyframes edspin{to{transform:rotate(360deg)}}
.ed-hint{position:absolute;left:50%;bottom:12px;transform:translateX(-50%);padding:4px 12px;border-radius:var(--r-pill);
  background:rgba(4,4,8,.7);color:var(--fg-2);font-size:12px;white-space:nowrap;pointer-events:none;animation:edfade 6s forwards}
.ed-hint.on{animation:none;color:#fff}
@keyframes edfade{0%,70%{opacity:1}100%{opacity:0}}
.ed-transport{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ed-play{width:40px;height:40px;border-radius:50%;border:none;background:var(--grad);color:#fff;display:grid;place-items:center;
  cursor:pointer;flex-shrink:0;box-shadow:0 6px 18px -6px rgba(184,106,220,.6);padding:0}
.ed-play:disabled{opacity:.4;cursor:default;box-shadow:none}
.ed-pause{width:12px;height:14px;border-left:4px solid #fff;border-right:4px solid #fff;display:block}
.ed-step{width:32px;height:32px;border-radius:var(--r-sm);border:1px solid var(--hair);background:rgba(255,255,255,.05);
  color:var(--fg-2);font-size:17px;line-height:1;cursor:pointer;padding:0;transition:background var(--dur-fast),color var(--dur-fast)}
.ed-step:hover{background:rgba(255,255,255,.1);color:#fff}
.ed-step:disabled{opacity:.4;cursor:default}
.ed-clock{font-size:14px;font-variant-numeric:tabular-nums;margin-left:4px}
.ed-clock b{font-weight:700}
.ed-clock .dim{color:var(--fg-3)}
.ed-cut{margin-left:auto;display:flex;gap:4px}
.ed-mark{padding:8px 12px;border-radius:var(--r-sm);border:1px solid var(--hair);background:rgba(255,255,255,.05);color:var(--fg-2);
  font-size:12px;font-weight:600;cursor:pointer;transition:background var(--dur-fast),color var(--dur-fast)}
.ed-mark:hover{background:rgba(255,255,255,.1);color:#fff}
.ed-mark:disabled{opacity:.4;cursor:default}
/* The timeline: a filmstrip with the cut lit and the rest dimmed, two handles
   and a playhead. Handles are 24px wide for a thumb; the visible bar is 12. */
.ed-tl{position:relative;height:64px;border-radius:12px;overflow:hidden;background:rgba(255,255,255,.04);border:1px solid var(--hair);
  touch-action:none;user-select:none;-webkit-user-select:none;cursor:pointer;flex-shrink:0}
.ed-tl.off{pointer-events:none;opacity:.6}
.ed-film{position:absolute;inset:0;display:grid;grid-template-columns:repeat(16,1fr)}
.ed-film img,.ed-film span{width:100%;height:100%;object-fit:cover;display:block;pointer-events:none;background:rgba(255,255,255,.03)}
.ed-dim{position:absolute;top:0;bottom:0;background:rgba(4,4,8,.72);pointer-events:none}
.ed-dim.l{left:0}
.ed-dim.r{right:0}
.ed-sel{position:absolute;top:0;bottom:0;border-top:3px solid var(--acc);border-bottom:3px solid var(--acc);pointer-events:none;box-sizing:border-box}
.ed-hd{position:absolute;top:0;bottom:0;width:24px;margin-left:-12px;cursor:ew-resize;z-index:2;display:grid;place-items:center}
.ed-hd i{position:relative;display:block;width:12px;height:100%;background:var(--acc);box-shadow:0 0 0 1px rgba(0,0,0,.5)}
.ed-hd.l i{border-radius:8px 4px 4px 8px}
.ed-hd.r i{border-radius:4px 8px 8px 4px}
.ed-hd i::after{content:'';position:absolute;top:50%;left:50%;width:2px;height:16px;transform:translate(-50%,-50%);background:rgba(0,0,0,.55);border-radius:1px}
.ed-ph{position:absolute;top:0;bottom:0;width:16px;margin-left:-8px;z-index:3;cursor:ew-resize;display:grid;place-items:center}
.ed-ph b{display:block;width:2px;height:100%;background:#fff;box-shadow:0 0 6px #fff;pointer-events:none}
.ed-tlinfo{display:flex;justify-content:space-between;font-size:12px;color:var(--fg-2);font-variant-numeric:tabular-nums}
.ed-tlinfo i{font-style:normal;color:var(--fg-3);margin-right:4px}
.ed-tlinfo .mid{font-weight:700;color:#fff}
.ed-side{grid-column:2;grid-row:2;display:flex;flex-direction:column;min-height:0;border-left:1px solid var(--hair)}
.ed-tpls{padding:16px 16px 0;display:flex;flex-direction:column;gap:8px;flex-shrink:0}
.ed-sec-t{font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--fg-3);
  display:flex;align-items:center;gap:8px;padding:16px 16px 0}
.ed-tpls .ed-sec-t{padding:0}
.ed-sec-t b{width:20px;height:20px;border-radius:50%;background:var(--grad-soft);border:1px solid rgba(196,137,228,.4);
  color:#fff;font-size:12px;display:grid;place-items:center;letter-spacing:0}
/* Five cards, two to a row, the fifth across the bottom: big enough to
   read and hit, with the diagram beside the name rather than stacked. */
.ed-tpl-row{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.ed-tpl{display:flex;flex-direction:row;align-items:center;gap:12px;padding:8px 12px;border-radius:12px;
  border:1px solid var(--hair);background:rgba(255,255,255,.04);color:var(--fg-2);cursor:pointer;min-width:0;text-align:left;
  transition:background var(--dur-fast),border-color var(--dur-fast),color var(--dur-fast)}
.ed-tpl:last-child{grid-column:1/-1}
.ed-tpl:hover{background:rgba(255,255,255,.08);color:#fff}
.ed-tpl.on{background:var(--grad-soft);border-color:rgba(196,137,228,.6);color:#fff;box-shadow:0 0 0 1px rgba(196,137,228,.25)}
.ed-tpl:disabled{opacity:.5;cursor:default}
.ed-tpl-n{font-size:14px;font-weight:700;line-height:1.2;min-width:0;overflow-wrap:anywhere}
/* Tiny 9:16 diagrams of each layout, drawn in CSS so they are the same
   colours as the rest of the panel. i = the main picture, b = the accent. */
.ed-tpl-ic{position:relative;width:20px;height:34px;border-radius:4px;background:rgba(255,255,255,.08);overflow:hidden;flex-shrink:0}
.ed-tpl-ic i,.ed-tpl-ic b{position:absolute;display:block;border-radius:2px;background:rgba(255,255,255,.55)}
.ed-tpl-ic.camgame i{left:0;right:0;top:0;height:40%;background:rgba(255,255,255,.55)}
.ed-tpl-ic.camgame b{left:0;right:0;top:44%;height:32%;background:rgba(255,255,255,.3)}
.ed-tpl-ic.full i{inset:0;border-radius:0}
.ed-tpl-ic.full b{left:4px;right:4px;bottom:6px;height:4px;background:var(--acc)}
.ed-tpl-ic.blur i{left:0;right:0;top:34%;height:32%}
.ed-tpl-ic.blur b{inset:0;background:rgba(255,255,255,.14);border-radius:0}
.ed-tpl-ic.punch i{left:-4px;right:-4px;top:2px;bottom:2px;border:2px solid rgba(255,255,255,.55);background:rgba(255,255,255,.2)}
.ed-tpl-ic.punch b{left:4px;right:4px;bottom:6px;height:4px;background:var(--acc)}
.ed-tpl-ic.hook i{inset:0;border-radius:0;background:rgba(255,255,255,.3)}
.ed-tpl-ic.hook b{left:3px;right:3px;top:4px;height:6px;background:#fff}
/* The adjust sections: one open at a time, each header carrying its current
   setting so the whole state reads at a glance without opening anything. */
.ed-panel{flex:1;overflow-y:auto;padding:8px 16px 16px;display:flex;flex-direction:column;gap:8px;min-height:0}
.ed-sec{border:1px solid var(--hair);border-radius:12px;background:rgba(255,255,255,.03);overflow:hidden;flex-shrink:0}
.ed-sec.open{border-color:rgba(196,137,228,.4);background:rgba(255,255,255,.04)}
.ed-sec-h{all:unset;box-sizing:border-box;width:100%;display:flex;align-items:center;gap:12px;padding:12px 16px;cursor:pointer;
  transition:background var(--dur-fast)}
.ed-sec-h:hover{background:rgba(255,255,255,.04)}
.ed-sec-l{font-size:14px;font-weight:700;color:var(--fg);flex-shrink:0;min-width:64px}
.ed-sec-s{flex:1;min-width:0;font-size:12px;color:var(--fg-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:right}
.ed-sec.open .ed-sec-s{color:var(--acc)}
.ed-sec-c{color:var(--fg-3);font-size:16px;line-height:1;transition:transform var(--dur-fast)}
.ed-sec.open .ed-sec-c{transform:rotate(90deg)}
.ed-sec-b{padding:4px 16px 16px;display:flex;flex-direction:column;gap:16px;border-top:1px solid var(--hair)}
.ed-foot{grid-column:2;grid-row:3;padding:16px;border-top:1px solid var(--hair);border-left:1px solid var(--hair);display:flex;flex-direction:column;gap:8px;flex-shrink:0;background:rgba(14,11,17,.6)}
.ed-export{width:100%;padding:16px 16px;font-size:16px;border-radius:12px}
.ed-grp{display:flex;flex-direction:column;gap:8px}
.ed-grp label{font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--fg-3);margin-top:8px}
.ed-grp label:first-child{margin-top:8px}
.ed-row{display:flex;align-items:center;gap:8px}
.ed-row input[type=range]{flex:1;accent-color:var(--acc);cursor:pointer;min-width:0}
.ed-num{font-size:12px;color:var(--fg-3);font-variant-numeric:tabular-nums;min-width:44px;text-align:right}
.ed-in{width:100%;background:rgba(255,255,255,.05);border:1px solid var(--hair);border-radius:12px;
  padding:12px 12px;color:var(--fg);font-size:14px;font-family:inherit}
.ed-in:focus{outline:none;border-color:var(--acc-2)}
.ed-seg{display:flex;gap:4px;flex-wrap:wrap}
.ed-seg button{flex:1;min-width:64px;padding:12px 8px;border-radius:12px;font-size:14px;font-weight:700;
  background:rgba(255,255,255,.05);border:1px solid var(--hair);color:var(--fg-3);cursor:pointer;transition:var(--dur-fast)}
.ed-seg button.on{background:var(--grad-soft);border-color:rgba(196,137,228,.4);color:#fff}
.ed-seg small{display:block;font-size:12px;font-weight:500;color:var(--fg-3)}
.ed-seg button.on small{color:rgba(255,255,255,.75)}
.ed-prog{height:6px;border-radius:99px;background:rgba(255,255,255,.08);overflow:hidden}
.ed-prog i{display:block;height:100%;width:100%;transform-origin:left;transform:scaleX(0);background:var(--grad);border-radius:99px;transition:transform var(--dur-fast) linear}
.ed-note{font-size:12px;color:var(--fg-3);line-height:1.5}
.ed-note.ok{color:var(--acc)}
.ed-note kbd{font-family:inherit;font-size:12px;padding:0 4px;border-radius:4px;border:1px solid var(--hair-2);background:rgba(255,255,255,.06);color:var(--fg-2);margin:0 4px 0 0}
/* Phone: the editor is the whole screen. Stage on top, then the transport
   and strip, then the tabs; the export button sticks to the bottom so it is
   never below a fold. */
@media(max-width:860px){
  .ed-bg{padding:0}
  .ed{width:100%;height:100%;max-height:none;border-radius:0;border:none;grid-template-columns:1fr}
  .ed-body{grid-column:1;grid-row:2;overflow:auto;display:flex;flex-direction:column;min-height:0}
  .ed-main{grid-column:auto;grid-row:auto;overflow:visible;padding:12px;flex-shrink:0}
  .ed-stage{height:min(42vh,420px)}
  .ed-side{grid-column:auto;grid-row:auto;border-left:none;border-top:1px solid var(--hair);min-height:0;flex:1 0 auto}
  .ed-panel{overflow:visible;padding-bottom:32px}
  .ed-foot{grid-column:1;grid-row:3;border-left:none;background:rgba(14,11,17,.96)}
  .ed-cut{margin-left:0;width:100%}
  .ed-cut .ed-mark{flex:1}
  .ed-tpl-n{font-size:14px}
  .ed-tpls{padding:12px 12px 0}
  .ed-panel{padding:8px 12px 32px}
}
.pub-row{display:flex;align-items:center;gap:8px;margin-top:8px}
.pub-row .rd-btn{flex-shrink:0;min-width:104px;justify-content:center}
.pub-ok{font-size:12px;color:var(--acc)}
.pub-warn{font-size:12px;color:#f7a745;line-height:1.4}
.q-row{display:flex;align-items:center;gap:8px;padding:8px 12px;margin-bottom:4px;
  border-radius:12px;background:rgba(255,255,255,.03);border:1px solid var(--hair)}
.q-row.due{border-color:rgba(184,106,220,.5);background:var(--grad-soft)}
.q-row.missed{border-color:rgba(255,138,76,.35)}
.q-when{flex-shrink:0;min-width:74px;font-size:12px;font-weight:700;color:var(--acc)}
.q-row.missed .q-when{color:#f7a745}
.q-mid{flex:1;min-width:0}
.q-name{font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.q-sub{font-size:12px;color:var(--fg-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* ── Scheduler: account chips, inbox tray, month calendar, day list, drawer ── */
.sc-top-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.sc-acct{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border-radius:99px;
  border:1px solid var(--hair);background:rgba(255,255,255,.03);font-size:12px;font-weight:600;
  color:var(--fg-2);text-decoration:none;transition:border-color var(--dur-fast),background var(--dur-fast)}
a.sc-acct:hover{border-color:var(--hair-2);background:rgba(255,255,255,.06);color:var(--fg)}
.sc-acct.on{border-color:rgba(184,106,220,.45);color:var(--fg);background:var(--grad-soft)}
.sc-acct.err{border-color:rgba(255,138,76,.5);color:#f7a745}
.sc-acct.off{opacity:.5}
.sc-acct .dot{width:8px;height:8px;border-radius:99px;background:var(--fg-3);flex-shrink:0}
.sc-acct.on .dot{background:#5ce0a8}
.sc-acct.err .dot{background:#f7a745}
.sc-acct button{all:unset;cursor:pointer;color:var(--fg-3);display:inline-flex;margin-left:4px}
.sc-acct button:hover{color:var(--fg)}
.sc-hint{font-size:12px;color:var(--fg-3);margin:8px 0 0}
.sc-sub{font-size:12px;color:var(--fg-3);line-height:1.5}
.sc-sub a{color:var(--acc)}
.sc-inbox{margin-top:12px;padding:12px 16px}
.sc-inbox-head{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:700;color:var(--fg-2);margin-bottom:8px;flex-wrap:wrap}
.sc-inbox-head .sc-sub{font-weight:500}
.sc-tray{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px}
.sc-tile{flex:0 0 auto;width:168px;padding:8px 12px;border-radius:12px;background:rgba(255,255,255,.04);
  border:1px solid var(--hair);cursor:grab;display:flex;flex-direction:column;gap:4px;transition:border-color var(--dur-fast)}
.sc-tile:hover{border-color:rgba(184,106,220,.45)}
.sc-tile:active{cursor:grabbing}
.sc-tile b{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-tile span{font-size:12px;color:var(--fg-3)}
.sc-cal{margin-top:12px;padding:16px}
.sc-cal-head{display:flex;align-items:center;gap:8px;margin-bottom:12px}
.sc-cal-head h3{flex:1;font-size:16px;font-weight:800;letter-spacing:-.02em;margin:0}
.sc-cal-nav{all:unset;box-sizing:border-box;cursor:pointer;width:32px;height:32px;border-radius:10px;display:grid;
  place-items:center;border:1px solid var(--hair);color:var(--fg-2);font-size:16px;line-height:1}
.sc-cal-nav:hover{background:rgba(255,255,255,.06);color:var(--fg)}
.sc-dow{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px;margin-bottom:4px}
.sc-dow span{font-size:12px;font-weight:700;color:var(--fg-3);text-align:center;text-transform:uppercase;letter-spacing:.06em}
.sc-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px}
.sc-day{min-height:96px;border-radius:12px;border:1px solid var(--hair);background:rgba(255,255,255,.02);
  padding:8px;display:flex;flex-direction:column;gap:4px;cursor:pointer;min-width:0;
  transition:background var(--dur-fast),border-color var(--dur-fast)}
.sc-day:hover{background:rgba(255,255,255,.04)}
.sc-day.is-out{opacity:.35}
.sc-day.is-today{border-color:rgba(184,106,220,.55)}
.sc-day.is-sel{background:var(--grad-soft);border-color:rgba(184,106,220,.7)}
.sc-day.is-over{background:rgba(184,106,220,.2);border-color:var(--acc)}
.sc-day .n{font-size:12px;font-weight:700;color:var(--fg-2)}
.sc-day.is-today .n{color:var(--acc)}
.sc-day .chips{display:flex;flex-direction:column;gap:4px;min-width:0}
.sc-chip{display:flex;align-items:center;gap:4px;padding:4px 8px;border-radius:8px;font-size:12px;line-height:1.2;
  background:rgba(255,255,255,.06);border:1px solid transparent;min-width:0;cursor:pointer;transition:background var(--dur-fast)}
.sc-chip:hover{background:rgba(255,255,255,.1)}
.sc-chip i{width:8px;height:8px;border-radius:99px;flex-shrink:0;background:var(--acc)}
.sc-chip.posted i{background:#5ce0a8}
.sc-chip.posted{opacity:.75}
.sc-chip.failed i,.sc-chip.missed i{background:#f7a745}
.sc-chip.posting i{animation:scPulse 1s infinite}
.sc-chip.due{border-color:rgba(184,106,220,.5)}
@keyframes scPulse{0%,100%{opacity:1}50%{opacity:.25}}
.sc-chip span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-chip .t{color:var(--fg-3);flex-shrink:0}
.sc-more{font-size:12px;color:var(--fg-3);padding:0 8px}
@media(max-width:760px){
  .sc-day{min-height:56px;padding:4px}
  .sc-day .chips{flex-direction:row;flex-wrap:wrap}
  .sc-chip span,.sc-chip .t,.sc-more{display:none}
  .sc-chip{padding:4px}
}
.sc-daylist{margin-top:12px;padding:16px}
.sc-row{display:flex;align-items:center;gap:12px;padding:8px 0;border-top:1px solid var(--hair);cursor:pointer}
.sc-row:hover .name{color:var(--acc)}
.sc-row .when{width:72px;flex-shrink:0;font-size:12px;font-weight:700;color:var(--acc)}
.sc-row .name{flex:1;min-width:0;font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-row .st{font-size:12px;color:var(--fg-3);flex-shrink:0}
.sc-row .st.failed,.sc-row .st.missed{color:#f7a745}
.sc-row .st.posted{color:#5ce0a8}
.sc-state{font-size:12px;font-weight:800;color:var(--acc);flex-shrink:0}
.sc-state.failed,.sc-state.missed{color:#f7a745}
.sc-state.posted{color:#5ce0a8}
/* No backdrop blur on the scrim: the drawer holds a playing <video>, and a live
   blur layer beside a decoding video drops frames (test_player_smoothness). */
.sc-drawer-bg{position:fixed;inset:0;z-index:150;background:rgba(4,4,8,.72)}
.sc-drawer{position:fixed;top:0;right:0;bottom:0;z-index:151;width:min(460px,100%);display:flex;flex-direction:column;
  background:rgba(16,14,22,.97);border-left:1px solid var(--hair-2);box-shadow:-24px 0 64px -24px rgba(0,0,0,.8);
  animation:scSlide .2s ease-out}
@keyframes scSlide{from{transform:translateX(24px);opacity:0}to{transform:none;opacity:1}}
.sc-dr-head{display:flex;align-items:center;gap:8px;padding:16px;border-bottom:1px solid var(--hair)}
.sc-dr-head b{flex:1;min-width:0;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-dr-body{flex:1;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:12px}
.sc-dr-body video{width:100%;max-height:280px;border-radius:12px;background:#000;object-fit:contain}
.sc-dr-foot{padding:12px 16px;border-top:1px solid var(--hair);display:flex;gap:8px;flex-wrap:wrap}
.sc-dr-foot a{text-decoration:none}
.sc-lbl{font-size:12px;font-weight:700;color:var(--fg-3);text-transform:uppercase;letter-spacing:.06em}
.sc-pchips{display:flex;gap:8px;flex-wrap:wrap}
.sc-pchip{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border-radius:99px;border:1px solid var(--hair);
  background:rgba(255,255,255,.03);font-size:12px;font-weight:600;cursor:pointer;color:var(--fg-2);transition:border-color var(--dur-fast),background var(--dur-fast)}
.sc-pchip:hover{border-color:var(--hair-2)}
.sc-pchip.on{border-color:rgba(184,106,220,.55);background:var(--grad-soft);color:var(--fg)}
.sc-pchip small{font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:var(--acc)}
.sc-pchip:disabled{opacity:.5;cursor:default}
.sc-res{display:flex;flex-direction:column;gap:4px;font-size:12px}
.sc-res div{display:flex;gap:8px;align-items:flex-start}
.sc-res b{width:72px;flex-shrink:0}
.sc-res a{color:var(--acc)}
.sc-note{color:var(--fg-3)}
.sr-tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
@media(max-width:620px){.sr-tiles{grid-template-columns:repeat(2,1fr)}}
.sr-tile{background:rgba(255,255,255,.04);border:1px solid var(--hair);
  border-radius:12px;padding:12px;text-align:center}
.sr-tile .k{font-size:12px;color:var(--fg-3);text-transform:uppercase;letter-spacing:.05em}
.sr-tile .v{font-size:24px;font-weight:800;letter-spacing:-.02em;margin-top:4px}
.sr-list{margin-top:8px;display:flex;flex-direction:column;gap:8px}
.sr-row{display:flex;align-items:center;gap:12px}
.sr-when{flex-shrink:0;width:104px;font-size:12px;color:var(--fg-3)}
.sr-bar{flex:1;height:8px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
.sr-bar i{display:block;height:100%;background:var(--grad);border-radius:99px}
.sr-nums{flex-shrink:0;font-size:12px;color:var(--fg-2)}
.rd-lost{display:flex;align-items:center;gap:12px;padding:12px 16px;margin-bottom:12px;
  border-radius:14px;background:rgba(255,138,76,.09);border:1px solid rgba(255,138,76,.3)}
.rd-lost .ic{flex-shrink:0;color:#f7a745;display:grid;place-items:center}
.rd-lost .tx{flex:1;min-width:0;font-size:12px;line-height:1.5;color:var(--fg-2)}
.rd-lost .tx b{color:#f7a745}
.rd-lost-x{flex-shrink:0;background:none;border:0;color:var(--fg-3);font-size:24px;
  line-height:1;cursor:pointer;padding:0 4px;transition:color var(--dur-fast)}
.rd-lost-x:hover{color:var(--fg-1)}
/* The refusal notice is the same banner in red. A different colour because it
   is a different KIND of problem: the amber one is a limit the user can act on
   by upgrading or clearing, this one is a wall on the broadcaster's side that
   they cannot act on at all. Same shape so the X reads as the same control. */
.rd-refused{background:rgba(255,122,138,.08);border-color:rgba(255,122,138,.32)}
.rd-refused .ic,.rd-refused .tx b{color:#ff7a8a}
@media(max-width:640px){.rd-lost{flex-direction:column;align-items:flex-start}}
.rv{max-width:460px;width:100%;padding:24px 24px;border-radius:20px;
  display:flex;flex-direction:column;gap:12px}
.rv h3{font-size:17px;font-weight:800;margin:0}
.rv-sub{font-size:12px;color:var(--fg-3);margin:0;line-height:1.5}
.rv-stars{display:flex;gap:4px;margin:4px 0}
.rv-star{background:none;border:0;cursor:pointer;font-size:30px;line-height:1;
  padding:0 4px;color:rgba(255,255,255,.2);transition:color var(--dur-fast)}
.rv-star.on{color:#ffc25c}
.rv-check{display:flex;align-items:flex-start;gap:8px;font-size:12px;
  color:var(--fg-2);cursor:pointer;line-height:1.4}
.rv-check input{margin-top:4px;flex-shrink:0}
.rv-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:4px}
.rv-actions .rd-btn.grad{flex:1 1 120px;justify-content:center}
.rv-done{display:flex;flex-direction:column;align-items:center;gap:8px;
  padding:24px 0;color:var(--acc);text-align:center}
.ed-warn{font-size:12px;color:#f7a745;background:rgba(255,138,76,.1);
  border:1px solid rgba(255,138,76,.28);border-radius:10px;padding:8px 8px;line-height:1.4}
.rd-how{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
@media(max-width:760px){.rd-how{grid-template-columns:1fr}}
.rd-step{display:flex;gap:12px;align-items:flex-start;padding:12px 16px;border-radius:14px;
  background:rgba(255,255,255,.025);border:1px solid var(--hair)}
.rd-step .sn{flex-shrink:0;width:22px;height:22px;border-radius:7px;display:grid;place-items:center;
  background:var(--grad-soft);color:var(--acc);font-size:12px;font-weight:800}
.rd-step .st{font-size:12px;font-weight:700;display:flex;align-items:center;gap:4px;margin-bottom:4px}
.rd-step .sb{font-size:12px;color:var(--fg-3);line-height:1.5}
.rd-picks{display:flex;gap:8px;flex-wrap:wrap}
.rd-pick{display:inline-flex;align-items:center;gap:8px;max-width:220px;padding:8px 12px;
  border-radius:99px;background:rgba(255,255,255,.05);border:1px solid var(--hair);
  color:var(--fg-2);font-size:12px;font-weight:600;cursor:pointer;transition:var(--dur-fast)}
.rd-pick:hover{background:var(--grad-soft);border-color:rgba(196,137,228,.4);color:#fff}
.rd-pick span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rd-uprow{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--hair)}
.rd-uprow:last-child{border-bottom:none}
.rd-uprow .pb{flex:1;height:6px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
.rd-uprow .pb i{display:block;height:100%;background:var(--grad);border-radius:99px;transition:transform var(--dur-slow) var(--ease)}
.rd-preset-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.rd-preset{border-radius:14px;padding:16px;border:1px solid var(--hair);background:rgba(255,255,255,.02)}
.rd-preset .pn{font-weight:700;font-size:14px;text-transform:capitalize;display:flex;align-items:center;justify-content:space-between}
.rd-preset .pn .badge2{font-size:12px;font-weight:700;color:var(--acc);background:var(--grad-soft);padding:4px 8px;border-radius:99px}
.rd-preset .pr{display:flex;justify-content:space-between;font-size:12px;color:var(--fg-2);margin-top:8px}
.rd-preset .pr b{color:var(--fg);font-weight:700;font-variant-numeric:tabular-nums}
.rd-field{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px 0;border-bottom:1px solid var(--hair)}
.rd-field:last-child{border-bottom:none;padding-bottom:0}
.rd-field:first-of-type{padding-top:0}
.rd-field .fl{font-size:12px;font-weight:500}
.rd-field .fd{font-size:12px;color:var(--fg-3);margin-top:4px}
/* NO backdrop-filter here, deliberately. This element covers the whole
   viewport, so a blur on it makes the browser re-blur everything behind it on
   every frame ANYTHING behind changes — and the nav logo (rdLogoGlow), the
   live dots (ping) and any spinner animate forever. Stack a decoding video on
   top of that and playback stutters. It only looked fine in fullscreen because
   the fullscreen element renders in the top layer, where none of this applies,
   which is exactly the shape of the bug that was reported.
   Measured on the real CSS: 50ms median frame with the blur, 16.7ms without.
   The dim is carried by a more opaque background instead. */
.rd-modal-bg{position:fixed;inset:0;background:rgba(5,4,8,.88);
  z-index:60;display:grid;place-items:center;padding:32px}
.rd-modal{width:min(900px,100%);max-height:90vh;border-radius:22px;overflow:hidden;display:flex;flex-direction:column;
  box-shadow:var(--shadow-card);background:rgba(16,14,22,.9);border:1px solid var(--hair-2)}
.rd-modal-media{position:relative;width:100%;padding-bottom:46%;flex-shrink:0}
.rd-modal-media .thumb{position:absolute;inset:0}
.rd-modal-media .thumb::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 50%,rgba(0,0,0,.6))}

/* ── While a clip player is open, the page stops maintaining blur layers ─────
   The rule above stripped the blur from the four things that sit ON or OVER
   the player, and that was only half of it: the page BEHIND a player is still
   being composited, and every backdrop-filter still on it is still being
   maintained, frame after frame, while the video decodes. Fullscreen is smooth
   because the browser stops painting the page underneath entirely — which is
   why the same clip judders windowed and does not in fullscreen, the second
   time that symptom has been reported.

   The modal scrim is 88% opaque and the editor scrim 86%, so none of this blur
   is meaningfully VISIBLE while a player is up. Dropping it costs nothing to
   look at and gives the frame budget back. It is scoped to
   `body.hz-player` — present only while a player is on screen — so ordinary
   browsing keeps the glass exactly as it was.

   MEASURED on the real page, canvas repainting every frame in the real modal,
   two passes each:
       nothing changed   45.6 fps   p95 33.4ms   29% of frames dropped
       nav blur off      58.3 fps   p95 16.8ms    2%
       header blur off   59.1 fps   p95 16.8ms    1%
       stat cards off    59.6 fps   p95 16.8ms    0%
       all of them off   59.6 fps   p95 16.8ms    0%
   Note what that says: removing ANY of them is most of the win, which is why
   picking a single culprit would have been the wrong reading. The page cannot
   afford to keep several blur layers alive next to a decoding video, so while
   one is playing it keeps none.

   ONE ENTRY PER BLURRING SELECTOR IN THIS STYLESHEET, and a test asserts that
   set equality rather than checking a hand-picked list. The last fix listed
   four selectors by hand and the test froze that list, so the five it had not
   thought of were never covered and the bug came back on every other screen.
   Add a backdrop-filter anywhere and that test fails until you have decided
   whether it may stay alive next to a decoding video. */
body.hz-player .glass,          /* every panel: cards, stat tiles, .tw-box    */
body.hz-player .rd-header,
body.hz-player .rd-nav,
body.hz-player .rd-navscrim,
body.hz-player .rd-toast,
body.hz-player .rd-undo,
/* One per card, so this is 40 blur layers on a full queue and 200 on a Pro
   one. At 40 clips the measurement above already reached 0% dropped without
   touching them, so they are not what was breaking playback — they are here
   because a page that has stopped keeping blur layers should not keep forty of
   them, and behind an 88%-opaque scrim not one is visible. */
body.hz-player .rd-play .ring,
body.hz-player .rd-tw .tw-play,
/* .ed-bg WRAPS the editor/import player rather than sitting behind it, which is
   the worse case: an ancestor blur re-rasterises the subtree it contains. */
body.hz-player .ed-bg{-webkit-backdrop-filter:none;backdrop-filter:none}
.rd-modal-close{position:absolute;top:14px;right:14px;width:36px;height:36px;border-radius:50%;border:none;
  /* Sits ON the player, so a blur here re-blurs that patch of video every
     frame it decodes. Opaque background instead — same look, no per-frame work. */
  background:rgba(10,8,14,.86);color:#fff;display:grid;place-items:center;z-index:2}
.rd-modal-close:hover{background:rgba(10,8,14,.85)}
.rd-modal-play{position:absolute;inset:0;display:grid;place-items:center}
.rd-modal-play .ring{width:76px;height:76px;border-radius:50%;display:grid;place-items:center;padding-left:4px;background:var(--grad);color:#fff;box-shadow:var(--glow)}
.rd-modal-body{padding:16px 24px;overflow-y:auto}
.rd-modal-head{display:flex;align-items:center;gap:12px}
.rd-modal-head .av{width:40px;height:40px;border-radius:12px;background:var(--grad);display:grid;place-items:center;font-weight:800;color:#1a0322}
.rd-modal-head h3{font-size:17px;font-weight:700;letter-spacing:-.02em}
.rd-modal-head .mt{font-size:12px;color:var(--fg-2);margin-top:4px}
.rd-modal-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:16px}
.rd-sigbar{margin-bottom:12px}
.rd-sigbar .sh{display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px}
.rd-sigbar .sh .sk{color:var(--fg-2)}
.rd-sigbar .sh .sv{font-weight:700;font-variant-numeric:tabular-nums}
.rd-sigbar .st{height:7px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
.rd-sigbar .sf{height:100%;border-radius:99px;background:var(--grad)}
.rd-modal-actions{display:flex;gap:8px;margin-top:8px}
.rd-modal-actions .rd-btn{flex:1}
.rd-meta-row{display:flex;justify-content:space-between;font-size:12px;padding:8px 0;border-bottom:1px solid var(--hair)}
.rd-meta-row:last-child{border-bottom:none}
.rd-meta-row .mk{color:var(--fg-2)}
.rd-meta-row .mv{font-weight:600}
@media(max-width:900px){
  .rd-body{grid-template-columns:1fr;grid-template-rows:auto 1fr}
  .rd-col{max-height:300px}
  /* Stacked, so it is now TALLER than the window rather than two columns that
     each scroll on their own. Without a scroller of its own the bottom of this
     screen was simply unreachable between 701px and 900px wide — the band
     between the desktop layout and the page-scroll mobile mode, which nothing
     had ever been checked at. Measured before the frame fix and after it: the
     last control sat 7802px down a 900px window either way, so this is its own
     bug rather than a consequence of that one. */
  .rd-streams-layout{grid-template-columns:1fr;overflow-y:auto}
  .rd-metrics{grid-template-columns:repeat(2,1fr)}
  .rd-modal-grid{grid-template-columns:1fr}
}
@media(max-width:700px){
  /* Scrollable instead of fixed-height */
  body{overflow:auto}
  .rd-app{grid-template-columns:1fr;height:auto;min-height:100dvh;
    grid-template-rows:auto}
  .rd-frame{min-height:0;overflow:visible}
  /* No bottom bar to clear anymore — content runs to the bottom of the screen. */
  .rd-screen{overflow:visible;padding-bottom:16px}
  .rd-navscrim{display:block}

  /* Vertical nav → slide-out drawer. The old bottom tab bar cost 58px of
     every screen and squeezed 9 tabs into it; the drawer gives the content
     the full viewport and each destination a full-width row. */
  .rd-nav{position:fixed;top:0;bottom:0;left:0;z-index:60;width:268px;max-width:82vw;
    flex-direction:column;align-items:stretch;gap:4px;padding:16px 12px calc(18px + env(safe-area-inset-bottom));
    border-right:1px solid var(--hair);border-top:none;overflow-y:auto;
    background:#0c0c12;
    transform:translateX(-102%);transition:transform var(--dur-slow) var(--ease);
    box-shadow:0 0 40px rgba(0,0,0,.6)}
  .rd-nav.open{transform:translateX(0)}
  .rd-nav .logo{display:flex;justify-content:center;margin-bottom:12px}
  .rd-nav .sp{flex:1;display:block;min-height:10px}
  .rd-navitem{width:auto;height:auto;min-height:48px;flex-direction:row;justify-content:flex-start;
    align-items:center;gap:12px;padding:0 12px;border-radius:12px;font-size:14px;font-weight:600;text-align:left}
  /* Badge is first in DOM (absolute on desktop); in the row layout it belongs
     at the end — order:3 keeps it there instead of shoving the icon/label right. */
  .rd-navitem .navbadge{position:static;order:3;margin-left:auto}
  /* Scrim: tap anywhere off the drawer to dismiss. */
  .rd-navscrim{position:fixed;inset:0;z-index:59;background:rgba(0,0,0,.55);
    opacity:0;pointer-events:none;transition:opacity var(--dur-slow) ease;-webkit-backdrop-filter:blur(2px);backdrop-filter:blur(2px)}
  .rd-navscrim.open{opacity:1;pointer-events:auto}
  .rd-menubtn{display:inline-flex;align-items:center;justify-content:center;width:38px;height:38px;
    flex-shrink:0;border-radius:11px;background:rgba(255,255,255,.06);border:1px solid var(--hair);
    color:var(--fg);cursor:pointer}

  /* Page-scroll mode: the frame stops constraining height at all, so the
     screen must not try to fill it. */
  .rd-frame > .rd-screen{flex:0 0 auto}

  /* Header */
  .rd-header{padding:0 12px;gap:8px;height:56px}
  .rd-menubtn{display:inline-flex}
  .rd-header .htitle{font-size:14px}
  .rd-header .hsub{display:none}
  .rd-header .rd-live{display:none}

  /* Review screen */
  .rd-body{grid-template-columns:1fr;padding:12px;gap:12px;overflow:visible}
  .rd-col{max-height:none}
  .rd-main{gap:12px;overflow:visible}
  .rd-toolbar{flex-wrap:wrap;gap:8px}
  .rd-filters{margin-left:0;width:100%;justify-content:space-between}
  .rd-filter{flex:1;text-align:center;padding:8px 4px}
  .rd-grid{grid-template-columns:1fr;padding-right:0;overflow:visible}
  .rd-clip{height:auto}

  /* Streams screen */
  .rd-streams-layout{grid-template-columns:1fr;padding:12px;gap:12px;overflow:visible}
  .rd-chanlist{overflow-y:visible;max-height:none}
  .rd-detail{overflow-y:visible}
  .rd-metrics{grid-template-columns:repeat(2,1fr);gap:8px}
  .rd-weight .wl{width:90px;font-size:12px}

  /* Settings */
  .rd-scroll{padding:12px}
  .rd-settings{gap:12px}
  .rd-preset-grid{grid-template-columns:1fr;gap:8px}
  .rd-card{padding:16px}

  /* Modal: full-screen sheet */
  .rd-modal-bg{padding:0;align-items:flex-end}
  .rd-modal{width:100%;max-height:92dvh;border-radius:22px 22px 0 0;overflow:hidden}
  .rd-modal-media{padding-bottom:56.25%}
  .rd-modal-body{flex:1;min-height:0;overflow-y:auto;padding:12px 16px}
  .rd-modal-grid{grid-template-columns:1fr;gap:16px}
  .rd-modal-actions{flex-wrap:wrap}

  /* Header: hide username text, just show avatar on narrow screens */
  .rd-user-chip .uc-name{display:none}
  .rd-user-chip{padding:4px;gap:0}

  /* Toast: no bottom bar to sit above now */
  .rd-toast{bottom:20px;font-size:12px;padding:8px 16px;max-width:90vw;text-align:center}
}
/* ═══ Aurora v2 — pure-CSS visual layer. Appended last so it wins at equal
   specificity; NO markup/logic depends on it. Theme-aware: every accent is
   derived from var(--acc)/var(--acc-2) via color-mix, so the (gated) kick
   theme keeps working. Wrapped fallbacks degrade to the original look. ═══ */
.rd-app::before{content:'';position:fixed;inset:0;z-index:-2;
  background:
    radial-gradient(1050px 560px at 15% -10%,rgba(184,106,220,.26),transparent 62%),
    radial-gradient(860px 500px at 94% 2%,rgba(249,67,255,.16),transparent 58%),
    radial-gradient(940px 720px at 55% 116%,rgba(124,107,255,.16),transparent 62%),
    var(--rd-bg)}
.rd-app::after{content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;
  background-image:radial-gradient(rgba(255,255,255,.045) 1px,transparent 1px);
  background-size:26px 26px;
  -webkit-mask-image:radial-gradient(1000px 640px at 50% 0%,#000 20%,transparent 78%);
  mask-image:radial-gradient(1000px 640px at 50% 0%,#000 20%,transparent 78%)}
@supports (background:linear-gradient(#000,#000) padding-box) and (color:color-mix(in srgb,#000 50%,#fff)){
  .glass{border-color:transparent;
    background:linear-gradient(var(--panel),var(--panel)) padding-box,
      linear-gradient(165deg,color-mix(in srgb,var(--acc-2) 34%,transparent),
        rgba(255,255,255,.075) 30%,rgba(255,255,255,.06) 66%,
        color-mix(in srgb,var(--acc) 26%,transparent)) border-box;
    box-shadow:0 18px 44px -22px rgba(0,0,0,.6)}
  .rd-modal{border-color:transparent;
    background:linear-gradient(rgba(16,14,22,.94),rgba(16,14,22,.94)) padding-box,
      linear-gradient(165deg,color-mix(in srgb,var(--acc-2) 45%,transparent),
        rgba(255,255,255,.1) 34%,rgba(255,255,255,.08) 64%,
        color-mix(in srgb,var(--acc) 34%,transparent)) border-box}
  .rd-toast{border-color:transparent;
    background:linear-gradient(rgba(18,14,24,.88),rgba(18,14,24,.88)) padding-box,
      linear-gradient(120deg,color-mix(in srgb,var(--acc-2) 55%,transparent),
        rgba(255,255,255,.14),color-mix(in srgb,var(--acc) 45%,transparent)) border-box}
  .rd-navitem.active::before{border-color:transparent;
    background:linear-gradient(135deg,color-mix(in srgb,var(--acc-2) 16%,transparent),
        color-mix(in srgb,var(--acc) 11%,transparent)) padding-box,
      linear-gradient(150deg,color-mix(in srgb,var(--acc) 55%,transparent),
        rgba(255,255,255,.1) 45%,color-mix(in srgb,var(--acc-2) 40%,transparent)) border-box}
}
.rd-btn.grad{position:relative;overflow:hidden}
.rd-btn.grad::after{content:'';position:absolute;top:0;left:-80%;width:50%;height:100%;
  background:linear-gradient(100deg,transparent,rgba(255,255,255,.34),transparent);
  transform:skewX(-20deg);transition:transform var(--dur-slow) var(--ease)}
.rd-btn.grad:hover::after{left:135%}
.rd-clip:hover{box-shadow:0 26px 54px -20px rgba(0,0,0,.72),
  0 0 44px -16px color-mix(in srgb,var(--acc-2) 55%,transparent)}
.rd-chan.active,.rd-stream:hover{box-shadow:0 0 30px -14px color-mix(in srgb,var(--acc-2) 45%,transparent)}
.rd-header{background:linear-gradient(180deg,rgba(12,11,17,.72),rgba(10,10,14,.5))}
.rd-nav{background:linear-gradient(180deg,rgba(13,12,19,.66),rgba(10,10,14,.44))}
.rd-navitem.active .ic{filter:drop-shadow(0 0 9px color-mix(in srgb,var(--acc) 75%,transparent))}
::-webkit-scrollbar-thumb{background:color-mix(in srgb,var(--acc-2) 26%,rgba(255,255,255,.08));
  border-radius:99px;border:2px solid transparent;background-clip:padding-box}
::-webkit-scrollbar-thumb:hover{background:color-mix(in srgb,var(--acc-2) 44%,rgba(255,255,255,.1));background-clip:padding-box}
@media(prefers-reduced-motion:no-preference){
  .rd-nav .logo img{animation:rdLogoGlow 4.5s ease-in-out infinite alternate}
  @keyframes rdLogoGlow{from{filter:drop-shadow(0 0 9px rgba(196,137,228,.4))}
    to{filter:drop-shadow(0 0 17px rgba(196,137,228,.75))}}
  .rd-screen{animation:rdScreenIn var(--dur-slow) var(--ease)}
  @keyframes rdScreenIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
  /* REMOVED: a staggered fade-up on the first six clip cards, 400ms with
     delays out to 250ms. Decoration removed for this phase, and the reason is
     what the screen is FOR: Clip Review is a work surface where you approve
     and reject in a run, and the grid re-renders on every decision. The
     stagger therefore did not play once on arrival — it replayed on every
     click, so the cards nearest the one you just acted on slid up again while
     you were reaching for the next. An entrance that repeats is not an
     entrance, it is a flinch.

     .rd-screen keeps its entrance: a screen is entered once per navigation,
     which is the case the animation was written for. */
}
@media(max-width:700px){
  /* Cull panel: fixed bottom sheet above nav bar so it can't overflow the right edge */
  .cull-panel{position:fixed;bottom:66px;left:10px;right:10px;width:auto;top:auto;z-index:50}

  /* ── Mobile usability pass ──
     Root cause of the old jank: several rows had an unshrinkable min-content
     width (~439px) — header title+switch+live+avatar on one nowrap line, stat
     tiles with nowrap labels, the toolbar — so the whole app laid out wider
     than the phone and taps landed off-target. Kill each constraint. */
  html,body{overflow-x:hidden}
  /* Collapse the desktop sidebar track (the nav is a fixed bottom bar here) and
     break min-content propagation: 1fr means minmax(auto,1fr), and 'auto' lets
     any deep unwrappable row push the whole app wider than the phone. */
  .rd-app{grid-template-columns:minmax(0,1fr)}
  .rd-body{grid-template-columns:minmax(0,1fr)}
  .rd-streams-layout{grid-template-columns:minmax(0,1fr)}
  .rd-frame,.rd-screen,.rd-main,.rd-col,.rd-rail{min-width:0}
  .rd-header{flex-wrap:wrap;height:auto;min-height:0;padding:8px 12px;gap:8px 8px}
  .rd-header>*{min-width:0}
  .rd-header .htitle{font-size:16px}
  .rd-header .hsub{display:none}
  .plat-sw-btn{padding:8px 12px;font-size:12px}
  .rd-live{font-size:12px;padding:4px 8px}
  .rd-toolbar{flex-wrap:wrap;gap:8px}
  .rd-addrow{flex-wrap:wrap}
  .rd-addrow .rd-input{flex:1 1 100%}
  .rd-addrow .rd-suggwrap{flex:1 1 100%}
  .rd-addrow .rd-select{flex:1}
  .rd-grid{grid-template-columns:1fr}
  /* The bottom nav is gone (drawer now), so scrolling content keeps only a
     small breathing gap instead of reserving a whole tab bar's height. */
  .rd-body,.rd-scroll,.rd-streams-layout{padding-bottom:16px}
  .rd-detail,.rd-chanlist{padding-bottom:16px}
  /* Opaque drawer: the aurora layer above gives .rd-nav a translucent
     gradient, which would let page content read through a panel that now
     floats OVER the content instead of sitting beside it. */
  .rd-nav{background:#0c0c12}
  /* First-run welcome card: phone-comfortable padding */
  .wm-card{padding:24px 16px !important;border-radius:18px !important}
  /* Toolbars: the two filter groups (status + sort) must wrap, not push wide */
  .rd-toolbar>div{flex-wrap:wrap;min-width:0}
  .rd-filters{width:auto;max-width:100%;flex-wrap:wrap}
  .rd-filter{flex:1 1 auto}
  .rd-section-title{flex-wrap:wrap}
  /* Clip cards: a long channel name must truncate, not shove the status pill out */
  .rd-clip-head{min-width:0}
  .rd-clip-ch{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  /* Stream detail header: long channel names wrap; Force clip stays on screen */
  .rd-detail-head{flex-wrap:wrap}
  .rd-detail-head>div{min-width:0}
  .rd-detail-head h2{font-size:17px;word-break:break-word}
}
</style>
</head>
<body>
<div id="root"></div>
<script src="https://unpkg.com/react@18.3.1/umd/react.production.min.js" crossorigin="anonymous" integrity="sha384-DGyLxAyjq0f9SPpVevD6IgztCFlnMF6oW/XQGmfe+IsZ8TqEiDrcHkMLKI6fiB/Z"></script>
<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js" crossorigin="anonymous" integrity="sha384-gTGxhz21lVGYNMcdJOyq01Edg0jhn/c22nsx0kyqP0TxaV5WVdsSH1fSDUf5YJj1"></script>
<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" crossorigin="anonymous" integrity="sha384-m08KidiNqLdpJqLq95G/LEi8Qvjl/xUYll3QILypMoQ65QorJ9Lvtp2RXYGBFj1y"></script>
<script src="/static/vendor/mp4-muxer.js"></script>
<script src="/static/vendor/webm-muxer.js"></script>
<script type="text/babel">
const { useState, useEffect, useRef, useCallback } = React;

// ── An expired session must SAY so ──────────────────────────────────────────
//
// This tab stays open for hours while streams run, and the session behind it
// can lapse while it does. Until this existed, that failed in silence: every
// poll was answered with the HTML sign-in page, each fetch tried to parse it
// as JSON, threw, and was swallowed by the .catch() that every call site has.
// The screen simply stopped updating, with nothing on it saying why, and the
// only way out was for the user to guess and reload.
//
// The server now answers a script with 401 instead of a page (_is_api_request
// in api.py). This is the other half: turn that into the one thing that can
// actually help, which is sending them to sign in again.
//
// WRAPPED HERE RATHER THAN AT 44 CALL SITES so a call added later cannot
// forget it, and so the Accept header goes out on every request — that header
// is what gets a 401 rather than a redirect from a browser too old to send
// Sec-Fetch-Dest, which would otherwise still be silently parsing HTML.
(function(){
  const real = window.fetch;
  let leaving = false;
  window.fetch = function(input, init){
    const url = (typeof input === 'string') ? input : (input && input.url) || '';
    // Same-origin API calls only. An absolute URL is somebody else's server and
    // none of this applies to it.
    const ours = url.charAt(0) === '/';
    if (ours) {
      init = Object.assign({}, init);
      const h = new Headers((init && init.headers) || (typeof input === 'object' && input.headers) || {});
      if (!h.has('Accept')) h.set('Accept', 'application/json');
      init.headers = h;
    }
    return real.call(this, input, init).then(function(res){
      if (ours && res.status === 401 && !leaving) {
        // Once. Several polls can land at the same moment and every one of
        // them is a 401; without the guard they fight over the location.
        leaving = true;
        window.location.href = '/login';
      }
      return res;
    });
  };
})();

// ONE name per signal, for the whole app. There were two of these tables — the
// clip modal called KEYWORD "Keyword hits" and the streams screen called the
// same signal "Keyword", so the product had two names for one thing depending
// on which screen you were standing on. It mirrors SIGNAL_LABELS in
// src/trigger/signals.py, and a test fails if the two ever disagree.
//
// Screens still choose WHICH signals they list — the modal shows the four the
// formula scores on, the streams panel shows every weight the learner has
// touched. They just no longer choose what those signals are called.
const SIGNAL_LABELS = {
  CHAT_VELOCITY:     'Chat velocity',
  KEYWORD:           'Keyword hits',
  SENTIMENT:         'Sentiment',
  AUDIO_SPIKE:       'Audio spike',
  MANUAL:            'Manual',
  VIEWER_SPIKE:      'Viewer spike',
  SILENCE_BURST:     'Silence burst',
  EMOTE_HOMOGENEITY: 'Emote wall',
};
const signalLabel = k => SIGNAL_LABELS[k] || k;

// ── Clip ordering, once, for every screen that shows clip cards ─────────────
//
// Clip Review and Clip Library render the SAME cards from the same store, and
// they had drifted: Review got a sort menu and a styled streamer picker, while
// the Library still had a bare <select> and no way to order anything at all.
// Two screens showing one set of clips, one of which could not be sorted.
//
// So the keys, the comparator and the direction wording live here and both
// screens use them. Each screen still decides WHICH sorts it offers and what it
// defaults to — the Library leads with "Date approved" because it is the record
// of what you decided to keep, Review leads with "Date added" because it is a
// queue — but neither owns a private copy of how sorting works.
const CLIP_SORTS = {
  newest:   {l:'Date added',    date:true,  k: c => c.created_at || 0},
  // approved_at only exists from the day it shipped; older clips fall back to
  // capture time, which leaves their relative order unchanged.
  approved: {l:'Date approved', date:true,  k: c => c.approved_at || c.created_at || 0},
  // Every key coalesces to 0. A clip captured before a field existed has no
  // value for it and must sort to the bottom rather than making the comparator
  // return NaN — which sorts nothing at all, silently.
  trigger:  {l:'Trigger score', date:false, k: c => c.trigger_score || 0},
  virality: {l:'Virality',      date:false, k: c => c.virality_score || 0},
  length:   {l:'Clip length',   date:false, dir:['Longest first','Shortest first'],
             k: c => c.duration_seconds || 0},
  // TEXT, not a number. The comparator subtracts, and subtracting two strings
  // is NaN — which sorts nothing and looks like the control is broken rather
  // than like a bug. `text` is what routes it to localeCompare.
  channel:  {l:'Streamer name', date:false, text:true,
             k: c => (c.channel || '').toLowerCase()},
  // 'AUDIENCE SIGNAL', NOT 'CLIPPERS'. This is clipper_count, and the card and
  // the detail panel have deliberately called it Audience signal (Detected /
  // Strong / Very strong) since it shipped — because the raw number names
  // OTHER PEOPLE'S actions, and a reviewer who has never read an API doc has
  // no idea what one of them is. Putting the raw word in this menu walked back
  // into exactly that, and told the reader something about where the moment
  // came from that the rest of the UI had settled on not saying. The menu uses
  // the words already printed on the card.
  //
  // ONLY EXISTS ON A HIGHLIGHT. An ordinary clip carries no audience signal —
  // not a zero, NONE — so `only` sinks the clips the measure cannot describe
  // rather than ranking them worst on it. Same reasoning the grouping below
  // applies to trigger_score, and the same mistake the old "0% trigger" badge
  // made.
  //
  // A VIEW-COUNT SORT WAS OFFERED HERE AND IS GONE. Nothing on the card or in
  // the detail panel shows that number, so it ordered by something the reader
  // cannot see — and naming it after Twitch said the clip already existed
  // there before they kept it.
  audience: {l:'Audience signal', date:false, only:true,
             // The detail panel grades this Detected / Strong / Very strong,
             // so the direction says strongest, not "high".
             dir:['Strongest first','Weakest first'],
             k: c => c.clipper_count || 0},
};

// WHAT a clip is, as a filter. Asked for directly: highlights became their own
// kind of thing and there was no way to look at just them, or just the ones the
// formula caught.
// Named for the badge on the card, which is the only thing the reader has to
// go on. A clip is either marked "Highlight" or it is not; "Detected only" was
// our word for the other half and appears nowhere they can see it.
const CLIP_KINDS = [
  {v:'all',       l:'All clips'},
  {v:'highlight', l:'Highlights only'},
  {v:'detected',  l:'Everything else'},
];

// HOW the queue is ordered, and the reason this control exists at all.
//
// The queue lifts highlights above everything else on every sort. That is the
// right default — they are the ones worth looking at first — but it is applied
// BEFORE the sort key, so it silently overrides it: asking for date order and
// getting every highlight first is not date order. There was no way to see the
// queue in one true sequence. "Strict order" turns the grouping off and sorts
// by exactly what was asked for, nothing else.
// "Strict order" describes the comparator, not what the reader gets. What they
// get is a list running straight down in whatever they sorted by, with the
// highlights left wherever they fall.
const CLIP_GROUPS = [
  {v:'highlights', l:'Highlights first'},
  {v:'strict',     l:'Straight down the list'},
  // Two lists, each with its own sort. 'Highlights first' groups them but ONE
  // key still governs both, so there was no way to order highlights by how
  // strong the signal was while ordering the rest by score. This splits the
  // queue in two and gives each half its own control.
  {v:'split',      l:'Sorted separately'},
];

// Both screens narrow the same way, so neither owns a private copy of it.
function filterClips(list, chan, kind) {
  return list.filter(c =>
    (chan === 'all' || c.channel === chan) &&
    (kind === 'all' || (kind === 'highlight') === !!c.suggested));
}

// `queueMode` means "this screen is the review queue, so group it" — Review
// passes true, the Library false. It was called `pendingFirst` when status was
// the only grouping; crowd suggestions added a second one, and a name promising
// exactly one of them would have been the misleading half of the truth.
function sortClips(list, sortBy, sortDir, group) {
  const s = CLIP_SORTS[sortBy] || CLIP_SORTS.newest;
  // `group` replaced a queueMode boolean. The Library passed false and Review
  // passed true, which left no way to express the third state the user asked
  // for: this IS the queue, and I still want one unbroken order.
  const queueMode = group === 'highlights';
  return [...list].sort((a,b)=>{
    // Pending first, but ONLY on a date sort, and only where the caller asked
    // for it. That grouping is what makes Review a queue rather than a gallery;
    // applying it to an explicit score sort would defeat the request, because
    // asking for the highest trigger score and getting a wall of already-
    // approved clips above a 95 is not sorting by trigger score.
    if(queueMode && s.date){
      const sp={pending:0,approved:1,rejected:2};
      if(sp[a.status]!==sp[b.status]) return sp[a.status]-sp[b.status];
    }
    // SUGGESTIONS LEAD THE QUEUE ON EVERY SORT, not only the date ones.
    //
    // They used to be confined to date sorts, on the reasoning that a
    // suggestion carries trigger_score 0 and pinning it above a 95 would be
    // answering a different question than the one asked. That reasoning had
    // the wrong premise: a suggestion is UNSCORED, not scored zero. Sorting it
    // to the bottom of "highest trigger score" states, falsely, that the
    // detector looked at it and rated it worst — the same mistake the "0%
    // trigger" badge made on the card, and it buried the clips a human framed
    // underneath every mediocre one the formula produced.
    //
    // So they lead, and the rest of the list still sorts exactly as asked.
    // Still INSIDE the status band above: an already-approved suggestion must
    // never outrank a clip still waiting on a decision, which is the one thing
    // the queue ordering exists to prevent.
    if(queueMode){
      const sg = c => c.suggested ? 0 : 1;
      if(sg(a)!==sg(b)) return sg(a)-sg(b);
    }
    // A measure that does not apply to this clip sinks it, in BOTH directions:
    // "fewest clippers first" must not answer with a wall of clips that were
    // never crowd-clipped at all.
    if(s.only){
      const ap = c => c.suggested ? 0 : 1;
      if(ap(a)!==ap(b)) return ap(a)-ap(b);
    }
    const d = s.text ? String(s.k(a)).localeCompare(String(s.k(b)))
                     : s.k(a) - s.k(b);
    if(d) return sortDir === 'asc' ? d : -d;
    // Ties are common — virality is banded and a quiet stream produces runs of
    // identical trigger scores. Newest inside a tie keeps the order stable.
    return (b.created_at||0) - (a.created_at||0);
  });
}

// The direction control says what it will DO, in the words that fit the field.
// "Ascending" on a date column is a small riddle; "Oldest first" is not.
function dirLabelFor(sortBy, sortDir) {
  const s = CLIP_SORTS[sortBy] || CLIP_SORTS.newest;
  // An explicit pair wins. "High to low" is true of every number and tells the
  // reader nothing about THIS one: a length sorted high-to-low is longest
  // first, and the words they can act on are the ones that say so.
  if(s.dir) return sortDir === 'desc' ? s.dir[0] : s.dir[1];
  if(s.text) return sortDir === 'desc' ? 'Z to A' : 'A to Z';
  return s.date ? (sortDir === 'desc' ? 'Newest first' : 'Oldest first')
                : (sortDir === 'desc' ? 'High to low'  : 'Low to high');
}

const Icon = ({ name, size=16, stroke=2, fill='none', style }) => {
  const P = {
    check: <polyline points="20 6 9 17 4 12"/>,
    x: <><path d="M18 6 6 18"/><path d="m6 6 12 12"/></>,
    menu: <><path d="M3 6h18"/><path d="M3 12h18"/><path d="M3 18h18"/></>,
    play: <polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none"/>,
    plus: <><path d="M5 12h14"/><path d="M12 5v14"/></>,
    zap: <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>,
    radio: <><path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/><path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/></>,
    film: <><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18"/><path d="M17 3v18"/><path d="M3 7.5h4"/><path d="M3 12h18"/><path d="M3 16.5h4"/><path d="M17 7.5h4"/><path d="M17 16.5h4"/></>,
    logout: <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/></>,
    search: <><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></>,
    bell: <><path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/></>,
    sparkles: <><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/></>,
    trending: <><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></>,
    grid: <><rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/></>,
    cog: <><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></>,
    download: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></>,
    upload: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/></>,
    sliders: <><line x1="4" x2="4" y1="21" y2="14"/><line x1="4" x2="4" y1="10" y2="3"/><line x1="12" x2="12" y1="21" y2="12"/><line x1="12" x2="12" y1="8" y2="3"/><line x1="20" x2="20" y1="21" y2="16"/><line x1="20" x2="20" y1="12" y2="3"/><line x1="2" x2="6" y1="14" y2="14"/><line x1="10" x2="14" y1="8" y2="8"/><line x1="18" x2="22" y1="16" y2="16"/></>,
    database: <><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/></>,
    user: <><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></>,
    card: <><rect width="20" height="14" x="2" y="5" rx="2"/><line x1="2" x2="22" y1="10" y2="10"/></>,
    trash: <><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></>,
    chat: <><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></>,
    video: <><path d="m22 8-6 4 6 4V8z"/><rect width="14" height="12" x="2" y="6" rx="2" ry="2"/></>,
    clock: <><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></>,
    link: <><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></>,
    book: <><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></>,
    chevron: <polyline points="6 9 12 15 18 9"/>,
    arrowdown: <><path d="M12 5v14"/><polyline points="19 12 12 19 5 12"/></>,
    arrowup: <><path d="M12 19V5"/><polyline points="5 12 12 5 19 12"/></>,
    alert: <><path d="M10.3 3.6 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/></>,
  };
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill}
      stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round"
      style={{ display:'block', flexShrink:0, ...style }}>
      {P[name]}
    </svg>
  );
};

const scoreColor = s => s >= 75 ? 'var(--live)' : s >= 50 ? 'var(--pending)' : 'var(--acc)';
const scoreFill  = s => s >= 75 ? 'var(--live)' : s >= 50 ? 'var(--pending)' : 'var(--grad)';
const initials   = s => (s||'').slice(0,2).toUpperCase();
const fmtTime    = ts => ts ? new Date(ts*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}) : '';
const fmtDur     = s  => s  ? Math.round(s)+'s' : '';
const thumbFor   = ch => { let h=0; for(const c of (ch||'')) h=(h*31+c.charCodeAt(0))%360; return `linear-gradient(135deg,hsl(${h} 55% 22%),hsl(${(h+42)%360} 60% 11%))`; };
// Twitch hands us a small ~480px preview; request a 1280x720 variant for crisp
// cards. If that size 404s, the <img> onError falls back to the original URL.
const RE_PREVIEW = new RegExp('-preview-[0-9]+x[0-9]+[.]');
const hiResThumb = url => (url||'').replace(RE_PREVIEW, '-preview-1280x720.');
// Strips a filename extension. Same reason as RE_PREVIEW: written as a literal
// it needs a backslash, and a backslash in this file is Python's, not JS's.
const RE_EXT = new RegExp('[.][^.]+$');

// Thumbnail load failed. Freshly-created Twitch clips 404 until Twitch finishes
// generating the preview frame (and the 1280x720 upscale may never exist), so we
// step down: hi-res -> original -> gradient placeholder. We track progress on a
// data attribute because e.target.src returns the *resolved* absolute URL, which
// can differ from the stored URL by encoding and break a naive string compare.
function thumbFallback(e, channel) {
  const img = e.target;
  const orig = img.getAttribute('data-orig') || '';
  if (orig && img.getAttribute('data-tried') !== '1') {
    img.setAttribute('data-tried', '1');
    img.src = orig;
    return;
  }
  img.style.display = 'none';
  if (img.parentElement) img.parentElement.style.background = thumbFor(channel);
}

// Catmull-Rom spline → cubic beziers: a smooth curve that still passes through
// every point (honest data, just rounded instead of zig-zagged).
function rdSmoothPath(pts){
  if(pts.length<3){
    return pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ');
  }
  const t=1/6;   // tension — standard Catmull-Rom factor, low overshoot
  let dStr='M'+pts[0][0].toFixed(1)+' '+pts[0][1].toFixed(1);
  for(let i=0;i<pts.length-1;i++){
    const p0=pts[i-1]||pts[i], p1=pts[i], p2=pts[i+1], p3=pts[i+2]||p2;
    const c1x=p1[0]+(p2[0]-p0[0])*t, c1y=p1[1]+(p2[1]-p0[1])*t;
    const c2x=p2[0]-(p3[0]-p1[0])*t, c2y=p2[1]-(p3[1]-p1[1])*t;
    dStr+=' C'+c1x.toFixed(1)+' '+c1y.toFixed(1)+' '+c2x.toFixed(1)+' '+c2y.toFixed(1)
         +' '+p2[0].toFixed(1)+' '+p2[1].toFixed(1);
  }
  return dStr;
}

function RdScoreChart({ data }) {
  const w=600, h=150, pad=6;
  let d = data && data.length>1 ? data : [0,0];
  // Light 3-point moving average to soften single-sample noise before plotting.
  if(d.length>2){
    d = d.map((v,i)=>{
      const a=(i>0?d[i-1]:v), c=(i<d.length-1?d[i+1]:v);
      return (a+v+c)/3;
    });
  }
  const clamp=v=>Math.max(0,Math.min(100,v));
  const pts = d.map((v,i)=>[pad+(i/(d.length-1))*(w-2*pad), h-pad-(clamp(v)/100)*(h-2*pad)]);
  const line = rdSmoothPath(pts);
  const area = line+` L ${w-pad} ${h} L ${pad} ${h} Z`;
  const last = pts[pts.length-1];
  return (
    <svg className="rd-chart" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stopColor="rgba(184,106,220,.5)"/><stop offset="1" stopColor="rgba(184,106,220,0)"/>
      </linearGradient></defs>
      {[25,50,75].map(y=><line key={y} x1="0" x2={w} y1={h-(y/100)*(h-2*pad)-pad} y2={h-(y/100)*(h-2*pad)-pad} stroke="rgba(255,255,255,.05)" strokeWidth="1"/>)}
      <path d={area} fill="url(#cg)"/>
      <path d={line} fill="none" stroke="#c489e4" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke"/>
      <circle cx={last[0]} cy={last[1]} r="3.5" fill="#fff"/>
    </svg>
  );
}

function RdStream({ s, scoreData, profile, onRemove, onForce }) {
  const rawScore = scoreData ? (scoreData.score||0) : 0;
  const breakdown = scoreData ? (scoreData.breakdown||{}) : {};

  // Engine heartbeat: a local 1s tick measures the gap since the last
  // score_update. A live worker emits ~1/s, so a >10s gap on a live stream
  // means the engine is stalled — without this, a dead worker just silently
  // freezes the card and looks identical to a calm chat.
  const [nowTick, setNowTick] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const staleSecs = scoreData && scoreData.at ? Math.floor((nowTick - scoreData.at) / 1000) : null;

  // Smooth the bar via rAF easing — avoids jarring jumps on large WS updates
  const [displayScore, setDisplayScore] = useState(rawScore);
  const curRef = useRef(rawScore);
  const tgtRef = useRef(rawScore);
  const rafRef = useRef(null);
  useEffect(() => {
    tgtRef.current = rawScore;
    const step = () => {
      const d = tgtRef.current - curRef.current;
      if (Math.abs(d) < 0.05) { curRef.current = tgtRef.current; setDisplayScore(tgtRef.current); return; }
      curRef.current += d * 0.1;
      setDisplayScore(curRef.current);
      rafRef.current = requestAnimationFrame(step);
    };
    cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(rafRef.current);
  }, [rawScore]);
  const score = displayScore;
  const p = profile || {};
  const samples = p.velocity_samples||0;
  // 'queued' means the channel IS live and every slot on the box is taken.
  // Amber like reconnecting, because both are "not running yet, shortly" —
  // and labelled, because the bare word means nothing to a viewer.
  const statusColor = s.status==='live' ? 'var(--live)' : (s.status==='reconnecting'||s.status==='queued') ? 'var(--pending)' : 'var(--fg-2)';
  const statusLabel = s.status==='queued' ? 'waiting for a slot' : s.status;
  const platColor = s.platform==='kick' ? '#53fc18' : 'var(--acc)';
  return (
    <div className="rd-stream">
      <div className="rd-stream-top">
        <div>
          <div className="nm"><span className="plat" style={{background:platColor,boxShadow:`0 0 8px ${platColor}`}}/>{s.channel}</div>
          <div className="mt">
            <span className="rd-chip" style={s.platform==='kick'?{background:'rgba(83,252,24,.12)',color:'#53fc18',border:'1px solid rgba(83,252,24,.3)'}:{}}>{s.platform}</span>
            <span className="rd-chip">{s.preset}</span>
            <span style={{color:statusColor,fontWeight:600}}
                  title={s.status==='queued'?'This channel is live. Every monitoring slot on the server is busy, so it starts as soon as one frees up.':''}>{statusLabel}</span>
          </div>
        </div>
        <div className="rd-stream-actions">
          <button className="rd-btn ghost-force sm" onClick={()=>onForce(s.channel)}><Icon name="zap" size={12}/>Clip</button>
          <button className="rd-x" onClick={()=>onRemove(s.channel)} title="Remove"><Icon name="x" size={14}/></button>
        </div>
      </div>
      <div className="rd-score">
        <div className="rd-score-top">
          <span className="lbl">Trigger score</span>
          <span className="val" style={{color:scoreColor(score)}}>{score.toFixed(1)}</span>
        </div>
        <div className="rd-track">
          <div className="rd-fill" style={{width:score+'%',background:scoreFill(score)}}/>
          {breakdown._threshold!=null&&<div className="rd-thr" style={{left:Math.min(breakdown._threshold,99)+'%'}} title={'Fires at '+breakdown._threshold}/>}
        </div>
        <div className="rd-sigs" style={{marginTop:4}}>{(()=>{
          const live = s.status==='live';
          const chips = [];
          if (staleSecs===null) chips.push(<span className="rd-sig" key="hb" style={{color:'var(--fg-3)'}}>&#9679; engine — waiting for first update…</span>);
          else if (staleSecs<=10) chips.push(<span className="rd-sig" key="hb" style={{color:'var(--live)'}}>&#9679; engine live</span>);
          else if (!live) chips.push(<span className="rd-sig" key="hb" style={{color:'var(--fg-3)'}}>&#9679; engine idle — stream {s.status}</span>);
          else chips.push(<span className="rd-sig" key="hb" style={{background:'rgba(255,90,120,.14)',color:'var(--danger)'}}>&#9679; engine — no updates for {staleSecs}s</span>);
          if (breakdown._chat_vps!=null) {
            const last = breakdown._last_chat_s;
            const col = last<0 ? 'var(--danger)' : last<30 ? 'var(--live)' : last<120 ? 'var(--pending)' : 'var(--danger)';
            const fresh = last<0 ? 'no chat received yet' : 'last msg '+(last<=1?'just now':last+'s ago');
            chips.push(<span className="rd-sig" key="chat" style={{color:col}}>CHAT {breakdown._chat_vps}/s{breakdown._chat_base_vps>0?' (base '+breakdown._chat_base_vps+')':''} &middot; {fresh}</span>);
          }
          if (breakdown._threshold!=null) chips.push(<span className="rd-sig" key="thr" style={{color:'var(--fg-2)'}}>fires at {breakdown._threshold}</span>);
          return chips;
        })()}</div>
        <div className="rd-sigs">{Object.entries(breakdown).filter(([k])=>!k.startsWith('_')).map(([k,v])=>{
          const active=typeof v==='number'&&v>0.05;
          return <span className="rd-sig" key={k} style={active?{background:'rgba(184,106,220,.18)',color:'var(--fg-1)'}:{}}>{k}: {typeof v==='number'?v.toFixed(2):v}</span>;
        })}</div>
        <div className="rd-sigs" style={{marginTop:4}}>{[
          breakdown._audio_db!=null&&<span className="rd-sig" key="adb" style={{color:breakdown._audio_db>-50?'var(--live)':'var(--fg-3)'}}>AUDIO {breakdown._audio_db}dB peak {breakdown._audio_peak_db}dB (base {breakdown._audio_base_db}dB)</span>,
          breakdown._viewers!=null&&<span className="rd-sig" key="vc" style={{color:'var(--fg-2)'}}>VIEWERS {breakdown._viewers} (base {breakdown._viewer_base})</span>,
        ].filter(Boolean)}</div>
      </div>
      <div className="rd-profile">
        <div className="rd-pgrid">
          <div className="rd-pcell"><div className="k">Threshold</div><div className="v">{p.trigger_threshold?p.trigger_threshold.toFixed(0):'—'}</div></div>
          <div className="rd-pcell"><div className="k">Velocity</div><div className="v">{p.avg_velocity>0?p.avg_velocity.toFixed(1):'—'}<span style={{fontSize:12,color:'var(--fg-3)',fontWeight:500}}> m/s</span></div></div>
          <div className="rd-pcell"><div className="k">Clips</div><div className="v">{p.total_clips||0}</div></div>
          <div className="rd-pcell"><div className="k">Approval</div>
            <div className="v" style={{color:!p.total_clips?'var(--fg)':p.approval_rate>=0.7?'var(--live)':p.approval_rate>=0.4?'var(--pending)':'var(--danger)'}}>
              {p.total_clips>0?Math.round(p.approval_rate*100)+'%':'—'}
            </div>
          </div>
        </div>
        <div className="rd-learn" style={{color:samples>=10?'var(--live)':'var(--acc)'}}>
          {samples>=10
            ? <><Icon name="check" size={12}/>Calibrated · {samples} samples</>
            : <><span>Learning {samples}/10</span><span className="rd-learnbar"><div style={{width:Math.min(100,samples*10)+'%'}}/></span></>}
        </div>
      </div>
    </div>
  );
}

/* THE AUDIENCE BADGE'S WORDING. One label said "Audience spike" on every card,
   which is accurate and completely flat down a queue of twenty. These say the
   same thing in the voice a streamer would use.

   TWO TIERS, because the word has to stay TRUE. clipper_count is the number of
   distinct viewers who clipped that moment, and the badge already only renders
   above 1 — but "Huge clip" on a moment two people caught is a claim the data
   does not support. Five or more gets the loud half; below that, and the VOD
   scanner (which carries no count at all, only viewer_clipped), gets the calm
   half. The tooltip states the measurement precisely either way.

   DETERMINISTIC, not random. Math.random() here would deal a new word on every
   re-render — and this grid re-renders on every websocket message, so badges
   would visibly reshuffle while you read them. Hashing the clip's own id means
   a given clip keeps its word for as long as it exists, and the queue still
   reads varied because ids differ. */
const SPIKE_CALM = ['Trending','Chat noticed','Crowd pick','Getting clipped','Worth a look'];
const SPIKE_LOUD = ['Blowing up','Huge clip','Everyone clipped this','Big moment'];
function spikeLabel(clip){
  const pool = (clip.clipper_count||0) >= 5 ? SPIKE_LOUD : SPIKE_CALM;
  const key = String(clip.id || clip.slug || '');
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
  return pool[Math.abs(h) % pool.length];
}

function RdClip({ clip, onApprove, onReject, onDelete, onOpen, onEdit, libraryMode }) {
  const score = Math.round(clip.score||clip.trigger_score||0);  // VOD clips carry 'score'; both are 0-100
  const dur = fmtDur(clip.duration_seconds);
  const time = fmtTime(clip.created_at);
  const title = clip.clip_title||clip.stream_title||'Live Stream';
  const thumb = clip.thumbnail_url || '';
  const twHref = clip.twitch_url || '';
  // A Kick clip has no hosted clip page; it links to the channel instead.
  const outHref = twHref || clip.platform_url || '';
  const outName = clip.platform === 'kick' ? 'Kick' : 'Twitch';
  // A moment viewers clipped, surfaced with the score deliberately never
  // consulted (src/trigger/suggested_clips.py). It is a different KIND of card,
  // not a decorated one, so it gets its own badge and suppresses the trigger
  // badge entirely — see below.
  const sug = !!clip.suggested;
  // Present on EVERY plan. The editor and scheduler are Pro, but a clip the
  // product caught for you is yours to keep — see get_clip_file. Absent until
  // the capture finishes, which is a few seconds after the card appears, and
  // the clip_file_ready event is what makes it turn up without a refresh.
  // LABELLED, not a bare glyph. It used to be a 13px download icon with no
  // word next to it, in a row of buttons that all had words — findable only if
  // you already knew it was there, which is not the same as being offered.
  //
  // `file_state` is the server's answer to "why can this not be downloaded",
  // and 'pending' is the common case people read as broken: the cut runs after
  // the moment's tail has been broadcast and buffered, so the card exists for
  // ~20s before its file does. Saying so beats showing nothing and letting
  // them conclude the feature does not work. The clip_file_ready event swaps
  // this for the real button with no refresh.
  const fileState = clip.file_state || (clip.has_file ? 'ready' : 'missed');
  const dlBtn = fileState === 'ready' ? (
    <a href={'/clips/'+clip.id+'/file?download=1'} download className="rd-btn sm"
       title="Download this clip as an MP4" onClick={e=>e.stopPropagation()}
       style={{textDecoration:'none'}}><Icon name="download" size={13}/>Download</a>
  ) : fileState === 'pending' ? (
    <span className="rd-btn sm rd-dl-wait" title="Highlightz is cutting the video for this clip"
       style={{cursor:'default'}}><Icon name="download" size={13}/>Preparing</span>
  ) : fileState === 'fetching' ? (
    <span className="rd-btn sm rd-dl-wait" title="Getting the video from Twitch"
       style={{cursor:'default'}}><Icon name="download" size={13}/>Fetching</span>
  ) : clip.fetchable ? (
    // No file, but one can be had: the same Download button, and pressing it
    // fetches the clip from Twitch. Sent up to App over the in-page event
    // channel rather than threaded through five render sites as a prop — App
    // owns the clip state, the request and the download that follows.
    <button className="rd-btn sm" title="Download this clip as an MP4 (fetched from Twitch)"
       onClick={e=>{e.stopPropagation();window.dispatchEvent(new CustomEvent('hz_fetch_clip',{detail:{id:clip.id,download:true}}))}}>
      <Icon name="download" size={13}/>Download</button>
  ) : null;
  // Straight into the editor, no download-then-reupload. `onEdit` is only
  // passed when the Editor is actually reachable for this account, so the
  // button cannot appear next to a tab the user does not have — the endpoint
  // enforces the same thing, this just stops offering a dead end.
  //
  // Offered for a FETCHABLE clip too: the editor route fetches the file from
  // Twitch inline when there is none, so Edit stays one click either way.
  const edBtn = ((clip.has_file || clip.fetchable) && onEdit) ? (
    <button className="rd-btn sm" title="Edit this clip" style={{flex:'0 0 auto'}}
       onClick={e=>{e.stopPropagation();onEdit(clip)}}><Icon name="sliders" size={13}/></button>
  ) : null;
  return (
    <div className={'rd-clip'+(sug?' suggested':'')}>
      <div className="rd-media" style={{cursor:'pointer'}} onClick={()=>onOpen&&onOpen(clip)}>
        {thumb
          ? <img src={hiResThumb(thumb)} data-orig={hiResThumb(thumb)!==thumb?thumb:''} alt="" onError={e=>thumbFallback(e, clip.channel)} style={{position:'absolute',inset:0,width:'100%',height:'100%',objectFit:'cover'}}/>
          : <div className="rd-thumb" style={{background:thumbFor(clip.channel)}}/>}
        <div className="rd-play"><span className="ring"><Icon name="play" size={20}/></span></div>
        {/* BOTH badges say what they are. Every card carried two bare
            percentages — "43% viral" top-left and a naked "47%" top-right —
            the same shape, the same size, one of them unlabelled, and nothing
            anywhere saying which number was which. The Sort menu offers
            "Trigger score" and "Virality" and the cards gave you no way to tell
            which badge you had just ordered them by.

            Labelled rather than reduced to one: they measure different things
            (what the detector MEASURED vs how shareable it looks) and the two
            disagreeing is the interesting case — a 95 trigger at 20% viral is
            worth seeing as both numbers, not as whichever one you sorted by. */}
        {/* THE TRIGGER BADGE IS SUPPRESSED ON A SUGGESTION, and that is a
            correctness fix rather than a style choice. A suggested clip carries
            trigger_score 0 because no score was consulted to surface it, so the
            unconditional badge rendered "0% trigger" — which does not read as
            "not scored", it reads as "the detector looked at this and rated it
            worthless". On the one card type whose entire purpose is to carry
            moments the detector MISSED, that is the exact opposite of the
            truth, and it sat next to an Approve button. The virality badge
            needs no such guard: it is already conditional on > 0. */}
        {sug
          ? <span className="rd-sugbadge" title="Highlightz flagged this from a spike in audience interest, outside the usual scoring.">
              <Icon name="trending" size={12}/>Highlight
            </span>
          : <span className="rd-scorebadge" title="Trigger score — what the detector measured at that moment">
              <span className="pip" style={{background:scoreColor(score)}}/>{score}% trigger</span>}
        {!sug && clip.virality_score>0 && <span className={'rd-viralbadge'+(clip.virality_score>=65?' hot':clip.virality_score>=35?' warm':'')} title="Virality — how shareable this moment looks">
          <Icon name="trending" size={12}/>{Math.round(clip.virality_score)}% viral
        </span>}
        {/* ONE VOCABULARY FOR BOTH DETECTORS. A crowd suggestion and a VOD
            moment that the audience also reacted to are the same finding —
            Highlightz measured a spike of interest at this timestamp — so they
            say the same thing. They used to read "2 viewers clipped it" and
            "412 clipped it", which credited the audience with the find and
            made the product look like it had outsourced the work. The strength
            of the signal is what a reviewer can act on; who supplied it is not,
            and it is still on the record either way. */}
        {sug && clip.clipper_count>1 && <span className="rd-clippedbadge" style={{top:10}}
          title="Highlightz measured unusually high audience interest at this moment">
          <Icon name="trending" size={11}/>{spikeLabel(clip)}
        </span>}
        {clip.viewer_clipped && <span className="rd-clippedbadge"
          title="Highlightz measured unusually high audience interest at this moment">
          <Icon name="trending" size={11}/>{spikeLabel(clip)}
        </span>}
        {/* Visible BEFORE the card is opened, because that is when it changes
            what you do: a gated clip cannot be reviewed inline, and on a plan
            with a weekly keep limit it is worth knowing before you spend a
            slot on something you have to leave the site to watch. */}
        {clip.autopilot && clip.autopilot.status && <span className={'rd-apbadge ' + clip.autopilot.status}
          title={clip.autopilot.status === 'failed' ? ('Autopilot: ' + (clip.autopilot.error || 'failed'))
               : clip.autopilot.status === 'scheduled' ? 'Autopilot: on the calendar' + (clip.autopilot.due_at ? ' for ' + qWhen(clip.autopilot.due_at) : '')
               : clip.autopilot.status === 'rendering' ? 'Autopilot: rendering now' : 'Autopilot: waiting for the video file'}>
          <Icon name="zap" size={11}/>{{scheduled:'Autopilot', rendering:'Rendering…', failed:'Autopilot failed', waiting_file:'Autopilot · waiting'}[clip.autopilot.status] || 'Autopilot'}
        </span>}
        {clip.age_restricted && <span className="rd-agebadge"
          title={clip.platform === 'kick'
            ? 'This channel is flagged mature on Kick. The clip is a file Highlightz captured and plays here.'
            : 'This channel is flagged mature on Twitch. The clip plays on Twitch, not in this player.'}>
          <Icon name="zap" size={11}/>Age-restricted
        </span>}
        {dur && <span className="rd-dur">{dur}</span>}
      </div>
      <div className="rd-clip-body">
        <div className="rd-clip-head">
          <span className="rd-clip-ch"><span className="av">{initials(clip.channel)}</span>{clip.channel}</span>
          <span className={'rd-status '+clip.status}>{clip.status}</span>
        </div>
        <div className="rd-clip-title">{title}</div>
        <div className="rd-clip-meta">
          {time && <span className="rd-tag">{time}</span>}
          {clip.game && <span className="rd-tag">{clip.game}</span>}
        </div>
        <div className="rd-clip-actions">
          {clip.status==='pending' ? <>
            {/* Guarded: the library no longer passes these, and an unguarded
                call on a clip that slipped through would white-screen the app. */}
            <button className="rd-btn live sm" onClick={e=>{e.stopPropagation();onApprove&&onApprove(clip.id)}}><Icon name="check" size={14}/>Approve</button>
            <button className="rd-btn danger sm" onClick={e=>{e.stopPropagation();onReject&&onReject(clip.id)}}><Icon name="x" size={14}/>Reject</button>
            {outHref && <a href={outHref} target="_blank" rel="noopener" className="rd-btn sm" style={{textDecoration:'none',flex:'0 0 auto'}} title={'Open on '+outName} onClick={e=>e.stopPropagation()}><Icon name="play" size={13}/></a>}
            {dlBtn}
            {edBtn}
          </> : libraryMode && clip.status==='approved' ? <>
            {outHref && <a href={outHref} target="_blank" rel="noopener" className="rd-btn grad sm" style={{textDecoration:'none'}} onClick={e=>e.stopPropagation()}><Icon name="play" size={13}/>Open on {outName}</a>}
            {dlBtn}
            {edBtn}
            {onDelete && <button className="rd-btn sm" style={{flex:'0 0 auto',background:'rgba(255,90,120,.1)',color:'var(--danger)',borderColor:'rgba(255,90,120,.2)'}} title="Remove from library" onClick={e=>{e.stopPropagation();onDelete(clip.id)}}><Icon name="trash" size={13}/></button>}
          </> : <span className="rd-resolved">
            <Icon name={clip.status==='approved'?'check':'x'} size={14} style={{color:clip.status==='approved'?'var(--live)':'var(--danger)'}}/>
            {clip.status==='approved'?'Approved':'Rejected'}
            {outHref && <a href={outHref} target="_blank" rel="noopener" className="rd-btn sm" style={{marginLeft:4,textDecoration:'none',flex:'0 0 auto'}} title={'Open on '+outName} onClick={e=>e.stopPropagation()}><Icon name="play" size={13}/></a>}
            {dlBtn}
            {edBtn}
          </span>}
        </div>
      </div>
    </div>
  );
}

function UndoToast({ entry, onUndo, onDismiss }) {
  // Counts down so the offer visibly has a deadline, rather than lingering as a
  // button whose behaviour silently changes when the server-side window lapses.
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [entry && entry.id]);
  if (!entry) return null;
  const left = Math.max(0, Math.round((entry.expires_at * 1000 - now) / 1000));
  if (left <= 0) return null;
  const mins = Math.floor(left / 60);
  return (
    <div className="rd-undo">
      <span className="ico"><Icon name="trash" size={13}/></span>
      <span className="msg">{entry.label}</span>
      <button className="act" onClick={onUndo}>Undo</button>
      <span className="left">{mins > 0 ? mins + 'm' : left + 's'}</span>
      <button className="x" onClick={onDismiss} aria-label="Dismiss">×</button>
    </div>
  );
}

function RdToast({ msg }) {
  return <div className={'rd-toast'+(msg?' show':'')}><span className="ico"><Icon name="sparkles" size={13}/></span>{msg}</div>;
}

function Spinner() {
  return <span style={{display:'inline-block',width:13,height:13,border:'2px solid rgba(255,255,255,.3)',borderTopColor:'#fff',borderRadius:'50%',animation:'spin 0.7s linear infinite',marginRight:4,verticalAlign:'middle'}}/>;
}

function fmtSecs(s) {
  if (!s && s!==0) return '';
  const m = Math.floor(s/60), sec = Math.floor(s%60);
  return `${m}:${String(sec).padStart(2,'0')}`;
}

function parseSecs(str) {
  str = (str||'').trim();
  if (str.includes(':')) {
    const [m,s] = str.split(':');
    return parseInt(m||0,10)*60 + parseFloat(s||0);
  }
  return parseFloat(str)||0;
}

// Marks the page as "a clip player is on screen" for as long as `open` is true.
// The stylesheet uses body.hz-player to drop every backdrop-filter the page is
// still maintaining behind the player — see the long note beside that rule for
// the measurements. Clips judder windowed and are smooth in fullscreen because
// fullscreen stops the page underneath being painted at all; this is how the
// windowed case gets the same frame budget.
//
// REF-COUNTED, not a boolean. Two players can be up at once (the review modal
// over the library, the import lightbox over the editor), and a plain
// add/remove would have the first one to close strip the class while the other
// is still playing — the bug would come back for exactly the case where two
// things are on screen and the machine is busiest.
let _hzPlayers = 0;
function usePlayerOpen(open) {
  useEffect(()=>{
    if(!open) return;
    _hzPlayers += 1;
    document.body.classList.add('hz-player');
    return ()=>{
      _hzPlayers = Math.max(0, _hzPlayers - 1);
      if(_hzPlayers === 0) document.body.classList.remove('hz-player');
    };
  }, [open]);
}

// An announcement from the operator. In FRONT of everything — above the clip
// modal and the editor — because the whole point is that it cannot be
// missed. One at a time, oldest first when several are waiting; dismissing
// one reveals the next. Plain text with line breaks kept; React escapes it,
// so nothing the operator types can become markup.
function AnnouncementModal({ a, onSeen }) {
  useEffect(() => {
    const k = e => { if (e.key === 'Escape') onSeen(a.id); };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [a.id]);
  return (
    <div className="rd-ann-bg" role="dialog" aria-modal="true" aria-labelledby="rd-ann-title">
      <div className="rd-ann glass">
        <div className="rd-ann-k"><Icon name="bell" size={13}/>Announcement</div>
        <h3 id="rd-ann-title">{a.title}</h3>
        <div className="rd-ann-body">{a.body}</div>
        <button className="rd-btn grad" onClick={()=>onSeen(a.id)} autoFocus>Got it</button>
      </div>
    </div>
  );
}

function ClipModal({ clip, onClose, onApprove, onReject, onEdit, isAdmin, featured, onFeature }) {
  // Retry counter for the Twitch iframe. Declared BEFORE the null-clip early
  // return: hooks must run on every render or React errors when the modal
  // opens (same trap documented on the VOD plan gate).
  const [playerTry, setPlayerTry] = useState(0);
  useEffect(()=>{ setPlayerTry(0); },[clip&&clip.id]);
  // Above the early return for the same reason playerTry is: this component is
  // always mounted and renders null when there is no clip, so a hook below the
  // return would run on some renders and not others.
  usePlayerOpen(!!clip);
  if (!clip) return null;
  const score = Math.round(clip.score||clip.trigger_score||0);  // VOD clips carry 'score'; both are 0-100
  const dur = fmtDur(clip.duration_seconds);
  const time = fmtTime(clip.created_at);
  const title = clip.clip_title||clip.stream_title||'Live Stream';
  const embed = clip.embed_url || '';
  const twHref = clip.twitch_url || '';
  const thumb = clip.thumbnail_url || '';
  // Inline playback everywhere, phones included. (The old ≤700px thumbnail
  // fallback dated from Twitch's autoplay-related #4000 mobile error; with
  // autoplay=false + tap-to-play the embed works on modern mobile browsers,
  // and the "Player not loading? Watch on Twitch" link below stays as the
  // escape hatch for any device that still refuses.)
  // AGE-RESTRICTED CLIPS GET NO IFRAME AT ALL.
  //
  // Twitch gates mature content behind an age confirmation, and inside a
  // third-party frame it cannot make one: the viewer's Twitch session is a
  // third-party cookie, which browsers block, so the player has no way to know
  // who is watching and refuses. The clip is fine — it plays on Twitch, where
  // that session is first-party. What we were rendering was a black box with a
  // play button that could never work.
  //
  // So do not render it. Send them where it plays, and say why in one line.
  const gated = !!clip.age_restricted;
  const embedSrc = (embed && !gated)
    ? embed + (embed.indexOf('?')>=0?'&':'?') + 'parent=' + location.hostname + '&autoplay=false'
    : '';
  // With no inline embed (no embed_url stored) the clip can only play on
  // Twitch — make the whole media area a tap target so it opens even when the
  // thumbnail image is broken (the old absolutely-positioned play link was an
  // unreliable hit target on mobile once the broken <img> collapsed).
  // Which now includes every gated clip, so the whole media area opens Twitch.
  // A Kick clip has no embed and no clip page: it is the file Highlightz cut
  // from live capture, played right here, and it links out to the channel.
  const fileSrc = (!embedSrc && clip.has_file) ? '/clips/' + clip.id + '/file' : '';
  const outHref = twHref || clip.platform_url || '';
  const outName = clip.platform === 'kick' ? 'Kick' : 'Twitch';
  const canLinkOut = !embedSrc && !fileSrc && !!outHref;
  const openClip = () => { if (outHref) window.open(outHref, '_blank', 'noopener'); };
  const sigMap = {};
  for (const s of (clip.trigger_signals||[])) {
    const k = (s.type||'').replace('SignalType.','');
    sigMap[k] = (s.value||0)*100;
  }
  const sigKeys = ['CHAT_VELOCITY','KEYWORD','SENTIMENT','AUDIO_SPIKE'];
  const sug = !!clip.suggested;

  return (
    <div className="rd-modal-bg" onClick={onClose}>
      <div className="rd-modal" onClick={e=>e.stopPropagation()}>
        <div className="rd-modal-media" style={canLinkOut?{cursor:'pointer'}:undefined} onClick={canLinkOut?openClip:undefined}>
          {embedSrc
            ? <iframe key={playerTry} src={embedSrc+'&_r='+playerTry} allow="autoplay; fullscreen; encrypted-media; picture-in-picture" allowFullScreen frameBorder="0" scrolling="no" style={{position:'absolute',inset:0,width:'100%',height:'100%',background:'#000'}}/>
            : fileSrc
              ? <video src={fileSrc} controls playsInline preload="metadata" style={{position:'absolute',inset:0,width:'100%',height:'100%',background:'#000',objectFit:'contain'}}/>
            : thumb
              ? <><img src={hiResThumb(thumb)} data-orig={hiResThumb(thumb)!==thumb?thumb:''} alt="" onError={e=>thumbFallback(e, clip.channel)} style={{position:'absolute',inset:0,width:'100%',height:'100%',objectFit:'cover'}}/>{outHref&&<div className="rd-modal-play"><span className="ring"><Icon name="play" size={26}/></span></div>}</>
              : <><div className="thumb" style={{background:thumbFor(clip.channel)}}/><div className="rd-modal-play"><span className="ring"><Icon name="play" size={26}/></span></div></>}
          <button className="rd-modal-close" onClick={e=>{e.stopPropagation();onClose();}}><Icon name="x" size={16}/></button>
          {sug
            ? <span className="rd-sugbadge" style={{top:14,right:60}}><Icon name="trending" size={12}/>Highlight</span>
            : <span className="rd-scorebadge" style={{top:14,right:60}}><span className="pip" style={{background:scoreColor(score)}}/>{score}% trigger</span>}
        </div>

        {/* Only when the flag actually stops playback: a Twitch clip with no
            local file. A Kick clip is a file Highlightz cut and plays right
            here, mature flag or not — "plays on Twitch" under a Kick clip
            was a real bug report (2026-09-15). */}
        {gated && !fileSrc && <div style={{display:'flex',alignItems:'center',justifyContent:'center',gap:12,padding:'8px 12px',fontSize:12,background:'rgba(250,204,21,.10)',borderBottom:'1px solid rgba(250,204,21,.22)'}}>
          <span style={{color:'var(--pending)',fontWeight:600,display:'inline-flex',alignItems:'center',gap:4}}>
            <Icon name="zap" size={13}/>Age-restricted on {outName}
          </span>
          <span style={{color:'var(--fg-3)'}}>It cannot play here, but it plays on {outName}.</span>
          {outHref && <a href={outHref} target="_blank" rel="noopener" className="rd-btn sm"
            style={{textDecoration:'none',flexShrink:0}}>Watch on {outName} ↗</a>}
        </div>}
        {embedSrc && <div style={{display:'flex',alignItems:'center',justifyContent:'center',gap:12,padding:'8px 12px',fontSize:12,background:'rgba(99,102,241,.10)',borderBottom:'1px solid rgba(255,255,255,.06)'}}>
          <span style={{color:'var(--fg-3)'}}>Player showing an error?</span>
          <button className="rd-btn sm" onClick={()=>setPlayerTry(t=>t+1)}>Reload player</button>
          {twHref && <a href={twHref} target="_blank" rel="noopener" style={{color:'var(--acc)',textDecoration:'none',fontWeight:600}}>Watch on Twitch ↗</a>}
        </div>}
        {fileSrc && clip.platform === 'kick' && <div style={{display:'flex',alignItems:'center',justifyContent:'center',gap:12,padding:'8px 12px',fontSize:12,background:'rgba(83,252,24,.08)',borderBottom:'1px solid rgba(255,255,255,.06)'}}>
          <span style={{color:'var(--fg-3)'}}>Captured by Highlightz from the live broadcast.</span>
          {outHref && <a href={outHref} target="_blank" rel="noopener" style={{color:'#53fc18',textDecoration:'none',fontWeight:600}}>Open channel on Kick ↗</a>}
        </div>}

        <div className="rd-modal-body">
          <div className="rd-modal-head">
            <span className="av">{initials(clip.channel)}</span>
            <div style={{flex:1}}><h3>{title}</h3><div className="mt">{clip.channel} · {clip.game||'stream'} · {time}</div></div>
            <span className={'rd-status '+clip.status}>{clip.status}</span>
          </div>
          <div className="rd-modal-grid">
            <div>
              {/* A suggestion did NOT fire, so it has no signals — sigKeys is
                  a fixed list of four and would have rendered four bars at 0%
                  under a heading claiming this is why the detector triggered.
                  That is a fabricated explanation of a decision nothing made.
                  The honest panel says what actually put the clip here. */}
              <div className="rd-eyebrow" style={{marginBottom:12}}>{sug?'Why it is here':'Why it fired'}</div>
              {sug
                ? <div style={{fontSize:12,lineHeight:1.65,color:'var(--fg-2)'}}>
                    {/* WHAT HIGHLIGHTZ DID, not who else was involved. This
                        panel used to name the viewer whose clip it is and end
                        on "Highlightz did not create it" — accurate about the
                        file, and it made the product sound like a middleman on
                        the one screen where it is doing its most distinctive
                        work. The detection IS ours: a second detector watching
                        audience behaviour instead of chat and audio. That is
                        what this now says. */}
                    <p style={{margin:'0 0 8px'}}>
                      <b style={{color:'var(--fg)'}}>Highlightz flagged this moment</b>
                      {clip.clipper_count>1
                        ? <> from an unusually strong spike in audience interest.</>
                        : <> from a spike in audience interest.</>}
                    </p>
                    <p style={{margin:'0 0 8px'}}>
                      {/* NOT "every signal on the left" — on a suggestion this
                          text replaces the signal bars, so there is no left to
                          point at. Caught by looking at the rendered modal. */}
                      It did not come from the usual score. Chat volume,
                      keywords and audio are proxies for whether a moment landed
                      with the people watching; this detector measures that
                      directly, so it catches moments the formula rates low.
                    </p>
                    <p style={{margin:0,color:'var(--fg-3)'}}>
                      Approving it keeps it in your library like any other clip.
                    </p>
                  </div>
                : sigKeys.map(k=>{
                    const v=sigMap[k]||0;
                    return <div className="rd-sigbar" key={k}>
                      <div className="sh"><span className="sk">{signalLabel(k)}</span><span className="sv" style={{color:scoreColor(v)}}>{v.toFixed(0)}%</span></div>
                      <div className="st"><div className="sf" style={{width:v+'%'}}/></div>
                    </div>;
                  })}
            </div>
            <div>
              <div className="rd-eyebrow" style={{marginBottom:12}}>Details</div>
              <div className="rd-meta-row"><span className="mk">Duration</span><span className="mv">{dur||'—'}</span></div>
              <div className="rd-meta-row"><span className="mk">Platform</span><span className="mv" style={{textTransform:'capitalize'}}>{clip.platform}</span></div>
              <div className="rd-meta-row"><span className="mk">Game</span><span className="mv">{clip.game||'—'}</span></div>
              <div className="rd-meta-row"><span className="mk">Captured</span><span className="mv">{time}</span></div>
              {/* The clipper count reframed as what it is to a reviewer: how
                  strong the signal was. The raw number named other people's
                  actions; the word names our measurement, and it is the part
                  that actually helps somebody decide. */}
              {sug && clip.clipper_count>0 && <div className="rd-meta-row"><span className="mk">Audience signal</span>
                <span className="mv" style={{color:'var(--pending)'}}>{clip.clipper_count>2?'Very strong':clip.clipper_count>1?'Strong':'Detected'}</span></div>}
              {clip.virality_score>0 && <div className="rd-meta-row"><span className="mk">Virality</span><span className="mv">{Math.round(clip.virality_score)}%</span></div>}
              {twHref && <a href={twHref} target="_blank" rel="noopener" className="rd-btn grad sm" style={{textDecoration:'none',marginTop:12,width:'100%',justifyContent:'center'}}><Icon name="play" size={14}/>Open on Twitch</a>}
              {/* The file Highlightz captured. Free plans included — the
                  paywall is on editing and scheduling, not on keeping a clip
                  the product caught for you.

                  ALWAYS SAYS SOMETHING. This is the screen a person opens
                  because they want the file, so "no button" is the one answer
                  it must never give: it reads as a broken feature rather than
                  as an absent file, and there is no way to tell from the
                  outside which it was. The card can stay quiet — a grid of
                  dead controls is noise — but here there is room to name the
                  reason.

                  Nothing here points at Twitch. These clips are of OTHER
                  people's channels, and Twitch's own download is a
                  broadcaster's control in their Creator Dashboard — telling a
                  clipper to go and get it there sends them somewhere the
                  button does not exist. */}
              {clip.file_state === 'pending'
                ? <div className="rd-dl-note"><b>Preparing the download…</b> Highlightz cuts
                    the video a few seconds after the moment ends. This turns into a
                    download button on its own — no need to reload.</div>
                : clip.file_state === 'fetching'
                ? <div className="rd-dl-note"><b>Getting the video from Twitch…</b> A few
                    seconds. The download starts by itself when it lands.</div>
                : clip.has_file
                ? <a href={'/clips/'+clip.id+'/file?download=1'} download
                    className="rd-btn sm" style={{textDecoration:'none',marginTop:8,width:'100%',justifyContent:'center'}}>
                  <Icon name="download" size={14}/>Download clip</a>
                : clip.fetchable
                ? <>
                    {/* No file, but one can be had. Same button, and it fetches
                        the clip from Twitch first — see the hz_fetch_clip
                        listener in App. The reasons below only apply to clips
                        that cannot be fetched at all. */}
                    <button className="rd-btn sm"
                      style={{marginTop:8,width:'100%',justifyContent:'center'}}
                      onClick={()=>window.dispatchEvent(new CustomEvent('hz_fetch_clip',{detail:{id:clip.id,download:true}}))}>
                      <Icon name="download" size={14}/>Download clip</button>
                    <div className="rd-dl-note" style={{marginTop:8}}>Highlightz fetches
                      this one from Twitch when you ask — a few seconds, then it downloads.</div>
                  </>
                : <div className="rd-dl-note">
                    {clip.file_state === 'off'
                      ? <><b>No file for this clip.</b> Highlightz was not holding video when
                          this moment was caught, so there is nothing to download. Clips caught
                          from here on come with an MP4.</>
                      : clip.file_state === 'expired'
                      ? <><b>This download has expired.</b> Video is held for a few days
                          and then cleared to make room for new clips, so grab the ones you
                          want soon after they land. The clip itself is unaffected — only
                          the downloadable file is gone.</>
                      : <><b>No file for this clip.</b> Highlightz keeps the video only for
                          moments it captured live, and the buffer did not cover this one —
                          usually a stream reconnect, or monitoring that had just started.
                          Later clips on this channel should download normally.</>}
                  </div>}
              {/* The other half of "never leave the site": the file is already
                  on our disk, so this hands it to the editor without a
                  download and a re-upload. Only rendered when the Editor is
                  reachable for this account — see the onEdit gate in App. */}
              {(clip.has_file || clip.fetchable) && onEdit && <button className="rd-btn grad sm"
                  style={{marginTop:8,width:'100%',justifyContent:'center'}}
                  onClick={()=>{onEdit(clip);onClose()}}>
                <Icon name="sliders" size={14}/>Edit clip</button>}
              {clip.status==='pending' && <div className="rd-modal-actions">
                <button className="rd-btn live sm" onClick={()=>{onApprove(clip.id);onClose()}}><Icon name="check" size={14}/>Approve</button>
                <button className="rd-btn danger sm" onClick={()=>{onReject(clip.id);onClose()}}><Icon name="x" size={14}/>Reject</button>
              </div>}
              {isAdmin && clip.status==='approved' && clip.platform==='twitch' && onFeature &&
                <button className="rd-btn sm" style={{marginTop:8,width:'100%',justifyContent:'center',
                    background:featured?'rgba(255,194,92,.14)':'rgba(184,106,220,.14)',
                    border:featured?'1px solid rgba(255,194,92,.35)':'1px solid rgba(184,106,220,.35)',
                    color:featured?'var(--pending)':'var(--acc)'}}
                  onClick={()=>onFeature(clip.id)}>
                  <Icon name="sparkles" size={13}/>{featured?'Remove from landing page':'Feature on landing page'}
                </button>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}


function CullPanel({ clips, onDone }) {
  const [thresh, setThresh] = React.useState(50);
  const [busy, setBusy]     = React.useState(false);
  // The SAME set the endpoint acts on, or the preview is a lie about what the
  // button is going to do. Pending only (approved clips are the library, not
  // the inbox) and never crowd suggestions, which carry score 0 because nothing
  // scored them — counting them here would promise to delete every one of them
  // at any threshold above zero.
  const clipsArr = Object.values(clips).filter(c => c.status === 'pending' && !c.suggested);
  const clipScore = c => parseFloat(c.score||0) || parseFloat(c.trigger_score||0);
  const keep   = clipsArr.filter(c => clipScore(c) >= thresh).length;
  const remove = clipsArr.filter(c => clipScore(c) < thresh).length;
  const run = async () => {
    if (!remove) return;
    setBusy(true);
    await fetch('/clips/bulk-cull', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({min_score: thresh})});
    setBusy(false);
    onDone();
  };
  return (
    <div className="cull-panel glass">
      <div className="cull-row">
        <span className="cull-lbl">Keep clips scoring above</span>
        <span className="cull-val" style={{color:scoreColor(thresh)}}>{thresh}</span>
      </div>
      <input type="range" min="0" max="100" value={thresh} onChange={e=>setThresh(+e.target.value)} className="cull-slider"/>
      <div className="cull-preview">
        <span style={{color:'var(--live)'}}>✓ {keep} kept</span>
        <span style={{color:'var(--danger)'}}>✕ {remove} removed</span>
      </div>
      {remove > 0
        ? <button className="rd-btn danger sm" onClick={run} disabled={busy} style={{width:'100%',justifyContent:'center'}}>
            {busy ? 'Removing…' : `Remove ${remove} clip${remove===1?'':'s'}`}
          </button>
        : <div style={{fontSize:12,color:'var(--fg-2)',textAlign:'center'}}>All clips meet this threshold</div>}
    </div>
  );
}

/* Adding a streamer lives on Live Streams, not Clip Review. Review is for
   judging clips; putting the add box there meant the two jobs shared one
   screen and neither tab said what it was for. Lifted verbatim so the
   suggestion dropdown keeps its exact focus/blur/escape behaviour. */
function AddStreamPanel({ streams, scores, profiles, activePlatform, onAdd, onRemove, onForce,
                          selected, onSelect }) {
  const [ch, setCh] = useState('');
  const [preset, setPreset] = useState('default');
  // Streamer suggestions: zero state = recently monitored + popular-now;
  // typing = partial-name search (debounced). Per platform: Helix on
  // Twitch, Kick's search + live list on Kick (same row shapes).
  const [sugg, setSugg] = useState(null);
  const [suggOpen, setSuggOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const suggT = useRef(null);
  const inputRef = useRef(null);
  const canSugg = activePlatform === 'twitch' || activePlatform === 'kick';
  const platName = activePlatform === 'kick' ? 'Kick' : 'Twitch';
  // A list fetched for one platform must not be shown under the other.
  useEffect(()=>{ setSugg(null); setSuggOpen(false); }, [activePlatform]);
  const fetchSugg = (q) => {
    fetch('/streams/suggest?platform=' + encodeURIComponent(activePlatform) + (q ? '&q=' + encodeURIComponent(q) : ''))
      .then(r => r.ok ? r.json() : null).then(d => { if(d) setSugg(d); }).catch(()=>{});
  };
  const onChInput = (v) => {
    setCh(v);
    if (!canSugg) return;
    setSuggOpen(true);
    clearTimeout(suggT.current);
    suggT.current = setTimeout(() => fetchSugg(v.trim()), v.trim() ? 250 : 0);
  };
  // Re-open the list after an add, but ONLY if the caret is still in the box.
  // Suggestions are picked on mousedown with preventDefault, so the input never
  // loses focus — and an already-focused input fires no onFocus when you click
  // it again. Closing the dropdown on pick therefore left it shut with no way
  // to reopen except clicking away and clicking back in, which is exactly what
  // adding a second channel used to require. Guarded on activeElement so the
  // "Monitor stream" button (which does move focus) doesn't pop an orphaned
  // dropdown, and so a user who clicks elsewhere during the request is left
  // alone when it finishes.
  const reopenIfFocused = () => {
    if (!canSugg) return;
    if (inputRef.current && document.activeElement === inputRef.current) {
      setSuggOpen(true);
      fetchSugg('');
    }
  };
  // AWAIT the add before refreshing: /streams/suggest filters against the
  // channels the server already has, so refetching first would re-offer the one
  // just picked and the next click would 409. `adding` swallows repeat clicks
  // while that request is in flight — the list stays open, so the same row is
  // still under the cursor.
  const addChannel = async (login) => {
    if (adding || !login) return;
    setAdding(true);
    setCh('');
    try { await onAdd(login, preset, activePlatform); }
    finally { setAdding(false); }
    reopenIfFocused();
  };
  const add = () => { if(ch.trim()){ setSuggOpen(false); addChannel(ch.trim()); } };
  const pick = (login) => addChannel(login);
  // Clearing a recent suggestion. Optimistic: the row disappears on click and
  // the server confirms after, because waiting on a round-trip to remove a
  // thing you just dismissed feels broken even when it is fast. The refetch on
  // completion is what corrects the list if the request actually failed.
  const clearRecent = async (login) => {
    setSugg(s => s ? {...s, recent:(s.recent||[]).filter(r=>r!==login)} : s);
    try { await fetch('/streams/suggest/recent/' + encodeURIComponent(login), {method:'DELETE'}); }
    finally { if(!ch.trim()) fetchSugg(''); }
  };
  const clearAllRecent = async () => {
    setSugg(s => s ? {...s, recent:[]} : s);
    try { await fetch('/streams/suggest/recent', {method:'DELETE'}); }
    finally { if(!ch.trim()) fetchSugg(''); }
  };
  // Another tab cleared the list — mirror it here. The dropdown is fetched on
  // open rather than on mount, so a stale open dropdown is the one case a
  // reconnect refetch would not reach.
  useEffect(()=>{
    const onCleared = () => { if(!ch.trim()) fetchSugg(''); };
    window.addEventListener('hz_suggestions_cleared', onCleared);
    return () => window.removeEventListener('hz_suggestions_cleared', onCleared);
  }, [ch]);
  const fmtViewers = (v) => v >= 1000 ? (v/1000).toFixed(v >= 10000 ? 0 : 1) + 'k' : '' + v;
  const streamsArr = Object.values(streams);
  return (
    <aside className="rd-col" style={{minHeight:0}}>
        {/* overflow visible + zIndex: the suggestion dropdown must escape this
            short rail (.rd-rail clips by default) and paint over the rail
            below it (both are backdrop-filter stacking contexts, so DOM order
            would otherwise put the later rail on top). */}
        <div className="rd-rail glass" style={{flex:'0 0 auto',overflow:'visible',position:'relative',zIndex:5}}>
          <div className="rd-eyebrow">Add a stream</div>
          <div className="rd-addrow">
            <div className="rd-suggwrap">
              <input className="rd-input" placeholder="search a streamer" value={ch}
                ref={inputRef}
                onChange={e=>onChInput(e.target.value)}
                onFocus={()=>{ if(canSugg){ setSuggOpen(true); fetchSugg(ch.trim()); } }}
                /* onFocus does not fire on an already-focused input, so a click
                   into the box after dismissing the list (Escape, or an add)
                   would otherwise do nothing. */
                onClick={()=>{ if(canSugg && !suggOpen){ setSuggOpen(true); fetchSugg(ch.trim()); } }}
                onBlur={()=>setTimeout(()=>setSuggOpen(false),150)}
                onKeyDown={e=>{ if(e.key==='Enter') add(); if(e.key==='Escape') setSuggOpen(false); }}/>
              {suggOpen && sugg && (
                <div className="rd-sugg">
                  {ch.trim() ? (
                    (sugg.results||[]).length
                      ? (sugg.results||[]).map(r=>(
                          <div key={r.login} className="rd-suggitem" onMouseDown={e=>{e.preventDefault();pick(r.login);}}>
                            {r.avatar ? <img src={r.avatar} alt="" style={{width:22,height:22,borderRadius:'50%',flexShrink:0}}/> : <span style={{width:22,flexShrink:0}}/>}
                            <div style={{minWidth:0,flex:1}}>
                              <div style={{display:'flex',alignItems:'center',gap:4}}>
                                <span style={{fontWeight:600,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{r.name||r.login}</span>
                                {r.is_live && <span className="rd-sugglive">LIVE</span>}
                              </div>
                              {r.game ? <div className="meta2">{r.game}</div> : null}
                            </div>
                          </div>))
                      : <div className="rd-suggempty">No channels found</div>
                  ) : (
                    <>
                      {(sugg.recent||[]).length > 0 && <>
                        <div className="rd-sugglabelrow">
                          <div className="rd-sugglabel">Recently monitored</div>
                          {/* onMouseDown, not onClick: the input's onBlur closes
                              this dropdown on a 150ms timer, and preventDefault
                              here is what stops the click from taking focus and
                              starting that close before the handler runs. */}
                          <button type="button" className="rd-suggclear" title="Clear all recently monitored"
                            onMouseDown={e=>{e.preventDefault();e.stopPropagation();clearAllRecent();}}>Clear</button>
                        </div>
                        {(sugg.recent||[]).map(c=>(
                          <div key={'r'+c} className="rd-suggitem" onMouseDown={e=>{e.preventDefault();pick(c);}}>
                            <Icon name="clock" size={13}/><span style={{fontWeight:600}}>{c}</span>
                            {/* stopPropagation is load-bearing: without it the
                                dismiss click bubbles to the row and ADDS the
                                stream it was meant to remove. */}
                            <button type="button" className="rd-suggx" aria-label={'Remove ' + c + ' from recently monitored'}
                              onMouseDown={e=>{e.preventDefault();e.stopPropagation();clearRecent(c);}}>
                              <Icon name="x" size={12}/>
                            </button>
                          </div>))}
                      </>}
                      {(sugg.popular||[]).length > 0 && <>
                        <div className="rd-sugglabel">Popular right now</div>
                        {(sugg.popular||[]).map(p=>(
                          <div key={'p'+p.login} className="rd-suggitem" onMouseDown={e=>{e.preventDefault();pick(p.login);}}>
                            <div style={{minWidth:0,flex:1}}>
                              <div style={{display:'flex',alignItems:'center',gap:4}}>
                                <span style={{fontWeight:600,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{p.name||p.login}</span>
                                <span className="rd-sugglive">LIVE</span>
                              </div>
                              <div className="meta2">{fmtViewers(p.viewers)} watching · {p.game||''}</div>
                            </div>
                          </div>))}
                      </>}
                      {!(sugg.recent||[]).length && !(sugg.popular||[]).length &&
                        <div className="rd-suggempty">Type a channel name to search {platName}</div>}
                    </>
                  )}
                </div>
              )}
            </div>
            <select className="rd-select" value={preset} onChange={e=>setPreset(e.target.value)}>
              <option value="default">Default</option>
              <option value="small">Small streamer</option>
              <option value="fps">FPS</option>
              <option value="moba">MOBA</option>
              <option value="chess">Chess / Strategy</option>
              <option value="casino">Casino / Gambling</option>
              <option value="irl">IRL / Outdoor</option>
              <option value="variety">Variety / Just Chatting</option>
              <option value="sports">Sports</option>
            </select>
          </div>
          <div style={{display:'flex',alignItems:'center',gap:4,marginTop:4,padding:'4px 8px',borderRadius:8,background:'rgba(255,255,255,.04)',border:'1px solid var(--hair)'}}>
            <span style={{width:7,height:7,borderRadius:'50%',background:'var(--acc)',boxShadow:'0 0 6px var(--acc)',flexShrink:0}}/>
            <span style={{fontSize:12,fontWeight:600,color:'var(--fg-2)',textTransform:'capitalize'}}>{activePlatform}</span>
          </div>
          <button className="rd-btn grad" onClick={add} style={{marginTop:8}}><Icon name="plus" size={15}/>Monitor stream</button>
        </div>
        <div className="rd-rail glass" style={{flex:1,minHeight:0}}>
          <div className="rd-rail-head">
            <span className="rd-eyebrow">Monitored streams</span>
            <span className="rd-count">{streamsArr.length}</span>
          </div>
          <div className="rd-streams">
            {streamsArr.length===0
              ? <div className="rd-empty"><span className="ic"><Icon name="radio" size={26}/></span>No streams yet.<br/>Add one to start monitoring.</div>
              : streamsArr.map(s=>(
                  <div key={s.channel} onClick={()=>onSelect&&onSelect(s.channel)}
                    className={onSelect?'rd-streampick'+(s.channel===selected?' on':''):''}>
                    <RdStream s={s} scoreData={scores[s.channel]} profile={profiles[s.channel]}
                      onRemove={onRemove} onForce={onForce}/>
                  </div>))}
          </div>
        </div>
    </aside>
  );
}

function ClearQueueButton({ pending }) {
  // Two-step, not window.confirm: a native dialog is unstyleable, blocks the
  // whole tab, and reads as a browser warning rather than part of the app.
  // Arming inline also lets the count sit in the confirm text, which is the
  // one number that decides whether someone actually wants to do this.
  const [armed, setArmed] = useState(false);
  const [busy, setBusy]   = useState(false);
  useEffect(() => {
    if (!armed) return;
    // Disarm on its own so a half-pressed destructive button never sits
    // waiting to be hit by a stray click minutes later.
    const t = setTimeout(()=>setArmed(false), 6000);
    return () => clearTimeout(t);
  }, [armed]);

  const run = async () => {
    setBusy(true);
    try {
      await fetch('/clips/clear-pending', {method:'POST'});
      // No local state surgery: the server broadcasts clip_removed per clip and
      // the existing handler drops each one, so every open tab converges the
      // same way. Mutating here as well would race that.
    } catch (e) { /* the socket resync on reconnect is the backstop */ }
    setBusy(false); setArmed(false);
  };

  if (!armed) {
    return (
      <button className="rd-btn sm" onClick={()=>setArmed(true)}
        title="Empty the review queue without rejecting anything"
        style={{background:'rgba(255,255,255,.06)',border:'1px solid var(--hair)',color:'var(--fg-2)'}}>
        <Icon name="trash" size={13}/>Clear queue
      </button>
    );
  }
  return (
    <span style={{display:'inline-flex',gap:4,alignItems:'center'}}>
      <span style={{fontSize:12,color:'var(--fg-2)',fontWeight:600}}>
        Clear {pending} clip{pending===1?'':'s'}?
      </span>
      <button className="rd-btn sm" disabled={busy} onClick={run}
        style={{background:'rgba(255,90,120,.16)',border:'1px solid rgba(255,90,120,.5)',color:'var(--danger)'}}>
        {busy ? 'Clearing…' : 'Yes, clear'}
      </button>
      <button className="rd-btn sm" disabled={busy} onClick={()=>setArmed(false)}
        style={{background:'rgba(255,255,255,.06)',border:'1px solid var(--hair)',color:'var(--fg-2)'}}>
        Cancel
      </button>
    </span>
  );
}

// A dropdown that can actually be styled. A native <select> cannot: the popup
// is drawn by the operating system, so its background, font and highlight
// ignore every rule on this page and it lands as a grey system menu in the
// middle of a dark app. This is the same control the streamer filter and the
// sort field both use, so they match.
//
// Closes on outside click and on Escape, and only binds those listeners while
// it is open — a dozen of these each holding a permanent document listener is
// how a long-lived tab starts feeling slow.
function RdMenu({ label, value, options, onChange, icon, align }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(()=>{
    if(!open) return;
    const onDoc = e => { if(ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = e => { if(e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  // Falls back to the first option rather than rendering blank: the selected
  // streamer can disappear mid-session when their last clip is culled.
  const cur = options.find(o => o.v === value) || options[0] || {l:''};
  return (
    <div className="rd-menu" ref={ref}>
      <button className={'rd-menu-btn' + (open ? ' open' : '')}
        onClick={()=>setOpen(v=>!v)} aria-haspopup="listbox" aria-expanded={open}>
        {icon && <Icon name={icon} size={13}/>}
        {label && <span className="rd-menu-lbl">{label}</span>}
        <span className="rd-menu-val">{cur.l}</span>
        <span className={'rd-menu-caret' + (open ? ' open' : '')}>
          <Icon name="chevron" size={13}/>
        </span>
      </button>
      {open && <div className={'rd-menu-pop' + (align === 'right' ? ' right' : '')} role="listbox">
        {options.map(o =>
          <button key={o.v} role="option" aria-selected={o.v === value}
            className={'rd-menu-item' + (o.v === value ? ' on' : '')}
            onClick={()=>{ onChange(o.v); setOpen(false); }}>
            <span className="rd-menu-item-l">{o.l}</span>
            {o.sub && <span className="rd-menu-item-s">{o.sub}</span>}
            {o.v === value && <span className="rd-menu-tick"><Icon name="check" size={13}/></span>}
          </button>)}
      </div>}
    </div>
  );
}

// The controls row both clip screens use: streamer filter, sort field, and the
// direction toggle welded to it. `children` is whatever that screen puts first
// — Review passes its status chips, the Library has none.
//
// Shared for the same reason the comparator is: these two screens show one set
// of cards, and every time only one of them was updated the product grew a
// second way of doing the same thing.
// One half of a split queue: a heading, its own sort, its own grid.
//
// Renders NOTHING when it holds nothing. With a streamer filter on, one of the
// two halves is routinely empty, and a heading over no clips reads as a bug —
// the count on the toolbar already says how much is on screen.
function ClipSection({ title, clips, sorts, sortBy, setSortBy, sortDir, setSortDir,
                       onApprove, onReject, onOpen, onEdit }) {
  if(!clips.length) return null;
  return (
    /* No class on the wrapper: it groups, it does not style, and the gap
       between sections comes from .rd-sects. A class name with no rule behind
       it reads as meaningful and is not. */
    <div>
      <div className="rd-sect-h">
        <span className="rd-sect-t">{title}</span>
        <span className="rd-sect-n">{clips.length}</span>
        <SortPicker compact sorts={sorts} sortBy={sortBy} setSortBy={setSortBy}
          sortDir={sortDir} setSortDir={setSortDir}/>
      </div>
      <div className="rd-sect-g">
        {clips.map(c=><RdClip key={c.id} clip={c} onApprove={onApprove}
          onReject={onReject} onOpen={onOpen} onEdit={onEdit}/>)}
      </div>
    </div>
  );
}

function ClipControls({ sorts, sortBy, setSortBy, sortDir, setSortDir,
                        channels, chan, setChan, kind, setKind,
                        hasHighlights, group, setGroup, hideSort, children }) {
  return (
    <div className="rd-controls">
      {children}
      {/* Only worth a control when there is more than one streamer to pick
          between — a menu whose every option is the same thing is furniture. */}
      {channels.length>1 && <RdMenu
        label="Streamer" icon="radio" value={chan} onChange={setChan}
        options={[{v:'all', l:'All streamers'}].concat(channels.map(c=>({v:c, l:c})))}/>}
      {/* Same rule as the streamer menu: with nothing but detected clips on
          screen, "Highlights only" selects an empty set and "Detected only"
          selects everything, so the control is three ways of saying the same
          thing. It appears the moment a highlight lands over the WS. */}
      {hasHighlights && <RdMenu
        label="Show" icon="sparkles" value={kind} onChange={setKind}
        options={CLIP_KINDS}/>}
      {/* Queue only. The Library has no grouping to turn off. */}
      {setGroup && hasHighlights && <RdMenu
        label="Order" icon="grid" value={group} onChange={setGroup}
        options={CLIP_GROUPS}/>}
      {!hideSort && <SortPicker sorts={sorts} sortBy={sortBy} setSortBy={setSortBy}
        sortDir={sortDir} setSortDir={setSortDir}/>}
    </div>
  );
}

// The sort menu and its direction button, on their own, because the queue can
// now show two independently sorted sections and each needs one. Extracted
// rather than copied: two copies of this is how one list ends up sortable by
// something the other is not, which is the exact drift the shared comparator
// was pulled out to end.
function SortPicker({ sorts, sortBy, setSortBy, sortDir, setSortDir, compact }) {
  const dir = dirLabelFor(sortBy, sortDir);
  return (
    <div className={'rd-sortwrap' + (compact ? ' compact' : '')}>
      <RdMenu label={compact ? '' : 'Sort'} icon="sliders" value={sortBy} onChange={setSortBy}
        options={sorts.map(v=>({v, l:CLIP_SORTS[v].l}))}/>
      <button className="rd-dir" onClick={()=>setSortDir(d=>d==='desc'?'asc':'desc')}
        title={'Currently ' + dir.toLowerCase() + ' — click to reverse'}>
        <Icon name={sortDir==='desc'?'arrowdown':'arrowup'} size={13}/>
        <span>{dir}</span>
      </button>
    </div>
  );
}

// PENDING ONLY. This screen is an inbox: the question it answers is "what is
// waiting on me", and a decision you have already made is not waiting on you.
// It used to carry All / Pending / Approved chips, so the default view mixed
// clips needing a verdict with clips that had one — and the Clip Library is
// already the place approved clips live, in full, sorted by when you kept them.
// Nothing is lost by dropping them from here; the two screens stop overlapping.
// What Twitch is actually refusing, in the user's terms, and WHO CAN FIX IT.
//
// That last part is the whole value of the notice. Every one of these is the
// broadcaster's setting — not the user's account, not ours — so a user staring
// at an empty queue needs to be told the thing is working and where the wall
// actually is. Kept as one map beside the banner so the wording is in one
// place; the admin table has its own, blunter copy, because an operator wants
// the cause and a customer wants to know whether to keep waiting.
const REFUSAL_COPY = {
  classification: {
    what: 'Twitch has not determined this channel’s content rating, and it will not create clips until it does.',
    who: 'The streamer needs to set their Content Classification Labels. Clipping resumes on its own once they do.'
  },
  title_automod: {
    what: 'The stream title did not pass Twitch’s automod, and Twitch refuses to clip while it stands.',
    who: 'It clears by itself when the streamer renames their stream — we keep trying in the background.'
  },
  not_authorized: {
    what: 'This broadcaster has clipping turned off on Twitch.',
    who: 'Only they can change that, so we stopped monitoring the channel to free the slot.'
  },
};

function ReviewScreen({ streams, scores, clips, onApprove, onReject, onOpen, onEdit, lost, me, onDismissLost, refusals, onDismissRefusal, onGoTutorial }) {
  const [showCull, setShowCull] = useState(false);
  const [sortBy, setSortBy] = useState('newest');
  const [sortDir, setSortDir] = useState('desc');
  const [chanFilter, setChanFilter] = useState('all');
  const [kind, setKind] = useState('all');
  const [group, setGroup] = useState('highlights');
  // The second sort, for the non-highlight half when the queue is split. It
  // starts where the primary one starts, so switching into split mode changes
  // the LAYOUT and nothing about the order until you actually pick something.
  const [restBy, setRestBy] = useState('newest');
  const [restDir, setRestDir] = useState('desc');
  const clipsArr = Object.values(clips).filter(c=>c.status==='pending');
  const pending = clipsArr.length;
  // Only to tell "you are caught up" apart from "you have never had a clip" in
  // the empty state — two very different things that read identically if the
  // screen only knows about its own queue.
  const approvedElsewhere = Object.values(clips).filter(c=>c.status==='approved').length;
  const streamsArr = Object.values(streams);
  const avgScore = streamsArr.length ? Math.round(streamsArr.reduce((a,s)=>a+(scores[s.channel]?.score||0),0)/streamsArr.length) : 0;
  // Streamer filter options come from the clips themselves, so the moment a
  // new streamer gets clipped (clip_ready over the WS) they become filterable.
  // If the selected streamer's clips all disappear (culled/removed), fall back
  // to 'all' rather than pinning the grid to an empty, invisible filter.
  const channels = [...new Set(clipsArr.map(c=>c.channel).filter(Boolean))].sort();
  const effChan = channels.includes(chanFilter) ? chanFilter : 'all';
  const hasHighlights = clipsArr.some(c=>c.suggested);
  // If the last highlight leaves the queue while "Highlights only" is selected,
  // fall back to all rather than pinning the screen to an empty view whose
  // control has just been hidden — the same self-correction the streamer
  // filter makes, and the reason both are computed rather than trusted.
  const effKind = (kind!=='all' && !hasHighlights) ? 'all' : kind;
  const filtered = filterClips(clipsArr, effChan, effKind);

  // Split needs both kinds actually present to mean anything: filtered to one
  // of them, or with no highlights in the queue at all, there is no second
  // section and the mode would render one list under a redundant heading.
  const split = group === 'split' && hasHighlights && effKind === 'all';
  const hiClips = split ? sortClips(filtered.filter(c=>c.suggested),
                                    sortBy, sortDir, 'strict') : [];
  const restClips = split ? sortClips(filtered.filter(c=>!c.suggested),
                                      restBy, restDir, 'strict') : [];
  // One `shown` either way, so the count and all three empty states keep
  // working off a single list rather than growing a split-mode branch each.
  const shown = split ? hiClips.concat(restClips)
                      : sortClips(filtered, sortBy, sortDir, group);
  const SORTS = ['newest', 'trigger', 'virality', 'audience', 'length',
                 'channel'];
  // The cap now REFUSES the new moment rather than deleting an old clip, so
  // "we did not clip this" is finally the accurate wording. The clip is never
  // created on Twitch either — the processor checks before spending the Helix
  // call — so there is no orphan for the user to find and contradict us with.
  const lostN = lost ? (lost.missed_24h || lost.lost_24h || 1) : 0;
  const nextPlan = lost && lost.next_plan;
  // THE WEEKLY LIBRARY ALLOWANCE, counted here rather than fetched.
  //
  // Every approved clip is already in `clips`, so the browser can answer "how
  // many did I keep this week" from what it is holding. That is not a
  // shortcut — a number pushed from the server would need its own event and
  // could drift out of step with the list on screen; this one is recomputed
  // from the same data the user is looking at and cannot disagree with it.
  // Only the CEILING comes from /me. Approving re-renders, so the meter moves
  // the moment a clip lands, without a refresh.
  const libCap  = (me && me.plan_limits && me.plan_limits.max_library_week) || 0;
  const libSince = Date.now()/1000 - 7*24*60*60;
  const libKept = Object.values(clips).filter(c =>
    c.status === 'approved' && (c.approved_at || c.created_at || 0) >= libSince).length;
  // The sentinel is a real number in JSON, so it has to be recognised rather
  // than printed — "0 of 1000000000 kept" reads as a bug. Same guard the queue
  // notice above uses.
  const libCapped = libCap > 0 && libCap < 1000000000;
  const libLeft = Math.max(0, libCap - libKept);
  return (
    <div className="rd-body rd-body-full" style={{flex:1}}>
      <section className="rd-main">
        {/* ABOVE the queue-full notice on purpose. Both answer "why am I not
            getting clips", and this one is the answer that is not the user's
            fault and that they cannot act on — reading it first stops them
            upgrading a plan to fix a broadcaster's setting.

            The server has already scoped these to this account and stripped
            the ones they dismissed, so everything that arrives is rendered. */}
        {(refusals||[]).map(r => {
          const copy = REFUSAL_COPY[r.reason] || {what:'Twitch is refusing to create clips on this channel.', who:''};
          return <div className="rd-lost rd-refused" key={r.channel}>
            <span className="ic"><Icon name="alert" size={16}/></span>
            <div className="tx">
              <b>Twitch will not clip {r.channel}.</b>{' '}
              {copy.what}{copy.who ? ' ' + copy.who : ''}
              {' '}Nothing is wrong with your account.
            </div>
            <button className="rd-lost-x" onClick={()=>onDismissRefusal(r.channel)}
              title="Dismiss" aria-label={'Dismiss the notice for ' + r.channel}>×</button>
          </div>;
        })}
        {lostN > 0 && <div className="rd-lost">
          <span className="ic"><Icon name="zap" size={16}/></span>
          <div className="tx">
            {/* The cap is only named when it is a real number. The admin cap is
                a large sentinel, and "full at 1000000000 clips" both reads as a
                bug and asserts a state that account cannot reach. The server
                suppresses this notice entirely for an uncapped queue; this is
                the second line of defence, so the sentinel cannot print raw
                whichever path put it here. */}
            <b>Your review queue is full{lost.limit && lost.limit < 1000000000 ? ' at ' + lost.limit + ' clips' : ''}.</b>{' '}
            {lostN === 1
              ? 'A highlight was not clipped, because there was no room left in your queue.'
              : lostN + ' highlights were not clipped in the last 24 hours, because there was no room left in your queue.'}
            {nextPlan
              ? ' ' + (nextPlan === 'starter' ? 'Starter' : 'Pro') + ' holds '
                + lost.next_limit + ' — $' + lost.next_price + '/month.'
              : ' Review or clear some to make room.'}
          </div>
          {nextPlan && <a className="rd-btn grad" href="/billing/paywall"
            style={{textDecoration:'none',flexShrink:0}}>See plans</a>}
          <button className="rd-lost-x" onClick={onDismissLost} title="Dismiss"
            aria-label="Dismiss">×</button>
        </div>}
        {/* TWO ROWS, and that is the organisation. The title line carries the
            actions that CHANGE things — culling and clearing, both
            destructive. The line below carries the controls that only change
            what you are looking at. They used to be one run of five controls
            with no grouping, so a bulk delete sat inches from a sort toggle.

            THERE USED TO BE A THIRD ROW ABOVE THESE: four big stat tiles, 130px
            of it. Two of them ("Pending review", "Approved") counted exactly
            what the filter chips below already select, and the other two are
            about STREAMS, on the screen for clips. Between those tiles, a
            duplicate "Clip review" heading under the one already in the page
            header, and the trial banner, 365px stood between the top of the
            window and the first clip — 52% of a 1366x700 laptop, on which
            precisely zero clips were fully visible.

            The numbers did not go away, they went to the control that uses
            them: the counts are on the filter chips you press to see them, and
            the stream context sits on the title line. Nothing is stated twice
            and the first clip starts far higher up. */}
        <div className="rd-toolbar">
          <span className="rd-toolbar-count">
            {shown.length === clipsArr.length
              ? shown.length + (shown.length === 1 ? ' clip' : ' clips')
              : shown.length + ' of ' + clipsArr.length}
          </span>
          {/* Only when there is something to say. "0 live · avg trigger 0" is
              four words to tell somebody nothing is happening, and it was two
              of the four tiles. */}
          {streamsArr.length > 0 &&
            <span className="rd-toolbar-meta">
              <Icon name="radio" size={12}/>
              {streamsArr.length} live
              {avgScore > 0 && <> · avg trigger {avgScore}</>}
            </span>}
          {/* Shown before it bites, not after. A limit a user only meets by
              being refused reads as the product breaking; a counter they have
              watched climb all week reads as a limit. Turns amber for the last
              five so the wall is never a surprise. */}
          {libCapped &&
            <span className={'rd-toolbar-meta'+(libLeft<=5?' warn':'')}
              title={'Your plan keeps ' + libCap + ' clips a week. Approving is paused once you reach it; the clips stay here until your week rolls over or you upgrade.'}>
              <Icon name="film" size={12}/>
              {libLeft > 0
                ? libKept + ' of ' + libCap + ' kept this week'
                : 'Weekly limit reached · ' + libCap + ' kept'}
            </span>}
          <div className="rd-toolbar-acts">
            {clipsArr.length > 0 && (
              <div style={{position:'relative'}}>
                <button className={'rd-btn sm'+(showCull?' active':'')} onClick={()=>setShowCull(v=>!v)} style={{background:showCull?'rgba(184,106,220,.18)':'rgba(255,255,255,.06)',border:'1px solid',borderColor:showCull?'var(--acc)':'var(--hair)',color:showCull?'var(--acc)':'var(--fg-2)'}}>
                  <Icon name="sparkles" size={13}/>Cull clips
                </button>
                {showCull && <CullPanel clips={clips} onDone={()=>setShowCull(false)}/>}
              </div>
            )}
            {pending > 0 && <ClearQueueButton pending={pending}/>}
          </div>
        </div>
        {/* The status chips are gone with the statuses. All / Pending /
            Approved over a list that is now pending by definition would be one
            live chip and two that select nothing — and an "Approved" chip on
            this screen is a second, worse doorway to the Clip Library. The
            streamer filter and the sort stay: those still narrow a real set. */}
        <ClipControls sorts={SORTS} sortBy={sortBy} setSortBy={setSortBy}
          sortDir={sortDir} setSortDir={setSortDir}
          channels={channels} chan={effChan} setChan={setChanFilter}
          kind={effKind} setKind={setKind} hasHighlights={hasHighlights}
          group={group} setGroup={setGroup}
          /* Split mode moves the sort into the section headings. Leaving it
             here too would put two controls on screen for one job, and the
             toolbar one would silently drive only the Highlights half. */
          hideSort={split}/>
        {/* THE SPLIT VIEW IS NOT INSIDE .rd-grid, and that is load-bearing.
            .rd-grid is `display:grid` with `repeat(auto-fill,minmax(310px,1fr))`
            columns AND its own `overflow-y:auto`. Nesting the sections in it
            made them ONE grid item: the whole two-section view was crushed
            into a single 310px column with the sort control clipped mid-word
            and four fifths of the screen empty, inside a second scrollbar.
            Every test still passed; the render is what caught it. The branches
            are siblings now, so split mode never enters the grid at all. */}
        {split && shown.length > 0
          ? <div className="rd-sects">
              <ClipSection title="Highlights" clips={hiClips} sorts={SORTS}
                sortBy={sortBy} setSortBy={setSortBy}
                sortDir={sortDir} setSortDir={setSortDir}
                onApprove={onApprove} onReject={onReject} onOpen={onOpen} onEdit={onEdit}/>
              <ClipSection title="Everything else" clips={restClips} sorts={SORTS}
                sortBy={restBy} setSortBy={setRestBy}
                sortDir={restDir} setSortDir={setRestDir}
                onApprove={onApprove} onReject={onReject} onOpen={onOpen} onEdit={onEdit}/>
            </div>
          : <div className="rd-grid">
          {/* EMPTY MEANS TWO DIFFERENT THINGS NOW, and they need different
              words. Before this screen dropped approved clips, an empty grid
              could only mean "you have never had a clip". It can now also mean
              "you have reviewed everything" — and telling somebody with 200
              clips in their library to go add a channel reads as the app having
              lost their work. The channel filter makes a third case: the queue
              is not empty, this streamer's slice of it is. */}
          {shown.length===0
            ? (clipsArr.length > 0
                ? <div className="rd-grid-empty"><div className="ic"><Icon name="film" size={42}/></div>
                    <div className="big">Nothing from {effChan}</div>
                    <div>Your queue has {clipsArr.length} clip{clipsArr.length===1?'':'s'} waiting from other streamers.</div>
                    <button className="rd-emptylink" onClick={()=>setChanFilter('all')}>Show every streamer →</button></div>
              : approvedElsewhere > 0
                ? <div className="rd-grid-empty"><div className="ic"><Icon name="check" size={42}/></div>
                    <div className="big">You are all caught up</div>
                    <div>Nothing is waiting on you. New highlights land here the moment they fire — the {approvedElsewhere} clip{approvedElsewhere===1?'':'s'} you kept {approvedElsewhere===1?'is':'are'} in your Clip Library.</div></div>
              : <div className="rd-grid-empty"><div className="ic"><Icon name="film" size={42}/></div><div className="big">Waiting for clips</div><div>Add a channel on the Live Streams tab — clips appear here the moment a highlight fires.</div>
                  {/* New here? This is the one screen a first-time user reliably
                      lands on with nothing to do, so it is where the walkthrough
                      belongs. It used to open /tutorial in a NEW TAB, because the
                      dashboard is a long-lived SPA holding a live socket and
                      navigating away throws that state out. The walkthrough now
                      has its own tab in here, so the socket survives and nobody
                      has to read instructions about this screen in a window that
                      is not this screen. */}
                  <button className="rd-emptylink" onClick={()=>onGoTutorial()}>Read the walkthrough →</button></div>)
            : shown.map(c=><RdClip key={c.id} clip={c} onApprove={onApprove} onReject={onReject} onOpen={onOpen} onEdit={onEdit}/>)}
            </div>}
      </section>
    </div>
  );
}

// Per-channel clip performance. It used to be a card on the SETTINGS screen,
// which is a drawer for things you change, not for numbers you read — and the
// screen's own subtitle promised "triggers, storage & workflow" while a normal
// user got presets and analytics. Channels are what this measures, so it lives
// on the screen about channels.
//
// EVERY channel, not just the monitored ones: /stats is derived from clips, so
// a streamer you have stopped watching still has a history worth reading, and
// moving this must not be the thing that quietly deletes access to it.
function ChannelPerformance() {
  const [stats, setStats] = useState(null);
  useEffect(()=>{
    const load = ()=> fetch('/stats').then(r=>r.json()).then(setStats).catch(()=>{});
    load();
    // Derived from clips, so it refreshes whenever one is created / approved /
    // rejected (forwarded on the in-page hz_ws channel) — approval rate, totals
    // and "clips this week" stay live with no refresh.
    const onWs = e=>{ try{ const m=JSON.parse(e.detail);
      if(['clip_ready','clip_updated','clip_removed'].includes(m.event)) load();
    }catch{} };
    window.addEventListener('hz_ws', onWs);
    // Rule 3 of the realtime contract: re-pull on reconnect/deploy so it
    // self-heals instead of staling behind an open tab.
    window.addEventListener('hz_refetch', load);
    return ()=>{ window.removeEventListener('hz_ws', onWs); window.removeEventListener('hz_refetch', load); };
  },[]);
  if(!stats || !stats.length) return null;
  return (
    <div className="rd-card glass" style={{marginTop:16}}>
      <h3><span className="si"><Icon name="trending" size={15}/></span>Channel performance</h3>
      <div className="desc">All time, per channel — including streamers you no longer monitor.</div>
          {stats.map(r=><div key={r.channel} style={{borderBottom:'1px solid var(--hair)',paddingBottom:16,marginBottom:16}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
              <span style={{fontSize:14,fontWeight:700,color:'var(--acc)'}}>{r.channel}</span>
              <span style={{fontSize:12,color:'var(--fg-3)'}}>{r.clips_this_week} clips this week</span>
            </div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:8}}>
              {[['Total clips',r.total_clips,'var(--fg)'],['Approval rate',r.approval_rate+'%',r.approval_rate>=60?'var(--live)':r.approval_rate>=30?'var(--pending)':'var(--danger)'],
                ['Avg score',r.avg_score,'var(--fg)'],['Avg virality',r.avg_virality,'var(--acc)'],['Pending',r.pending,'var(--pending)'],['Top signal',r.top_signal,'var(--fg)']
              ].map(([k,v,c])=><div key={k} style={{background:'rgba(255,255,255,.03)',borderRadius:12,padding:'12px 12px'}}>
                <div style={{fontSize:12,color:'var(--fg-3)',marginBottom:4}}>{k}</div>
                <div style={{fontSize:17,fontWeight:700,color:c}}>{v}</div>
              </div>)}
            </div>
          </div>)}
    </div>
  );
}

/* THE RETURNING STATES were going to get a TodayHeader here: the queue count
   at display size with one action beside it, on the Live Streams screen.

   IT WAS NOT BUILT, because rendering the app showed the plan had the wrong
   screen. The default route is Clip Review, not Live Streams — a returning
   user never sees Live Streams first — and Clip Review's toolbar already
   carries all three facts the header was going to lead with: how many clips
   are waiting, how many channels are live, and how many of the week's keeps
   are used, the last of which already turns amber for the final five. A
   header on a screen nobody lands on, repeating numbers that are already on
   the screen they DO land on, would have been a third copy of the same data.

   What was actually wrong is that the count sits at 12px in a toolbar. So the
   count is promoted below, and nothing is duplicated. */
function StreamsScreen({ streams, scores, profiles, histories, clips, activePlatform, onAdd, onRemove, onForce }) {
  const streamsArr = Object.values(streams);
  const [sel, setSel] = useState(null);
  useEffect(()=>{ if(!sel&&streamsArr.length>0) setSel(streamsArr[0].channel); },[streamsArr.length]);
  const active = streams[sel]||streamsArr[0];
  // With no streams the add panel MUST still render — this is now the only
  // place a channel can be added, so an early return here would leave a new
  // user with nowhere to start.
  if(!active) return (
    <div className="rd-streams-layout">
      <AddStreamPanel {...{streams,scores,profiles,activePlatform,onAdd,onRemove,onForce}}/>
      <div className="rd-detail">
        <div className="rd-grid-empty" style={{padding:'64px 0'}}>
          <div className="ic"><Icon name="radio" size={42}/></div>
          <div className="big">No streams monitored yet</div>
          <div>Search a streamer on the left to start watching for highlights.</div>
        </div>
        <ChannelPerformance/>
      </div>
    </div>
  );
  const p = profiles[active.channel]||{};
  const sd = scores[active.channel]||{score:0,breakdown:{}};
  const hist = histories[active.channel]||[sd.score];
  const recent = Object.values(clips).filter(c=>c.channel===active.channel).sort((a,b)=>(b.created_at||0)-(a.created_at||0)).slice(0,4);
  const WK=['CHAT_VELOCITY','KEYWORD','SENTIMENT','AUDIO_SPIKE','VIEWER_SPIKE','SILENCE_BURST'];
  const sw = p.signal_weights||{};
  const statusColor = active.status==='live'?'var(--live)':(active.status==='reconnecting'||active.status==='queued')?'var(--pending)':'var(--fg-2)';
  const statusLabel = active.status==='queued' ? 'waiting for a slot' : active.status;
  return (
    <div className="rd-streams-layout">
      <AddStreamPanel {...{streams,scores,profiles,activePlatform,onAdd,onRemove,onForce}}
        selected={active.channel} onSelect={setSel}/>
      <div className="rd-detail">
        <div className="rd-detail-head">
          <span className="av">{initials(active.channel)}</span>
          <div>
            <h2>{active.channel}</h2>
            <div className="mt"><span className="rd-chip">{active.platform}</span><span className="rd-chip">{active.preset}</span>
              <span style={{color:statusColor,fontWeight:600}}>● {statusLabel}</span></div>
          </div>
          <div className="sp"/>
          <button className="rd-btn ghost-force" onClick={()=>onForce(active.channel)}><Icon name="zap" size={14}/>Force clip</button>
        </div>
        <div className="rd-card2 glass">
          <div className="rd-chart-head">
            <span className="lbl">Trigger score · live</span>
            <span className="big" style={{color:scoreColor(sd.score)}}>{sd.score.toFixed(1)}</span>
          </div>
          <RdScoreChart data={hist}/>
        </div>
        <div className="rd-metrics">
          <div className="rd-metric glass"><div className="k">Threshold</div><div className="v">{p.trigger_threshold?p.trigger_threshold.toFixed(0):'—'}</div></div>
          <div className="rd-metric glass"><div className="k">Avg velocity</div><div className="v">{p.avg_velocity>0?p.avg_velocity.toFixed(1):'—'}<span style={{fontSize:12,color:'var(--fg-3)'}}> m/s</span></div></div>
          <div className="rd-metric glass"><div className="k">Approval rate</div>
            <div className="v" style={{color:p.approval_rate>=.7?'var(--live)':p.approval_rate>=.4?'var(--pending)':'var(--danger)'}}>
              {p.total_clips?Math.round(p.approval_rate*100)+'%':'—'}
            </div>
          </div>
          <div className="rd-metric glass"><div className="k">Total clips</div><div className="v">{p.total_clips||0}</div></div>
        </div>
        {Object.keys(sw).length>0 && <div className="rd-card2 glass">
          <h3 style={{fontSize:14,fontWeight:700,marginBottom:16,display:'flex',alignItems:'center',gap:8}}><Icon name="sliders" size={15} style={{color:'var(--acc)'}}/>Learned signal weights</h3>
          {WK.filter(k=>sw[k]!=null).map(k=>{const v=sw[k]||1;const pct=Math.min(100,(v/2.5)*100);return <div className="rd-weight" key={k}>
            <span className="wl">{signalLabel(k)}</span>
            <span className="wt"><span className="wf" style={{width:pct+'%'}}/></span>
            <span className="wv" style={{color:v>1.1?'var(--live)':v<0.9?'var(--fg-2)':'var(--fg)'}}>{v.toFixed(2)}x</span>
          </div>;})}
        </div>}
        <div>
          <div className="rd-eyebrow" style={{marginBottom:12}}>Recent clips · {active.channel}</div>
          {recent.length===0
            ? <div className="rd-empty" style={{padding:24}}>No clips captured yet.</div>
            : <div className="rd-grid" style={{overflow:'visible',paddingRight:0}}>
                {recent.map(c=><RdClip key={c.id} clip={c} onOpen={()=>{}} onApprove={()=>{}} onReject={()=>{}} libraryMode/>)}
              </div>}
        </div>
        <ChannelPerformance/>
      </div>
    </div>
  );
}

// The library is the ARCHIVE: approved clips only. An undecided clip belongs in
// Clip Review and nowhere else — showing it in both places let people approve
// from here and then wonder why the review queue still had work in it, and it
// buried the clips they had actually kept under a pile of ones they hadn't
// looked at yet.
//
// That is also why there is no status filter row any more. Pending is gone from
// this screen by definition, and a rejected clip is DELETED server-side (see
// /clips/{id}/reject) rather than kept with a status — so "Rejected" could never
// match anything, and "All" and "Approved" were the same button twice. The
// streamer filter stays: it is the one that still narrows a real list.
function LibraryScreen({ clips, onOpen, onDelete, onEdit, onGoReview }) {
  const [chanFilter, setChanFilter] = useState('all');
  // Defaults to newest APPROVAL, not newest capture. The library is the record
  // of what you decided to keep, so approving a clip puts it at the top even if
  // it was captured days ago and had been sitting in the queue since. Ordering
  // by capture time meant a clip you had just kept could appear pages down,
  // which reads as "my approval did nothing".
  //
  // It is a CHOICE now rather than the only possibility: this screen had no
  // sort at all while Review had three, so the same clips could be ordered on
  // one screen and not on the other.
  const [sortBy, setSortBy] = useState('approved');
  const [sortDir, setSortDir] = useState('desc');
  const [kind, setKind] = useState('all');
  const all = Object.values(clips);
  const approved = all.filter(c=>c.status==='approved');
  // Filterable streamers derive from the clips themselves — a newly-approved
  // streamer is selectable the moment their first clip lands over the WS.
  const channels = [...new Set(approved.map(c=>c.channel).filter(Boolean))].sort();
  const effChan = channels.includes(chanFilter) ? chanFilter : 'all';
  const hasHighlights = approved.some(c=>c.suggested);
  const effKind = (kind!=='all' && !hasHighlights) ? 'all' : kind;
  const clipsArr = sortClips(
    filterClips(approved, effChan, effKind),
    // Never grouped: nothing pending is listed here, and an approved highlight
    // should not outrank the rest of the library forever just for being one.
    sortBy, sortDir, 'strict');
  // Pending clips are not listed here, but their existence is worth surfacing —
  // otherwise hiding them reads as "my clips vanished" rather than "they are one
  // tab over waiting on you".
  const pendingCount = all.filter(c=>c.status==='pending').length;
  return (
    <div className="rd-scroll">
      {/* Same two rows as Clip Review, in the same order and out of the same
          components: what you can DO on the title line, what you are LOOKING at
          below it. The screen name is not repeated here — the page header two
          inches above already says it. */}
      <div className="rd-cliphead">
      <div className="rd-toolbar">
        <span className="rd-toolbar-count">
          {clipsArr.length === approved.length
            ? approved.length + ' approved'
            : clipsArr.length + ' of ' + approved.length}
        </span>
        <div className="rd-toolbar-acts">
          {pendingCount>0 && <button className="rd-btn sm" onClick={()=>onGoReview&&onGoReview()}
            title="Undecided clips live in Clip Review"
            style={{background:'rgba(250,204,21,.12)',color:'var(--pending)',border:'1px solid rgba(250,204,21,.25)'}}>
            <Icon name="grid" size={13}/>{pendingCount} waiting in Clip Review
          </button>}
        </div>
      </div>
      {approved.length>0 && <ClipControls
        sorts={['approved','newest','trigger','virality','audience','length',
                'channel']}
        sortBy={sortBy} setSortBy={setSortBy} sortDir={sortDir} setSortDir={setSortDir}
        channels={channels} chan={effChan} setChan={setChanFilter}
        kind={effKind} setKind={setKind} hasHighlights={hasHighlights}/>}
      </div>
      {clipsArr.length===0
        ? <div className="rd-grid-empty"><div className="ic"><Icon name="film" size={42}/></div><div className="big">Nothing here yet</div><div>{pendingCount>0?'Approve a clip in Clip Review and it is archived here.':'Clips you approve are archived in the library.'}</div></div>
        : <div className="rd-grid" style={{overflow:'visible',paddingRight:0}}>
            {clipsArr.map(c=><RdClip key={c.id} clip={c} onOpen={onOpen} onDelete={onDelete} onEdit={onEdit} libraryMode/>)}
          </div>}
    </div>
  );
}

function SettingsScreen({ streams }) {
  const PRESETS=[
    {name:'default',  emoji:'', desc:'General-purpose baseline. Good starting point for any stream type.'},
    {name:'small',    emoji:'', desc:'Small / growing streamers (<1k viewers). Lower thresholds catch moments that the default preset misses.'},
    {name:'fps',      emoji:'', desc:'FPS games (Valorant, CS2, Warzone, Apex). Shorter pre-roll, fast-action keywords, tighter cooldown.'},
    {name:'moba',     emoji:'',  desc:'MOBAs (League of Legends, Dota 2, SMITE). Pentakill, teamfight, and outplay keywords tuned in.'},
    {name:'chess',    emoji:'',  desc:'Chess and strategy games. Very sparse chat — only large eruptions fire. Cooldown extended to avoid duplicates.'},
    {name:'casino',   emoji:'', desc:'Casino, gambling and case-opening streams. Sensitive to win reactions; captures the moment and its aftermath.'},
    {name:'irl',      emoji:'', desc:'IRL / outdoor streams. Audio spikes and crowd reactions weighted higher than chat velocity.'},
    {name:'variety',  emoji:'', desc:'Just Chatting, reaction and variety content. Balanced settings with broad hype-word detection.'},
    {name:'sports',   emoji:'', desc:'Sports co-streams. Sensitive to goal/score spikes; longer post-roll captures the celebration.'},
  ];
  const streamsArr = Object.values(streams);
  return (
    <div className="rd-scroll">
      <div className="rd-settings">
        <div className="rd-card glass">
          <h3><span className="si"><Icon name="film" size={15}/></span>Content presets</h3>
          <div className="desc">Select when adding a stream to tune signal sensitivity.</div>
          <div className="rd-preset-grid">
            {PRESETS.map(p=><div className="rd-preset" key={p.name}>
              <div className="pn">
                <span>{p.name}</span>
                {p.name==='default'&&<span className="badge2">base</span>}
              </div>
              <div className="pr" style={{marginTop:8}}><span style={{color:'var(--fg-2)',fontSize:12,lineHeight:1.5}}>{p.desc}</span></div>
            </div>)}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Tutorial ─────────────────────────────────────────────────────────────────
// The same walkthrough as /tutorial, rendered inside the app instead of in a
// second tab. It cannot be a link: the dashboard is a long-lived SPA holding a
// live socket, and navigating away throws that state out — which is exactly why
// the old link opened a new window, and exactly why reading it meant flipping
// between two windows to follow steps about the one you left.
//
// Content comes from /tutorial/content, serialised from the same dataclasses
// the public page renders. Transcribing it here would guarantee the two drift.

// **like this** -> <b>like this</b>. Split on the delimiter rather than a
// regex: this file is a Python string and a backslash in it is a bug waiting
// to happen.
function tutBold(text){
  const parts = String(text||'').split('**');
  return parts.map((p,i)=> i%2 ? <b key={i}>{p}</b> : <React.Fragment key={i}>{p}</React.Fragment>);
}

function TutMedia({ m }){
  if(!m) return null;
  // A missing screenshot draws a labelled box, never a broken-image icon —
  // the same choice the public page makes, so the two agree about what a
  // not-yet-captured slot looks like.
  if(!m.exists) return (
    <div className="tut-ph" role="img" aria-label={m.alt}>
      <span className="tut-ph-k">{m.kind==='video'?'Video':'Screenshot'} coming soon</span>
      <span className="tut-ph-a">{m.alt}</span>
    </div>
  );
  if(m.kind==='video') return (
    <figure className="tut-fig">
      <video className="tut-media" controls preload="none" poster={m.poster} width={m.width} height={m.height}>
        <source src={m.src}/>
      </video>
      {m.caption && <figcaption className="tut-cap">{m.caption}</figcaption>}
    </figure>
  );
  return (
    <figure className="tut-fig">
      {/* loading="lazy" matters here: the tab holds a dozen full-width
          screenshots and eager-loading them all stalls the first paint. */}
      <img className="tut-media" src={m.src} alt={m.alt} width={m.width} height={m.height} loading="lazy"/>
    </figure>
  );
}

function TutSection({ s }){
  return (
    <section className="tut-sec" id={'tut-' + s.id}>
      <h3 className="tut-h">{s.title}{s.plan && <span className="tut-plan">{s.plan}</span>}</h3>
      {s.body && <p className="tut-body">{tutBold(s.body)}</p>}
      {s.steps.length > 0 && <ol className="tut-steps">
        {s.steps.map((t,i)=><li key={i}>{tutBold(t)}</li>)}
      </ol>}
      {s.note && <p className="tut-note">{tutBold(s.note)}</p>}
      <TutMedia m={s.media}/>
      {s.tip && <aside className="tut-tip"><span className="tut-tip-k">Tip</span><p>{tutBold(s.tip)}</p></aside>}
    </section>
  );
}

function TutorialScreen({ doc, onGo }){
  const [openFaq, setOpenFaq] = useState(-1);
  const [active, setActive] = useState('');
  const bodyRef = useRef(null);

  // The contents rail, built once so the scroll spy and the links agree on
  // exactly which sections are targets.
  const toc = doc ? [{id:'tut-top', nav:'Overview'}, {id:'tut-quickstart', nav:'Get started'}]
    .concat(doc.features.map(f=>({id:'tut-'+f.id, nav:f.nav})))
    .concat([{id:'tut-plans', nav:'Plans'}, {id:'tut-faq', nav:'Questions'}]) : [];

  // Which section the reader is actually looking at, so the rail can say so.
  //
  // Scroll listener on the SCROLLING ELEMENT, not the window: this screen
  // scrolls inside .rd-scroll and window scroll never fires.
  //
  // Only TOC TARGETS count. Every step of the quickstart is a .tut-sec too but
  // none of them are in the rail, so matching on all sections left the rail
  // blank for the whole first third of the page — highlighting nothing exactly
  // where a new reader is.
  useEffect(()=>{
    const el = bodyRef.current; if(!el || !doc) return;
    const ids = toc.map(t=>t.id);
    const onScroll = ()=>{
      const top = el.getBoundingClientRect().top;
      let cur = ids[0] || '';
      for(const id of ids){
        const sec = document.getElementById(id);
        if(sec && sec.getBoundingClientRect().top - top <= 90) cur = id;
      }
      setActive(cur);
    };
    onScroll();
    el.addEventListener('scroll', onScroll, {passive:true});
    return ()=>el.removeEventListener('scroll', onScroll);
  },[doc]);

  if(!doc) return (
    <div className="rd-scroll"><div className="rd-tut">
      <div className="rd-empty" style={{padding:32}}>Loading the walkthrough…</div>
    </div></div>
  );

  const jump = id => {
    const el = document.getElementById(id);
    if(el) el.scrollIntoView({behavior:'smooth', block:'start'});
  };

  return (
    <div className="rd-scroll" ref={bodyRef}>
      <div className="rd-tut">
        <aside className="tut-toc">
          <div className="tut-toc-k">On this page</div>
          {toc.map(t=>
            <button key={t.id} className={'tut-toc-l' + (active===t.id?' on':'')}
              onClick={()=>jump(t.id)}>{t.nav}</button>)}
          {/* The printable copy still exists and some people want it on a
              second screen while they work. Not the primary path any more. */}
          <a className="tut-toc-out" href="/tutorial" target="_blank" rel="noopener">
            Open as a page ↗
          </a>
        </aside>

        <div className="tut-main">
          <section className="tut-sec" id="tut-top">
            <h2 className="tut-title">{doc.hero.title}</h2>
            <p className="tut-lead">{doc.hero.lead}</p>
            <TutMedia m={doc.hero.media}/>
          </section>

          <section className="tut-sec" id="tut-quickstart">
            <h2 className="tut-title">{doc.quickstart.title}</h2>
            <p className="tut-lead">{doc.quickstart.lead}</p>
          </section>
          {doc.quickstart.sections.map(s=><TutSection key={s.id} s={s}/>)}

          {doc.features.map(s=><TutSection key={s.id} s={s}/>)}

          <section className="tut-sec" id="tut-plans">
            <h2 className="tut-title">{doc.plans.title}</h2>
            <div className="tut-tablewrap">
              <table className="tut-table">
                <thead><tr>{doc.plans.rows[0].map((c,i)=><th key={i}>{c}</th>)}</tr></thead>
                <tbody>
                  {doc.plans.rows.slice(1).map((r,i)=>
                    <tr key={i}>{r.map((c,j)=>j===0?<th key={j}>{c}</th>:<td key={j}>{c}</td>)}</tr>)}
                </tbody>
              </table>
            </div>
          </section>

          <section className="tut-sec" id="tut-faq">
            <h2 className="tut-title">{doc.faq.title}</h2>
            <p className="tut-lead">{doc.faq.lead}</p>
            <div className="tut-faq">
              {doc.faq.items.map((f,i)=>
                <div className={'tut-q' + (openFaq===i?' on':'')} key={i}>
                  <button className="tut-q-h" onClick={()=>setOpenFaq(openFaq===i?-1:i)}
                    aria-expanded={openFaq===i}>
                    <span>{f.q}</span><span className="tut-q-c">{openFaq===i?'−':'+'}</span>
                  </button>
                  {/* Answers carry <b> and <a> from the content file. It is our
                      own copy, not user input, and it never reaches this
                      component from anywhere else. */}
                  {openFaq===i && <div className="tut-q-a" dangerouslySetInnerHTML={{__html: f.a}}/>}
                </div>)}
            </div>
          </section>

          <section className="tut-sec">
            <h2 className="tut-title">{doc.support.title}</h2>
            {/* The public page tells readers to use the Feedback tab. They are
                already inside the app, so the tab is one click away — make it
                a button rather than an instruction. */}
            <p className="tut-body">Ask us directly and it comes straight through.</p>
            <button className="rd-btn grad" onClick={()=>onGo('feedback')}>
              <Icon name="chat" size={14}/>Open Feedback
            </button>
          </section>
        </div>
      </div>
    </div>
  );
}

const NAV=[{id:'streams',label:'Live Streams',icon:'radio'},{id:'review',label:'Clip Review',icon:'grid'},{id:'library',label:'Clip Library',icon:'film'},{id:'vod',label:'VOD Scanner',icon:'video'},{id:'uploads',label:'Clip Editor',icon:'upload'},{id:'schedule',label:'Scheduler',icon:'clock'},{id:'training',label:'Training',icon:'sparkles',labelerOnly:true},{id:'landing',label:'Landing Page',icon:'trending',adminOnly:true},{id:'tutorial',label:'Tutorial',icon:'book'},{id:'settings',label:'Settings',icon:'cog'},{id:'account',label:'Account',icon:'user'},{id:'feedback',label:'Feedback',icon:'chat'}];
// Tabs closed off on Kick FOR NON-ADMINS. Kick monitoring went live on
// 2026-09-15 (chat + audio + viewers, clips cut from live capture — no
// Kick-hosted clip, no Highlight clips) as an ADMIN-ONLY beta: the owner tests
// it on prod first, everyone else keeps the "coming soon" screen. `kickOpen`
// in the app is the switch (admins), and this list is what closes. Used by
// BOTH the route dispatch and the nav, so a blocked tab is greyed out and
// unclickable rather than looking live and then dead-ending; Account,
// Feedback, the platform switch and Sign out always stay live so Kick is
// never a trap.
const KICK_BLOCKED=['review','streams','library','vod','uploads','schedule','settings'];
const HEAD={streams:['Live Streams','Add channels and watch them score in real time'],review:['Clip Review','Approve or reject the highlights the bot caught'],library:['Clip Library','Every clip you have approved'],vod:['VOD Scanner','Find highlight moments in finished streams'],uploads:['Clip Editor','Bring clips in and cut them for vertical'],schedule:['Scheduler','Everything you have exported, posted for you at the time you set'],training:['Training Studio','Blind-score clips to calibrate the formula'],landing:['Landing Page','Curate the example clips visitors see'],tutorial:['Tutorial','How every screen works, start to finish'],settings:['Settings','How each preset tunes what counts as a highlight'],account:['Account','Billing, profile & platforms'],feedback:['Feedback','Questions, bugs & suggestions']};

function TrainingScreen() {
  // Blind scoring studio: the queue endpoint strips every bot judgment
  // (scores, signals, even the generated title), so the human rates the clip
  // with zero anchoring. The server pairs each submission with the bot's
  // hidden signal vector at save time. Only dimensions a human can honestly
  // judge from watching: chat velocity and keyword sliders were removed —
  // guessing at message rates just polluted the dataset.
  const DIMS = [
    ['sentiment','Sentiment','How emotionally charged?'],
    ['audio','Audio spike','How loud / reactive was it?'],
    ['virality','Virality','Would this travel — shareable, meme-able, clip-worthy?'],
  ];
  const FRESH = {sentiment:5,audio:5,virality:5};
  const [queue, setQueue] = useState(null);
  const [stats, setStats] = useState(null);
  const [agree, setAgree] = useState(null);
  // 'own'       — your account's clips, nobody has scored them.
  // 'agreement' — clips a TEAMMATE already scored, that you have not. The
  //               only way to find out whether the thing we call "human
  //               virality" is a shared judgement or one person's taste.
  const [mode, setMode] = useState('own');
  const [idx, setIdx] = useState(0);
  const [vals, setVals] = useState({...FRESH});
  const [busy, setBusy] = useState(false);
  const [playerTry, setPlayerTry] = useState(0);
  const loadStats = ()=>fetch('/training/stats').then(r=>r.ok?r.json():null).then(setStats).catch(()=>{});
  const loadAgree = ()=>fetch('/training/agreement').then(r=>r.ok?r.json():null).then(setAgree).catch(()=>{});
  // useCallback + [mode]: `load` is read by the effect below AND by submit, so
  // a stale closure here would keep refetching the queue you just left.
  const load = useCallback(()=>{
    fetch('/training/queue?mode=' + mode).then(r=>r.ok?r.json():[])
      .then(q=>{setQueue(q);setIdx(0);setPlayerTry(0);}).catch(()=>setQueue([]));
    loadStats(); loadAgree();
  }, [mode]);
  useEffect(()=>{
    setQueue(null);   // show Loading rather than the previous mode's clips
    load();
    // Realtime: a freshly-fired clip joins the blind queue live, teammates'
    // submissions tick the counter live, and the screen self-heals on
    // reconnect/deploy like every other data source.
    const onWs = e => { try {
      const m = JSON.parse(e.detail);
      if(m.event==='clip_ready') load();
      else if(m.event==='training_scored'){
        setStats(s=>({...(s||{by_labeler:{}}), total: m.total}));
        loadStats();   // refresh the per-trainer breakdown too
        loadAgree();   // a completed pair moves the agreement number
        // Someone else just scored this clip. In cross-rate mode that does not
        // remove it — a second opinion is the entire point — but in your OWN
        // queue it is gone, and leaving it on screen means submitting into a
        // 409 after watching thirty seconds of video.
        if(mode === 'own' && m.clip_id)
          setQueue(q=>Array.isArray(q)?q.filter(c=>c.id!==m.clip_id):q);
      }
    } catch {} };
    window.addEventListener('hz_refetch', load);
    window.addEventListener('hz_ws', onWs);
    return ()=>{ window.removeEventListener('hz_refetch', load); window.removeEventListener('hz_ws', onWs); };
  },[load, mode]);
  const cur = queue && queue.length ? queue[Math.min(idx, queue.length-1)] : null;
  // Score first, then (optionally) resolve the clip in the same click —
  // trainers never need to visit Clip Review, which keeps them blind.
  const submit = async (verdict)=>{
    if(!cur || busy) return;
    setBusy(true);
    try{
      const r = await fetch('/training/score',{method:'POST',headers:{'Content-Type':'application/json'},
        body: JSON.stringify({clip_id: cur.id, ...vals})});
      if(r.ok || r.status===409){
        // Only ever resolve YOUR OWN clip. In cross-rate mode the clip belongs
        // to another account and approving it is not yours to do.
        // The weekly library cap cannot refuse this one: /training/* is behind
        // _require_labeler, and get_plan resolves every labeler to pro, which
        // has no library ceiling. If that ever changes, a 403 here would leave
        // the clip pending in Clip Review rather than losing it — recoverable,
        // but it would need a message instead of this silent catch.
        if(mode==='own' && (verdict==='approve'||verdict==='reject')){
          await fetch(`/clips/${cur.id}/${verdict}`,{method:'POST'}).catch(()=>{});
        }
        setQueue(q=>q.filter(c=>c.id!==cur.id)); setIdx(0); setVals({...FRESH});
        setPlayerTry(0); loadStats(); loadAgree();
      }
    } catch {} finally { setBusy(false); }
  };
  const skip = ()=>{ if(queue&&queue.length) { setIdx(i=>(i+1)%queue.length); setVals({...FRESH}); setPlayerTry(0); } };
  // Inline playback on every screen size — trainers score from their phones
  // too. Tap-to-play (autoplay=false) works on modern mobile browsers; the
  // Twitch link under the player is the escape hatch, never the only path.
  const embedSrc = cur && cur.embed_url
    ? cur.embed_url + (cur.embed_url.indexOf('?')>=0?'&':'?') + 'parent=' + location.hostname + '&autoplay=false'
    : '';
  return (
    <div className="rd-scroll">
      <div className="rd-settings">
        <div className="rd-section-title">
          <span className="cnt">{queue===null?'Loading…':queue.length+' clip'+(queue.length===1?'':'s')+' waiting for you'}{stats?` · ${stats.total} scored by the team`:''}</span>
        </div>
        <div className="rd-filters" style={{marginBottom:12,alignSelf:'flex-start'}}>
          {[['own','My queue'],['agreement','Cross-rate']].map(([m,label])=>(
            <button key={m} className={'rd-filter'+(mode===m?' active':'')}
              onClick={()=>setMode(m)}
              title={m==='own'
                ? 'Clips from your own channels that nobody has scored yet'
                : 'Clips a teammate already scored — rate them blind so we can measure whether people agree'}>
              {label}
            </button>))}
        </div>
        {mode==='agreement' &&
          <div className="rd-card glass" style={{marginBottom:12,padding:'12px 16px'}}>
            <div style={{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap',marginBottom:agree&&agree.clips_rated_twice?10:0}}>
              <Icon name="sparkles" size={15}/>
              <span style={{flex:1,minWidth:240,fontSize:12,color:'var(--fg-2)'}}>
                <b style={{color:'var(--fg)'}}>Somebody already rated these.</b> You will
                not be shown what they said — an anchored second opinion measures
                suggestibility, not agreement. Rate what YOU saw.
              </span>
            </div>
            {agree && agree.clips_rated_twice > 0 &&
              <div style={{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}>
                <span className="rd-tag" style={{background:'rgba(184,106,220,.16)',color:'var(--acc)',fontWeight:800,fontSize:12}}>
                  {agree.clips_rated_twice} rated twice
                </span>
                {agree.agreement!==null && <span className="rd-tag">
                  agreement {agree.agreement>=0?'+':''}{agree.agreement}
                </span>}
                {agree.median_gap!==null && <span className="rd-tag">
                  typical gap {agree.median_gap} of 10
                </span>}
                {agree.within_two!==null && <span className="rd-tag">
                  {agree.within_two}% within 2 points
                </span>}
                {Object.entries(agree.by_pair||{}).map(([who,d])=>
                  <span key={who} className="rd-tag" style={{color:'var(--fg-3)'}}>{who}: {d.n}</span>)}
              </div>}
            {agree && !agree.clips_rated_twice &&
              <div style={{fontSize:12,color:'var(--fg-3)'}}>
                No clip has two ratings yet — the first few you score here create the
                very first measurement of whether this team agrees with itself.
              </div>}
          </div>}
        <div className="rd-card glass" style={{marginBottom:12,padding:'12px 16px',fontSize:12,color:'var(--fg-2)',display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
          <Icon name="sparkles" size={15}/>
          <span style={{flex:1,minWidth:220}}><b style={{color:'var(--fg)'}}>You're scoring blind.</b> The bot's numbers are hidden on purpose — rate what YOU saw, 1 (nothing) to 10 (insane). Your scores get paired with the bot's hidden read to recalibrate the formula.</span>
          {stats && <span style={{display:'flex',gap:4,alignItems:'center',flexWrap:'wrap'}}>
            <span className="rd-tag" style={{background:'rgba(184,106,220,.16)',color:'var(--acc)',fontWeight:800,fontSize:12}}>{stats.total} trained</span>
            {Object.entries(stats.by_labeler||{}).sort((a,b)=>b[1]-a[1]).map(([name,n])=>
              <span key={name} className="rd-tag">{name}: {n}</span>)}
          </span>}
        </div>
        {!cur
          ? <div className="rd-card glass" style={{textAlign:'center',padding:'48px 24px'}}>
              <div style={{marginBottom:12,color:'var(--acc)'}}><Icon name="check" size={36}/></div>
              <h3 style={{fontSize:17,justifyContent:'center'}}>Queue clear</h3>
              <div className="desc">{mode==='agreement'
                ? 'Nothing left that a teammate has rated and you have not. Score some in My queue — every one you do becomes cross-rateable for somebody else.'
                : 'New clips land here automatically as the bot captures them.'}</div>
            </div>
          : <div className="rd-card glass">
              <div style={{display:'flex',justifyContent:'space-between',alignItems:'baseline',flexWrap:'wrap',gap:8,marginBottom:12}}>
                <h3 style={{margin:0}}>{cur.channel}<span style={{fontSize:12,color:'var(--fg-3)',fontWeight:500,marginLeft:8}}>{cur.game||''}</span></h3>
                <span style={{fontSize:12,color:'var(--fg-3)'}}>{new Date((cur.created_at||0)*1000).toLocaleString()}</span>
              </div>
              {embedSrc
                ? <>
                    <div style={{position:'relative',paddingBottom:'56.25%',borderRadius:12,overflow:'hidden',background:'#000'}}>
                      <iframe key={playerTry} src={embedSrc+'&_r='+playerTry} style={{position:'absolute',inset:0,width:'100%',height:'100%',border:0}} allow="autoplay; fullscreen; encrypted-media; picture-in-picture" allowFullScreen scrolling="no" title="Clip"/>
                    </div>
                    <div style={{display:'flex',alignItems:'center',gap:12,marginTop:8,fontSize:12,color:'var(--fg-3)'}}>
                      <span>Player showing an error?</span>
                      <button className="rd-btn sm" onClick={()=>setPlayerTry(t=>t+1)}>Reload player</button>
                      {cur.twitch_url && <a href={cur.twitch_url} target="_blank" rel="noopener" style={{color:'var(--acc)',textDecoration:'none',fontWeight:600}}>Watch on Twitch ↗</a>}
                    </div>
                  </>
                : <a href={cur.twitch_url||'#'} target="_blank" rel="noopener" className="rd-btn sm" style={{textDecoration:'none'}}>Watch on Twitch ↗</a>}
              <div style={{marginTop:16,display:'flex',flexDirection:'column',gap:12}}>
                {DIMS.map(([key,label,hint])=>(
                  <div key={key} className="tr-dim">
                    <div className="tr-dim-head">
                      <span className="tr-dim-label">{label}<span className="tr-dim-hint">{hint}</span></span>
                      <span className="tr-dim-val">{vals[key]}</span>
                    </div>
                    <input type="range" min="1" max="10" step="1" value={vals[key]}
                           onChange={e=>setVals(v=>({...v,[key]:parseInt(e.target.value,10)}))}
                           className="tr-slider"/>
                  </div>
                ))}
              </div>
              <div style={{display:'flex',gap:8,marginTop:16,flexWrap:'wrap',alignItems:'center'}}>
                {/* Approve/Reject belong to the clip's OWNER. In cross-rate mode
                    it is somebody else's clip, so scoring is the whole job. */}
                {mode==='own' ? <>
                  <button className="rd-btn live" disabled={busy} onClick={()=>submit('approve')} style={{opacity:busy?0.6:1}}>
                    <Icon name="check" size={14}/>{busy?'Saving…':'Score & Approve'}
                  </button>
                  <button className="rd-btn danger" disabled={busy} onClick={()=>submit('reject')} style={{opacity:busy?0.6:1}}>
                    <Icon name="x" size={14}/>Score & Reject
                  </button>
                  <button className="rd-btn sm" disabled={busy} onClick={()=>submit(null)} title="Save the sliders and leave the clip pending for later review">Score only</button>
                </> : (
                  <button className="rd-btn grad" disabled={busy} onClick={()=>submit(null)} style={{opacity:busy?0.6:1}}>
                    <Icon name="check" size={14}/>{busy?'Saving…':'Save my score'}
                  </button>
                )}
                {queue.length>1&&<button className="rd-btn sm" onClick={skip}>Skip</button>}
              </div>
            </div>}
      </div>
    </div>
  );
}

function LandingScreen({ clips, featured, onToggle, onMove, onGrab, onPlace, myUrls }) {
  // Admin-only curation of the landing page. Featured entries come from
  // /landing/showcase — the same payload visitors get — so what is listed here
  // is literally what the site is showing.
  //
  // ONE LIST, TWO DESTINATIONS. The hero wall shows four channels being scored
  // and wants variety ACROSS channels; the examples grid is a spread of the
  // best clips and can repeat a channel happily. They used to be the same
  // list, so tuning one wrecked the other.
  const [q, setQ] = useState('');
  const max = 8;
  const inHero    = featured.filter(f=>f.hero    !== false);
  const inGallery = featured.filter(f=>f.gallery !== false);
  // The wall draws four tiles (two on a phone). Fewer than four and it repeats
  // channels, which argues against the thing the section exists to show.
  const heroShort = inHero.length > 0 && inHero.length < 4;
  const heroChannels = new Set(inHero.map(f=>(f.channel||'').toLowerCase()));
  const featuredIds = featured.map(f=>f.id);
  const eligible = Object.values(clips)
    .filter(c=>c.status==='approved' && c.platform==='twitch' && c.twitch_url && !featuredIds.includes(c.id))
    .filter(c=>!q.trim() || (c.channel||'').toLowerCase().includes(q.trim().toLowerCase()))
    .sort((a,b)=>(b.virality_score||0)-(a.virality_score||0));
  const full = featured.length >= max;
  const thumb = (c)=> c.thumbnail_url
    ? <img src={c.thumbnail_url} alt="" style={{width:96,height:54,objectFit:'cover',borderRadius:8,flexShrink:0,background:'var(--rd-bg-2)'}}/>
    : <div style={{width:96,height:54,borderRadius:8,flexShrink:0,background:'linear-gradient(135deg,#2a1840,#3a1a4d)'}}/>;
  const row = (c, right)=>(
    <div key={c.id} style={{display:'flex',alignItems:'center',gap:12,padding:'8px 12px',borderRadius:12,
      background:'rgba(255,255,255,.03)',border:'1px solid var(--hair)',minWidth:0}}>
      {thumb(c)}
      <div style={{minWidth:0,flex:1}}>
        <div style={{fontWeight:700,fontSize:14,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
          {c.clip_title||c.stream_title||'Clip'}</div>
        <div style={{fontSize:12,color:'var(--fg-3)',marginTop:4}}>
          {/* Same suppression as the review card: a crowd suggestion has no
              trigger score, and "0% trigger" here would read as a rating. */}
          {c.channel}{c.game?' · '+c.game:''} · {c.suggested?'highlight':Math.round(c.score||c.trigger_score||0)+'% trigger'}</div>
      </div>
      <div style={{display:'flex',gap:4,flexShrink:0}}>{right}</div>
    </div>
  );
  return (
    <div className="rd-scroll">
      <div className="rd-settings">
        <div className="rd-section-title">
          <h2>On the landing page</h2>
          <span className="cnt">{featured.length} of {max} slots · {inHero.length} in the hero · {inGallery.length} in the examples</span>
        </div>
        {heroShort && <div className="rd-card glass" style={{padding:'8px 16px',marginBottom:12,fontSize:12,color:'var(--pending)'}}>
          The hero wall draws four tiles and only {inHero.length} clip{inHero.length===1?' is':'s are'} set
          to Hero, so it will repeat {inHero.length===1?'that one':'them'}. Add {4-inHero.length} more.
        </div>}
        {inHero.length >= 4 && heroChannels.size < 4 && <div className="rd-card glass"
          style={{padding:'8px 16px',marginBottom:12,fontSize:12,color:'var(--pending)'}}>
          The hero clips come from only {heroChannels.size} channel{heroChannels.size===1?'':'s'}. The wall
          is meant to show several channels being watched at once, so it currently argues the opposite —
          feature a clip from {4-heroChannels.size} more channel{4-heroChannels.size===1?'':'s'}.
        </div>}
        {inGallery.length === 0 && featured.length > 0 && <div className="rd-card glass"
          style={{padding:'8px 16px',marginBottom:12,fontSize:12,color:'var(--pending)'}}>
          Nothing is set to Examples, so the sample clips section is hidden on the landing page.
        </div>}
        <div className="rd-card glass" style={{marginBottom:12,padding:'12px 16px',fontSize:12,color:'var(--fg-2)',
          display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
          <Icon name="trending" size={15}/>
          <span style={{flex:1,minWidth:220}}>These are the real clips visitors see at
            highlightz.app. <b>Hero</b> puts a clip in the animated wall at the top —
            it needs four, from four different channels, or the wall repeats itself.
            <b> Examples</b> puts it in the sample clips grid further down. Order here
            is the order they appear in both. <b>Grab</b> copies one into your own Clip
            Library so the team can trade clips — it links to the same Twitch clip,
            nothing is re-hosted.</span>
          <a href="/" target="_blank" rel="noopener" className="rd-btn sm" style={{textDecoration:'none'}}>View live page ↗</a>
        </div>
        {featured.length===0
          ? <div className="rd-card glass" style={{textAlign:'center',padding:'32px 24px',marginBottom:24}}>
              <div className="desc">No clips featured yet — add a few from the list below and the
                examples section appears on the landing page.</div>
            </div>
          : <div style={{display:'flex',flexDirection:'column',gap:8,marginBottom:24}}>
              {featured.map((c,i)=>{
                // Already in your library? Say so rather than offering a
                // button that errors — the server refuses a duplicate, and a
                // dead button is worse than no button.
                const mine = myUrls.has(c.twitch_url);
                const h = c.hero !== false, g = c.gallery !== false;
                const chip = (on,label,where,tip)=>(
                  <button className={'rd-btn sm'+(on?' live':'')} title={tip}
                    onClick={()=>onPlace(c.id, where, !on)}>{on?'✓ ':''}{label}</button>
                );
                return row(c,<>
                  {chip(h,'Hero','hero','Show this clip in the animated wall at the top')}
                  {chip(g,'Examples','gallery','Show this clip in the sample clips grid')}
                  <button className="rd-btn sm" disabled={mine} onClick={()=>onGrab(c.id)}
                    title={mine?'Already in your clips':'Copy this clip into your own library'}>
                    {mine ? 'In yours' : 'Grab'}
                  </button>
                  <button className="rd-btn sm" disabled={i===0} onClick={()=>onMove(c.id,'up')} title="Move up">↑</button>
                  <button className="rd-btn sm" disabled={i===featured.length-1} onClick={()=>onMove(c.id,'down')} title="Move down">↓</button>
                  <button className="rd-btn sm danger" onClick={()=>onToggle(c.id)}>Remove</button>
                </>);
              })}
            </div>}

        <div className="rd-section-title">
          <h2>Approved clips you can add</h2>
          <span className="cnt">{eligible.length} available</span>
        </div>
        <div style={{margin:'8px 0 12px'}}>
          <input className="rd-input" placeholder="filter by streamer" value={q} onChange={e=>setQ(e.target.value)}
            style={{maxWidth:280}}/>
        </div>
        {full && <div className="rd-card glass" style={{padding:'8px 16px',marginBottom:12,fontSize:12,color:'var(--pending)'}}>
          All {max} slots are full — remove one above to add another.</div>}
        {eligible.length===0
          ? <div className="rd-card glass" style={{textAlign:'center',padding:'32px 24px'}}>
              <div className="desc">{q.trim()?'No approved clips from that streamer.':'Approve some Twitch clips first — approved clips show up here.'}</div>
            </div>
          : <div style={{display:'flex',flexDirection:'column',gap:8}}>
              {eligible.slice(0,40).map(c=>row(c,
                <button className="rd-btn sm live" disabled={full} onClick={()=>onToggle(c.id)}>Add</button>))}
            </div>}
      </div>
    </div>
  );
}

function AccountScreen({ me }) {
  const [deleting, setDeleting]   = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [delErr, setDelErr]       = useState('');
  const sub        = me.subscription_status || 'none';
  const trialDays  = me.trial_days_left || 0;
  const isTrial    = sub === 'trialing';
  // FREE IS A PLAN, NOT THE ABSENCE OF ONE. This row used to read
  // "No subscription" in the dim/inactive colour for everyone on the free
  // tier — so a new signup opened Account and the most prominent line on the
  // screen told them they had nothing, which reads as the product being
  // broken rather than as the tier working. The wording predates the free
  // tier, when no subscription really did mean no access. Lapsed states say
  // where the user landed for the same reason: they still have the product.
  const subLabel   = isTrial ? `Free trial — ${trialDays} day${trialDays===1?'':'s'} left`
    : ({active:'Active', past_due:'Past due', expired:'Trial ended — on Free',
        canceled:'Canceled — on Free', inactive:'Canceled — on Free',
        none:'Free plan — active'}[sub] || sub);
  const subColor   = (sub==='active'||sub==='trialing') ? 'var(--live)'
    : sub==='past_due' ? 'var(--pending)'
    // Free and lapsed are working states, not warnings. Only a genuine billing
    // problem gets the alarm colour.
    : 'var(--fg-2)';
  const isSubscribed = sub==='active'||sub==='trialing';

  const hasTwitch  = !!(me.twitch_login);
  // Twitch is the only way in. Signing in with Kick was already disabled at the
  // route before the Kick OAuth flow was removed altogether, so there is no
  // longer a second answer to this.
  const signedInWith = 'Twitch';

  const deleteAccount = async () => {
    setDeleting(true); setDelErr('');
    try {
      const r = await fetch('/account', {method:'DELETE'});
      if (r.ok) { window.location.href='/login'; return; }
      const d = await r.json().catch(()=>({}));
      setDelErr(d.detail||'Delete failed — try again');
    } catch { setDelErr('Network error — try again'); }
    setDeleting(false);
  };

  return (
    <div className="rd-scroll">
      <div className="rd-settings">

        {/* Subscription */}
        <div className="rd-card glass">
          <h3><span className="si"><Icon name="card" size={15}/></span>Subscription</h3>
          <div className="desc">Your plan and billing.</div>
          <div className="rd-field">
            <div><div className="fl">Plan status</div></div>
            {/* No capitalize: the labels are written with the casing they
                should have, and title-casing them turns "Trial ended — on
                Free" into "Trial Ended — On Free". */}
            <span style={{fontWeight:700,color:subColor}}>{subLabel}</span>
          </div>
          {me.plan_label && <div className="rd-field">
            <div><div className="fl">Membership</div>
              {/* The admin cap is a large sentinel, not a real number, so it is
                  named rather than printed — "1000000000 pending clips" reads
                  as a bug, which is how anyone seeing it would report it. */}
              <div className="fd">{me.plan_limits ? `${me.plan_limits.max_streams} monitored stream${me.plan_limits.max_streams===1?'':'s'} · ${me.plan_limits.max_pending >= 1000000000 ? 'unlimited' : me.plan_limits.max_pending} pending clips · ${me.plan_limits.max_library_week >= 1000000000 ? 'unlimited clips kept' : me.plan_limits.max_library_week + ' clips kept a week'} · VOD scanner ${me.plan_limits.vod?'included':'not included'}` : ''}</div>
            </div>
            <span style={{fontWeight:700,color:'var(--acc)'}}>{me.plan_label}</span>
          </div>}
          {sub==='active' && me.plan==='starter' && <div className="rd-field">
            <div><div className="fl">Upgrade to Pro</div><div className="fd">10 streams, 200 pending clips, and the VOD scanner — $25/month</div></div>
            <a href="/billing/portal" className="rd-btn grad" style={{textDecoration:'none',display:'inline-flex',gap:8,alignItems:'center'}}>
              <Icon name="zap" size={14}/>Upgrade
            </a>
          </div>}
          {/* Two kinds of trial, opposite advice. A card-up-front trial is
              already a subscription and converts on its own — telling that
              user to subscribe would send them into a second checkout. An
              admin comp has no card and really does just stop. me.trial_converts
              is true only for the first. */}
          {isTrial && me.trial_converts && <div className="rd-field">
            <div><div className="fl">Billing</div><div className="fd">Your card is charged when the {trialDays===1?'last day':`${trialDays} days`} run out — cancel before then and you pay nothing.</div></div>
            <a href="/billing/portal" className="rd-btn sm" style={{textDecoration:'none'}}>Manage billing</a>
          </div>}
          {isTrial && !me.trial_converts && <div className="rd-field">
            <div><div className="fl">Keep your access</div><div className="fd">Subscribe before the trial ends and nothing stops — your clips and streams carry straight over</div></div>
            <a href="/billing/checkout" className="rd-btn grad" style={{textDecoration:'none',display:'inline-flex',gap:8,alignItems:'center'}}>
              <Icon name="zap" size={14}/>Subscribe
            </a>
          </div>}
          {sub==='active' && <div className="rd-field">
            <div><div className="fl">Billing</div><div className="fd">Manage or cancel via Stripe portal</div></div>
            <a href="/billing/portal" className="rd-btn sm" style={{textDecoration:'none'}}>Manage billing</a>
          </div>}
          {!isSubscribed && sub!=='trialing' && <div className="rd-field">
            <div><div className="fl">Want more?</div>
              <div className="fd">
                Starter is $10/month for 3 streams and 50 pending clips.
                Pro is $25 for 10 streams, 200 pending and the VOD scanner.
              </div></div>
            <a href="/billing/paywall" className="rd-btn grad" style={{textDecoration:'none',display:'inline-flex',gap:8,alignItems:'center'}}>
              <Icon name="zap" size={14}/>See plans
            </a>
          </div>}
          {!isSubscribed && <div className="fd" style={{marginTop:12,fontSize:12,color:'var(--fg-2)'}}>Have a promo code? Enter it at checkout for 50% off your first month.</div>}
        </div>

        {/* Profile & Connected Platforms */}
        <div className="rd-card glass">
          <h3><span className="si"><Icon name="user" size={15}/></span>Profile &amp; Platforms</h3>
          <div className="desc">Your account and connected streaming platforms.</div>

          {/* Avatar + display name + sign out */}
          <div style={{display:'flex',alignItems:'center',gap:12,padding:'12px 0 16px',borderBottom:'1px solid rgba(255,255,255,.07)'}}>
            {me.avatar_url
              ? <img src={me.avatar_url} alt={me.username} style={{width:48,height:48,borderRadius:'50%',objectFit:'cover',flexShrink:0}}/>
              : <span style={{width:48,height:48,borderRadius:'50%',background:'var(--grad)',display:'grid',placeItems:'center',fontWeight:700,color:'#14021c',fontSize:17,flexShrink:0}}>{(me.username||'?')[0].toUpperCase()}</span>}
            <div style={{flex:1,minWidth:0}}>
              <div style={{fontWeight:700,fontSize:14,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{me.username||'—'}</div>
              <div style={{fontSize:12,color:'var(--fg-3)',marginTop:4}}>Signed in with {signedInWith}</div>
            </div>
            <button className="rd-btn sm danger" style={{flexShrink:0}}
              onClick={()=>fetch('/logout',{method:'POST'}).then(()=>{location.href='/login';})}>
              Sign out
            </button>
          </div>

          {/* Twitch row */}
          <div style={{display:'flex',alignItems:'center',gap:12,padding:'12px 0',borderBottom:'1px solid rgba(255,255,255,.07)'}}>
            <span style={{width:32,height:32,borderRadius:8,background:'rgba(145,71,255,.18)',display:'grid',placeItems:'center',flexShrink:0}}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="#9146ff"><path d="M11.571 4.714h1.715v5.143H11.57zm4.715 0H18v5.143h-1.714zM6 0L1.714 4.286v15.428h5.143V24l4.286-4.286h3.428L22.286 12V0zm14.571 11.143l-3.428 3.428h-3.429l-3 3v-3H6.857V1.714h13.714z"/></svg>
            </span>
            <div style={{flex:1,minWidth:0}}>
              <div style={{fontWeight:600,fontSize:12}}>Twitch</div>
              {hasTwitch
                ? <div style={{fontSize:12,color:'var(--fg-3)',marginTop:4}}>@{me.twitch_login}</div>
                : <div style={{fontSize:12,color:'var(--fg-3)',marginTop:4}}>Not connected</div>}
            </div>
            {hasTwitch
              ? <span style={{fontSize:12,color:'#9146ff',fontWeight:600,flexShrink:0}}>✓ Connected</span>
              : <a href="/auth/twitch" className="rd-btn sm" style={{textDecoration:'none',flexShrink:0}}>Connect</a>}
          </div>

          {/* Kick row */}
          <div style={{display:'flex',alignItems:'center',gap:12,padding:'12px 0 4px'}}>
            <span style={{width:32,height:32,borderRadius:8,background:'rgba(83,252,24,.12)',display:'grid',placeItems:'center',flexShrink:0}}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="#53fc18"><path d="M2 2h4v8l6-8h5l-7 9 7 9h-5l-6-8v8H2z"/></svg>
            </span>
            {/* No Connect button and no linked-account state, on purpose: Kick
                channels are monitored from their public broadcast and chat, so
                no Kick login is needed and none is stored — both legal pages
                say so. Clips on Kick are files Highlightz captures itself. */}
            <div style={{flex:1,minWidth:0}}>
              <div style={{fontWeight:600,fontSize:12}}>Kick</div>
              <div style={{fontSize:12,color:'var(--fg-3)',marginTop:4}}>No account needed. Switch to Kick and add a channel.</div>
            </div>
            <span style={{fontSize:12,color:'#53fc18',fontWeight:600,flexShrink:0}}>Live</span>
          </div>
        </div>

        {/* Legal links */}
        <div className="rd-card glass">
          <h3><span className="si"><Icon name="database" size={15}/></span>Legal</h3>
          <div className="desc">Terms, privacy, and cookie information.</div>
          {[['Terms of Service','/tos'],['Privacy Policy','/privacy'],['Cookie Policy','/cookies']].map(([lbl,href])=>(
            <div key={href} className="rd-field">
              <div className="fl">{lbl}</div>
              <a href={href} target="_blank" rel="noopener" className="rd-btn sm" style={{textDecoration:'none'}}>View ↗</a>
            </div>
          ))}
        </div>

        {/* Danger zone */}
        <div className="rd-card glass" style={{border:'1px solid rgba(255,90,120,.22)'}}>
          <h3><span className="si" style={{background:'rgba(255,90,120,.14)',color:'var(--danger)'}}><Icon name="trash" size={15}/></span>Danger zone</h3>
          <div className="desc">Permanently delete your account and all data — clips, streams, and settings. This cannot be undone.</div>
          {isSubscribed && <div style={{fontSize:12,color:'var(--pending)',marginBottom:12,padding:'8px 12px',background:'rgba(255,194,92,.08)',borderRadius:10,border:'1px solid rgba(255,194,92,.2)'}}>
            You have an active subscription. Cancel it via <a href="/billing/portal" style={{color:'var(--pending)'}}>Manage billing</a> before deleting your account so you are not charged again.
          </div>}
          {delErr && <div style={{fontSize:12,color:'var(--danger)',marginBottom:8}}>{delErr}</div>}
          {!confirmDel
            ? <button className="rd-btn danger" onClick={()=>setConfirmDel(true)}>Delete my account</button>
            : <div style={{display:'flex',flexDirection:'column',gap:8}}>
                <div style={{fontSize:12,color:'var(--danger)',fontWeight:600}}>This will delete all your clips, streams, and account data. Continue?</div>
                <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
                  <button className="rd-btn danger" onClick={deleteAccount} disabled={deleting} style={{flex:'1 1 auto'}}>
                    {deleting ? 'Deleting…' : 'Yes, delete everything'}
                  </button>
                  <button className="rd-btn" onClick={()=>{setConfirmDel(false);setDelErr('');}} style={{flex:'0 0 auto'}}>Cancel</button>
                </div>
              </div>}
        </div>

        <div style={{textAlign:'center',fontSize:12,color:'var(--fg-3)',paddingBottom:24}}>
          &copy; 2026 ANTI Technology LLC — All rights reserved.
        </div>
      </div>
    </div>
  );
}

function FeedbackScreen({ onSeen }) {
  const CATEGORIES = ['General','Bug report','Feature request','Question'];
  // The user's own threads. Fetched on mount and again whenever a reply
  // arrives over the socket, so an open tab updates without a refresh.
  const [threads, setThreads] = useState([]);
  const [replyTo, setReplyTo]   = useState('');   // thread id being answered
  const [replyMsg, setReplyMsg] = useState('');
  const [replyErr, setReplyErr] = useState('');
  const sendReply = async (id) => {
    const msg = replyMsg.trim();
    if(!msg){ setReplyErr('Write something first.'); return; }
    setReplyErr('');
    try {
      const r = await fetch('/feedback/' + encodeURIComponent(id) + '/reply', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ message: msg }),
      });
      if(!r.ok){ const d = await r.json().catch(()=>({})); setReplyErr(d.detail||'Failed to send.'); return; }
      setReplyMsg(''); setReplyTo(''); loadThreads();
    } catch { setReplyErr('Network error — try again.'); }
  };
  const loadThreads = useCallback(()=>{
    fetch('/feedback/mine').then(r=>r.ok?r.json():null)
      .then(d=>{ if(Array.isArray(d)) setThreads(d); }).catch(()=>{});
  }, []);
  useEffect(()=>{
    loadThreads();
    // Opening the tab IS reading them — clear the badge, then tell the shell so
    // the nav count drops without waiting for the next poll.
    fetch('/feedback/mark-read', {method:'POST'})
      .then(()=>{ if(onSeen) onSeen(); }).catch(()=>{});
    const onReply = ()=>{ loadThreads(); };
    window.addEventListener('hz_fb_reply', onReply);
    // Rule 3 of the realtime contract, which this screen was the only one
    // missing. hz_fb_reply covers a reply that arrives while the socket is UP;
    // it cannot cover one posted while it was DOWN, because no event is
    // delivered for the gap. So across a deploy the nav badge lit — loadFbUnread
    // is in refetchAll — while the thread the user was reading stayed exactly
    // as it was. Being told you have a reply by a badge, on the screen that is
    // supposed to be showing it to you, is worse than not being told.
    window.addEventListener('hz_refetch', loadThreads);
    return ()=>{
      window.removeEventListener('hz_fb_reply', onReply);
      window.removeEventListener('hz_refetch', loadThreads);
    };
  }, [loadThreads, onSeen]);
  const [category, setCategory] = useState('General');
  const [message, setMessage]   = useState('');
  const [sending, setSending]   = useState(false);
  const [sent, setSent]         = useState(false);
  const [err, setErr]           = useState('');

  const submit = async () => {
    const msg = message.trim();
    if (!msg) { setErr('Please enter a message.'); return; }
    setSending(true); setErr('');
    try {
      const r = await fetch('/feedback', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ message: msg, category }),
      });
      if (!r.ok) { const d = await r.json().catch(()=>({})); setErr(d.detail||'Failed to send — try again.'); }
      else { setSent(true); setMessage(''); }
    } catch { setErr('Network error — try again.'); }
    setSending(false);
  };

  return (
    <div className="rd-scroll">
      <div className="rd-settings">
        <div className="rd-card glass">
          <h3><span className="si"><Icon name="chat" size={15}/></span>Send feedback</h3>
          <div className="desc">Questions, suggestions, bug reports — we read everything.</div>
          {sent ? (
            <div style={{padding:'24px 0',textAlign:'center'}}>
              <div style={{fontSize:30,marginBottom:12}}>✓</div>
              <div style={{fontWeight:700,marginBottom:8}}>Thanks for your feedback!</div>
              <div style={{fontSize:12,color:'var(--fg-3)',marginBottom:16}}>We'll review it shortly.</div>
              <button className="rd-btn" onClick={()=>{setSent(false);loadThreads();}}>Send another</button>
            </div>
          ) : (
            <>
              <div style={{marginBottom:12}}>
                <div style={{fontSize:12,fontWeight:600,color:'var(--fg-3)',textTransform:'uppercase',letterSpacing:'.04em',marginBottom:8}}>Category</div>
                <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
                  {CATEGORIES.map(c=>(
                    <button key={c} onClick={()=>setCategory(c)} style={{
                      padding:'4px 12px',borderRadius:99,fontSize:12,fontWeight:600,cursor:'pointer',border:'1px solid',transition:'var(--dur-fast)',
                      background: category===c ? 'rgba(184,106,220,.2)' : 'rgba(255,255,255,.05)',
                      borderColor: category===c ? 'rgba(184,106,220,.5)' : 'rgba(255,255,255,.09)',
                      color: category===c ? 'var(--acc)' : 'var(--fg-3)',
                    }}>{c}</button>
                  ))}
                </div>
              </div>
              <div style={{marginBottom:12}}>
                <div style={{fontSize:12,fontWeight:600,color:'var(--fg-3)',textTransform:'uppercase',letterSpacing:'.04em',marginBottom:8}}>Message</div>
                <textarea
                  value={message}
                  onChange={e=>setMessage(e.target.value)}
                  placeholder="Tell us what's on your mind…"
                  maxLength={2000}
                  rows={6}
                  style={{width:'100%',background:'rgba(255,255,255,.04)',border:'1px solid rgba(255,255,255,.09)',borderRadius:12,color:'var(--fg)',padding:'12px 12px',fontSize:14,resize:'vertical',outline:'none',fontFamily:'inherit',lineHeight:1.6}}
                />
                <div style={{textAlign:'right',fontSize:12,color:'var(--fg-3)',marginTop:4}}>{message.length}/2000</div>
              </div>
              {err && <div style={{color:'var(--danger)',fontSize:12,marginBottom:12,padding:'8px 12px',background:'rgba(255,90,120,.08)',borderRadius:9,border:'1px solid rgba(255,90,120,.2)'}}>{err}</div>}
              <button className="rd-btn grad" onClick={submit} disabled={sending} style={{opacity:sending?.6:1}}>
                <Icon name="chat" size={14}/>{sending ? 'Sending…' : 'Send feedback'}
              </button>
            </>
          )}
        </div>

        {threads.length > 0 &&
          <div className="rd-card glass" style={{marginTop:16}}>
            <h3><span className="si"><Icon name="chat" size={15}/></span>Your messages</h3>
            <div className="desc">Your conversations with us — anything you have sent, anything we have sent you, and every reply either way.</div>
            <div style={{display:'flex',flexDirection:'column',gap:12,marginTop:12}}>
              {threads.map(t=>(
                <div key={t.id} style={{border:'1px solid var(--hair)',borderRadius:10,padding:'12px 12px',
                    background:t.reply_unread?'rgba(184,106,220,.07)':'transparent'}}>
                  <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:4}}>
                    <span style={{fontFamily:'ui-monospace,monospace',fontSize:12,letterSpacing:'.12em',
                      textTransform:'uppercase',color:t.from_admin_start?'var(--acc)':'var(--fg-3)'}}>
                      {t.from_admin_start ? 'From Highlightz' : t.category}</span>
                    <span style={{fontSize:12,color:'var(--fg-3)'}}>{fmtTime(t.created_at)}</span>
                    {t.reply_unread && <span className="navbadge" style={{position:'static'}}>new</span>}
                  </div>
                  {/* A thread WE opened has no opening message from them, so
                      there is nothing to draw here — the first thing in it is
                      our message, which the replies below already render with
                      the right name and colour. Without this guard it drew an
                      empty bubble above every message we send. */}
                  {!t.from_admin_start &&
                    <div style={{fontSize:14,lineHeight:1.55,whiteSpace:'pre-wrap'}}>{t.message}</div>}
                  {(t.replies||[]).map((r,ri)=>(
                    <div key={ri} style={{marginTop:8,paddingLeft:12,
                        borderLeft:'2px solid '+(r.from_admin===false?'var(--hair-2)':'var(--acc)')}}>
                      <div style={{fontSize:12,fontWeight:700,marginBottom:4,
                          color:r.from_admin===false?'var(--fg-3)':'var(--acc)'}}>
                        {r.from_admin===false?'You':'Highlightz'}</div>
                      <div style={{fontSize:14,lineHeight:1.55,whiteSpace:'pre-wrap'}}>{r.message}</div>
                      <div style={{fontSize:12,color:'var(--fg-3)',marginTop:4}}>{fmtTime(r.at)}</div>
                    </div>
                  ))}
                  {replyTo===t.id ? (
                    <div style={{marginTop:12}}>
                      <textarea className="rd-input" rows={3} value={replyMsg} autoFocus
                        onChange={e=>setReplyMsg(e.target.value)}
                        placeholder="Write your reply…"
                        style={{width:'100%',resize:'vertical',fontFamily:'inherit',fontSize:14}}/>
                      {replyErr && <div style={{fontSize:12,color:'var(--bad)',marginTop:4}}>{replyErr}</div>}
                      <div style={{display:'flex',gap:8,marginTop:8}}>
                        <button className="rd-btn grad" onClick={()=>sendReply(t.id)}>Send reply</button>
                        <button className="rd-btn" onClick={()=>{setReplyTo('');setReplyMsg('');setReplyErr('');}}>Cancel</button>
                      </div>
                    </div>
                  ) : (
                    <button className="rd-btn" style={{marginTop:12}}
                      onClick={()=>{setReplyTo(t.id);setReplyMsg('');setReplyErr('');}}>Reply</button>
                  )}
                </div>
              ))}
            </div>
          </div>}
      </div>
    </div>
  );
}

function fmtBytes(n){
  if(!n) return '0 MB';
  const mb = n/1048576;
  return mb >= 1024 ? (mb/1024).toFixed(1)+' GB' : (mb<10?mb.toFixed(1):Math.round(mb))+' MB';
}

function TwitchImport() {
  const [clips, setClips]   = useState([]);
  const [cursor, setCursor] = useState('');
  const [loading, setLoad]  = useState(false);
  const [started, setStart] = useState(false);
  const [err, setErr]       = useState('');
  const [sort, setSort]     = useState('views');
  const [play, setPlay]     = useState(null);
  // This lightbox is the worse of the two cases: .ed-bg blurs the whole
  // viewport and .tw-box blurs the player's own container, so the blur is an
  // ANCESTOR of the video rather than merely behind it.
  usePlayerOpen(!!play);

  const fetchPage = useCallback(async (cur) => {
    setLoad(true); setErr('');
    try {
      const r = await fetch('/twitch/clips' + (cur ? '?cursor=' + encodeURIComponent(cur) : ''));
      if (!r.ok) {
        let d = 'Could not load your clips';
        try { d = (await r.json()).detail || d; } catch {}
        setErr(d); return;
      }
      const data = await r.json();
      // De-dupe by id: Helix pages by view count, and a clip whose count
      // changes mid-paging can legitimately appear on two pages.
      setClips(prev => {
        const seen = new Set(prev.map(c => c.id));
        return [...prev, ...(data.clips || []).filter(c => !seen.has(c.id))];
      });
      setCursor(data.cursor || '');
      // Only on success. Setting this in `finally` made a FAILED first load
      // look like a completed one: the retry button vanished and the user got
      // an error sitting next to "No clips on your channel yet" — two
      // contradictory messages and no way forward. A failed first load must
      // leave the button exactly where it was.
      setStart(true);
    } catch { setErr('Could not reach the server'); }
    finally { setLoad(false); }
  }, []);

  const shown = [...clips].sort((a, b) =>
    sort === 'views' ? (b.view_count || 0) - (a.view_count || 0)
                     : String(b.created_at || '').localeCompare(String(a.created_at || '')));

  return (
    <div className="rd-card glass">
      <h3><span className="si"><Icon name="download" size={15}/></span>Your Twitch clips</h3>
      <div className="desc">
        Every clip on your channel — the ones you made and the ones your viewers made.
        Browse and watch them here; Twitch doesn't let apps download clip files, so
        to edit one, download it from your Twitch Creator Dashboard and drop it in above.
      </div>

      {!started
        ? <button className="rd-btn grad" disabled={loading} onClick={()=>fetchPage('')}>
            {loading ? 'Loading…' : 'Load my Twitch clips'}
          </button>
        : <>
            <div style={{display:'flex',alignItems:'center',gap:8,flexWrap:'wrap',marginBottom:12}}>
              <span style={{fontSize:12,color:'var(--fg-3)'}}>
                {clips.length} clip{clips.length===1?'':'s'} loaded
              </span>
              <div className="rd-filters" style={{marginLeft:'auto'}}>
                {[['views','Most viewed'],['recent','Newest']].map(([k,label])=>(
                  <button key={k} className={'rd-filter'+(sort===k?' active':'')}
                    onClick={()=>setSort(k)}>{label}</button>
                ))}
              </div>
            </div>

            {clips.length===0 && !loading &&
              <div className="rd-grid-empty" style={{padding:'32px 0'}}>
                <div className="ic"><Icon name="film" size={38}/></div>
                <div className="big">No clips on your channel yet</div>
                <div>Clips you or your viewers create on Twitch will show up here.</div>
              </div>}

            {clips.length>0 &&
              <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(240px,1fr))',gap:12}}>
                {shown.map(c=>(
                  <div className="rd-tw" key={c.id}>
                    <button className="tw-thumb" onClick={()=>setPlay(c)} title="Play clip">
                      {c.thumbnail_url
                        ? <img src={c.thumbnail_url} alt="" loading="lazy"/>
                        : <span className="tw-noimg"><Icon name="film" size={26}/></span>}
                      <span className="tw-play"><Icon name="play" size={16}/></span>
                      <span className="tw-dur">{Math.round(c.duration||0)}s</span>
                    </button>
                    <div className="tw-meta">
                      <div className="tw-title" title={c.title}>{c.title || 'Untitled clip'}</div>
                      <div className="tw-sub">
                        {(c.view_count||0).toLocaleString()} view{c.view_count===1?'':'s'}
                        {c.creator_name ? ' · by ' + c.creator_name : ''}
                      </div>
                    </div>
                  </div>
                ))}
              </div>}

            {cursor &&
              <button className="rd-btn" style={{marginTop:12}} disabled={loading}
                onClick={()=>fetchPage(cursor)}>
                {loading ? 'Loading…' : 'Load more'}
              </button>}
          </>}

      {err && <div style={{marginTop:12,fontSize:12,color:'var(--danger)'}}>{err}</div>}

      {/* Playback in a lightbox, not in the card. Twitch's embed draws its own
          title, avatar and controls over the video; at ~300px of grid cell
          they overlap the picture and it reads as broken. The player needs
          real width, so give it the screen. */}
      {play && <div className="ed-bg" onMouseDown={e=>{ if(e.target===e.currentTarget) setPlay(null); }}>
        <div className="tw-box glass">
          <div className="ed-head">
            <span style={{color:'var(--acc)'}}><Icon name="film" size={18}/></span>
            <h3>{play.title || 'Clip'}</h3>
            <button className="rd-btn sm" onClick={()=>setPlay(null)}>Close</button>
          </div>
          <div className="tw-frame">
            <iframe src={play.embed_url + '&parent=' + location.hostname + '&autoplay=true'}
              allow="autoplay; fullscreen; encrypted-media; picture-in-picture" allowFullScreen title={play.title||'Clip'}/>
          </div>
          <div className="ed-note" style={{marginTop:8}}>
            {(play.view_count||0).toLocaleString()} views
            {play.creator_name ? ' · clipped by ' + play.creator_name : ''} ·{' '}
            <a href={play.url} target="_blank" rel="noopener noreferrer"
               style={{color:'var(--acc)',fontWeight:600}}>Open on Twitch</a>
          </div>
        </div>
      </div>}
    </div>
  );
}

/* ── Clip editor ────────────────────────────────────────────────────────────
   Everything runs in the USER'S browser. The clip is already on our disk from
   the upload, but the decode, the compositing and the encode all happen on
   their machine — the droplet has 1 vCPU already saturated by audio meters for
   every monitored channel, and a 30s 1080p re-encode there would starve live
   clip detection to serve a side feature.

   Two encode paths, chosen at runtime:
     WebCodecs      fast (a 30s clip in seconds), real H.264. Chrome/Edge,
                    Safari 16.4+, Firefox 130+.
     MediaRecorder  everywhere else. Real-time (a 30s clip takes 30s) because
                    it records playback rather than encoding frames directly.

   Both draw the same canvas, so the picture is identical either way. Only the
   speed and the container differ.                                          */

const HAS_WEBCODECS = typeof window !== 'undefined'
  && typeof window.VideoEncoder === 'function'
  && typeof window.VideoFrame === 'function';

// Preferred MediaRecorder types, best first. H.264 in an MP4 is what TikTok and
// Instagram accept; WebM is the last resort and needs a server-side convert
// before it can be published anywhere.
//
// EVERY ENTRY THAT NAMES A VIDEO CODEC ALSO NAMES AN AUDIO ONE. A type string
// listing video alone is a request for a video-only container on some builds,
// which silently drops the audio track we hand the recorder.
// The video-only variants ('...codecs=avc1.42E01E' on its own) are gone: the
// bare container below each one matches every browser they did and lets the
// browser choose an audio codec too, which is the whole point.
// Profile order matters for the picture: High (avc1.64) and Main (avc1.4D)
// get CABAC and B-frames, so the same bitrate buys visibly more detail than
// Baseline (avc1.42), which is listed last as the compatibility floor.
const REC_TYPES = [
  'video/mp4;codecs=avc1.640028,mp4a.40.2',
  'video/mp4;codecs=avc1.4D401F,mp4a.40.2',
  'video/mp4;codecs=avc1.42E01E,mp4a.40.2',
  'video/mp4',
  'video/webm;codecs=h264,opus',
  'video/webm;codecs=vp9,opus',
  'video/webm',
];

function pickRecorderType() {
  if (typeof MediaRecorder === 'undefined') return '';
  return REC_TYPES.find(t => { try { return MediaRecorder.isTypeSupported(t); } catch { return false; } }) || '';
}

/* ── Export, frame-accurate (WebCodecs) ──────────────────────────────────────
   WHY A SECOND EXPORT PATH. MediaRecorder records the canvas in REAL TIME:
   it keeps whatever frames the encoder finishes before the next one arrives
   and drops the rest, and it spends bitrate on its own schedule. Measured in
   software-rendered Chromium on the real paint path (blur fill + caption):
   captureStream(30) landed 10.8 fps in the file and captureStream(60) landed
   11.6, at 1.5 Mbps against 16 asked for. A laptop GPU does better, but the
   mechanism is the same — smoothness depends on the machine, and 60 fps is
   never guaranteed. That is "the export is not smooth".

   This path encodes EVERY frame the source presents, at its own media time,
   with the encoder allowed to fall behind: requestVideoFrameCallback hands
   over each decoded frame, the canvas is painted for exactly that time, the
   frame is queued to a VideoEncoder, and if the queue backs up playback is
   PAUSED until it drains rather than frames being lost. If the video element
   itself starts skipping (presentedFrames jumps), playback rate is halved so
   it stops. Audio is decoded offline from the source file and encoded
   separately, so it cannot drift or stall. The result is deterministic:
   source fps in, source fps out, the bitrate that was asked for, and no
   "playback stalled" failure mode. It runs at roughly real time on a slow
   machine and faster than real time on a fast one.

   The container is written by a vendored muxer (/static/vendor): the raw
   encoder output is an elementary stream nobody can open, which is the reason
   this was not wired in before. MP4 (H.264 + AAC) where the browser has those
   encoders, else WebM (VP9 + Opus); the MediaRecorder path stays as the
   fallback for browsers with neither WebCodecs nor a muxer-compatible codec.

   Same paintFrame, same opts. There is still exactly one draw path. */

function _haveMuxers() {
  return typeof Mp4Muxer !== 'undefined' || typeof WebMMuxer !== 'undefined';
}

// Which encoders THIS browser has for THIS output. null means "use the
// recorder". Probed with the real dimensions: H.264 level 4.0 is only rated to
// 1080p30, so 1080x1920 at 60 asks for 4.2 first and lets the browser say no.
async function frameAccurateSupport(w, h, fps) {
  if (typeof VideoEncoder === 'undefined' || typeof AudioEncoder === 'undefined') return null;
  if (!('requestVideoFrameCallback' in HTMLVideoElement.prototype)) return null;
  if (!_haveMuxers()) return null;
  const tryV = async (codec) => {
    try { return (await VideoEncoder.isConfigSupported({ codec, width: w, height: h, bitrate: 16e6, framerate: fps })).supported; }
    catch (e) { return false; }
  };
  const tryA = async (codec) => {
    try { return (await AudioEncoder.isConfigSupported({ codec, sampleRate: 48000, numberOfChannels: 2, bitrate: 160e3 })).supported; }
    catch (e) { return false; }
  };
  if (typeof Mp4Muxer !== 'undefined' && await tryA('mp4a.40.2')) {
    for (const c of ['avc1.64002A', 'avc1.640028', 'avc1.4D402A', 'avc1.4D401F', 'avc1.42E01F']) {
      if (await tryV(c)) return { ext: 'mp4', mime: 'video/mp4', vcodec: c, acodec: 'mp4a.40.2', mux: 'mp4', label: 'MP4 (H.264)' };
    }
  }
  if (typeof WebMMuxer !== 'undefined' && await tryA('opus') && await tryV('vp09.00.10.08')) {
    return { ext: 'webm', mime: 'video/webm', vcodec: 'vp09.00.10.08', acodec: 'opus', mux: 'webm', label: 'WebM (VP9)' };
  }
  return null;
}

function _makeMuxer(sup, w, h, fps, audio) {
  const a = audio ? { sampleRate: audio.sampleRate, numberOfChannels: audio.channels } : null;
  if (sup.mux === 'mp4') {
    return new Mp4Muxer.Muxer({
      target: new Mp4Muxer.ArrayBufferTarget(),
      video: { codec: 'avc', width: w, height: h, frameRate: fps },
      audio: a ? { codec: 'aac', ...a } : undefined,
      // The index at the front, so the file plays before it has fully
      // downloaded — the same reason the ffmpeg cut uses +faststart.
      fastStart: 'in-memory',
      firstTimestampBehavior: 'offset',
    });
  }
  return new WebMMuxer.Muxer({
    target: new WebMMuxer.ArrayBufferTarget(),
    video: { codec: 'V_VP9', width: w, height: h, frameRate: fps },
    audio: a ? { codec: 'A_OPUS', ...a } : undefined,
    firstTimestampBehavior: 'offset',
  });
}

// The clip's own sound for the cut, encoded OFFLINE: fetch the source, decode
// it, render just [inPt, outPt] to 48 kHz through an OfflineAudioContext, and
// feed that to an AudioEncoder in 1024-frame pieces. Nothing here is real
// time, so it cannot drift against the picture or stall behind it. A source
// we cannot fetch (cross-origin) exports silent, as the recorder path did.
async function _encodeAudioOffline(sup, srcUrl, inPt, outPt, sfx) {
  let decoded = null;
  try {
    const ab = await (await fetch(srcUrl)).arrayBuffer();
    const actx = new (window.AudioContext || window.webkitAudioContext)();
    try { decoded = await actx.decodeAudioData(ab); } finally { actx.close().catch(() => {}); }
  } catch (e) { decoded = null; }
  if (decoded && !decoded.numberOfChannels) decoded = null;
  const haveSfx = !!(sfx && sfx.length);
  if (!decoded && !haveSfx) return null;
  const sr = 48000, ch = decoded ? Math.min(2, decoded.numberOfChannels) : 2;
  const secs = Math.max(0.05, outPt - inPt);
  const len = Math.ceil(secs * sr);
  const off = new OfflineAudioContext(ch, len, sr);
  if (decoded) {
    const src = off.createBufferSource();
    src.buffer = decoded; src.connect(off.destination);
    src.start(0, Math.max(0, inPt), secs);
  }
  // The sound effects go into the SAME offline render as the clip's own
  // audio, at the same offsets the preview scheduled them at. The context
  // starts at 0 = the cut's start, so base is 0.
  if (haveSfx) scheduleSfx(off, off.destination, sfx, 0);
  const pcm = await off.startRendering();
  const chunks = [];
  let err = null;
  const enc = new AudioEncoder({ output: (chunk, meta) => chunks.push([chunk, meta]), error: e => { err = e; } });
  enc.configure({ codec: sup.acodec, sampleRate: sr, numberOfChannels: ch, bitrate: 160e3 });
  const N = 1024;
  for (let i = 0; i < len; i += N) {
    const n = Math.min(N, len - i);
    const planar = new Float32Array(n * ch);
    for (let k = 0; k < ch; k++) planar.set(pcm.getChannelData(k).subarray(i, i + n), k * n);
    const ad = new AudioData({ format: 'f32-planar', sampleRate: sr, numberOfFrames: n, numberOfChannels: ch,
                               timestamp: Math.round(i / sr * 1e6), data: planar });
    enc.encode(ad); ad.close();
    if (err) break;
  }
  await enc.flush(); enc.close();
  if (err) throw err;
  return { chunks, sampleRate: sr, channels: ch };
}

// Every frame, at its own time. See the block comment above.
//   v, c    the editor's video and canvas (canvas already at outW x outH)
//   paint   (t) => paints the canvas for media time t
//   onPct   progress 0..99
//   cancel  a ref; true aborts
async function exportFrameAccurate(sup, { v, c, paint, inPt, outPt, outW, outH, fps, hd, srcUrl, onPct, cancel, sfx }) {
  const audio = await _encodeAudioOffline(sup, srcUrl, inPt, outPt, sfx).catch(() => null);
  if (cancel.current) return null;
  const muxer = _makeMuxer(sup, outW, outH, fps, audio);
  if (audio) for (const [chunk, meta] of audio.chunks) muxer.addAudioChunk(chunk, meta);

  let encErr = null;
  const enc = new VideoEncoder({
    output: (chunk, meta) => { try { muxer.addVideoChunk(chunk, meta); } catch (e) { encErr = e; } },
    error: e => { encErr = e; },
  });
  const cfg = { codec: sup.vcodec, width: outW, height: outH, bitrate: hd ? 16e6 : 10e6,
                framerate: fps, latencyMode: 'quality' };
  if (sup.mux === 'mp4') cfg.avc = { format: 'avc' };
  enc.configure(cfg);

  // Park at the in-point. Assigning the current time fires no 'seeked'.
  v.pause();
  if (Math.abs(v.currentTime - inPt) > 0.01) {
    v.currentTime = inPt;
    await new Promise(res => { const h = () => { v.removeEventListener('seeked', h); res(); };
                               v.addEventListener('seeked', h); setTimeout(h, 3000); });
  }
  const span = Math.max(0.1, outPt - inPt);
  const frameDur = Math.round(1e6 / fps);
  let n = 0, lastTs = -1, lastPresented = null, lastFrameAt = Date.now();
  v.playbackRate = 1;

  await new Promise((res, rej) => {
    let finished = false;
    const done = (e) => { if (finished) return; finished = true; e ? rej(e) : res(); };
    const onFrame = (_now, md) => {
      if (finished) return;
      if (cancel.current) return done();
      if (encErr) return done(encErr);
      lastFrameAt = Date.now();
      const mt = md.mediaTime;
      if (mt >= outPt - 1e-4) return done();
      // The ELEMENT skipped a frame (it decodes on its own clock and a busy
      // tab can fall behind). Slow it down AND go back for what it skipped:
      // seek to the last frame that was encoded, so the skipped ones are
      // presented again on the slower pass. The `ts > lastTs` guard below
      // makes the re-presented frames harmless, and the timestamps stay
      // media time, so the file is identical to a pass that never skipped.
      // Measured without the seek-back: 5 frames lost in the first 100 ms
      // while the rate ramped 1 -> 0.5 -> 0.25, then none. This closes that.
      if (lastPresented !== null && md.presentedFrames - lastPresented > 1) {
        if (v.playbackRate > 0.25) v.playbackRate = v.playbackRate / 2;
        if (lastTs >= 0) {
          lastPresented = null;              // the seek re-presents; not a skip
          v.currentTime = inPt + lastTs / 1e6;
          v.requestVideoFrameCallback(onFrame);
          return;
        }
      }
      lastPresented = md.presentedFrames;
      if (mt >= inPt - 1e-4) {
        const ts = Math.round((mt - inPt) * 1e6);
        if (ts > lastTs) {                 // a re-presented frame after a pause
          paint(mt);
          const frame = new VideoFrame(c, { timestamp: ts, duration: frameDur });
          try { enc.encode(frame, { keyFrame: n % (fps * 2) === 0 }); } finally { frame.close(); }
          n++; lastTs = ts;
        }
      }
      onPct(Math.min(99, ((mt - inPt) / span) * 100));
      // Backpressure: the encoder is behind. Hold the picture until it has
      // caught up — that is the whole difference from the recorder path,
      // which would have thrown these frames away.
      if (enc.encodeQueueSize > 6 && !v.paused) {
        v.pause();
        (async () => {
          while (enc.encodeQueueSize > 2 && !finished && !cancel.current) await new Promise(r => setTimeout(r, 15));
          if (!finished && !cancel.current) v.play().catch(e => done(e));
        })();
      }
      v.requestVideoFrameCallback(onFrame);
    };
    v.addEventListener('ended', () => done(), { once: true });
    v.requestVideoFrameCallback(onFrame);
    // A stall guard on real time, like the recorder's: no frame for 10s is
    // stuck, not slow.
    const guard = setInterval(() => {
      if (finished) return clearInterval(guard);
      if (Date.now() - lastFrameAt > 10000) { clearInterval(guard); done(new Error('Playback stalled during the export. Try again, or trim a shorter section.')); }
    }, 1000);
    v.play().catch(e => done(e));
  });
  v.pause();
  v.playbackRate = 1;
  if (cancel.current) { try { enc.close(); } catch (e) {} return null; }
  await enc.flush();
  enc.close();
  if (encErr) throw encErr;
  if (!n) throw new Error('Export produced no frames.');
  muxer.finalize();
  return { blob: new Blob([muxer.target.buffer], { type: sup.mime }), ext: sup.ext, frames: n };
}

/* ── Export audio ────────────────────────────────────────────────────────────
   WHY THIS EXISTS. The export records canvas.captureStream(), and a canvas has
   no sound — so the recorded stream carried a video track and nothing else,
   and every exported clip came out silent. Reproduced in Chromium: the shipped
   path wrote a file with no audio stream at all.

   The fix is to hand the recorder the video element's audio as a second track.
   WebAudio rather than v.captureStream() because it also solves the problem
   that made the old code mute the element: createMediaElementSource REROUTES
   the element's sound into the graph, so what the user hears is whatever we
   connect to ctx.destination. Turning the monitor gain down makes the export
   silent in the room while the recorded track stays hot. Setting v.muted for
   that — which is what the code used to do — mutes the captured track too, so
   it would defeat the fix.

   ONE GRAPH PER ELEMENT, FOREVER: createMediaElementSource throws if it is
   called twice for the same element, and the routing it installs is permanent.
   Hence the WeakMap. */
const AUDIO_GRAPHS = typeof WeakMap === 'function' ? new WeakMap() : null;

// A media element whose audio WebAudio is allowed to read. A cross-origin
// source without CORS is not: createMediaElementSource does not throw on one,
// it silently yields silence — and because the routing is permanent, that
// would take the PREVIEW's sound with it. Same-origin and blob: only, so the
// worst case is the silent-export behaviour we already had.
function canReadAudio(url) {
  if (!url) return false;
  if (url.indexOf('blob:') === 0 || url.indexOf('data:') === 0) return true;
  try { return new URL(url, location.href).origin === location.origin; }
  catch (e) { return false; }
}

function audioGraph(v, url) {
  if (!AUDIO_GRAPHS || !v || !canReadAudio(url)) return null;
  var g = AUDIO_GRAPHS.get(v);
  if (g !== undefined) return g;            // null is cached too: do not retry
  var AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) { AUDIO_GRAPHS.set(v, null); return null; }
  try {
    var ctx = new AC();
    var src = ctx.createMediaElementSource(v);
    var monitor = ctx.createGain();                  // what the room hears
    var dest = ctx.createMediaStreamDestination();   // what gets recorded
    src.connect(monitor); monitor.connect(ctx.destination);
    src.connect(dest);
    // Sound effects feed the same two outputs, so a preview hears them and a
    // recorder export records them. Through `monitor` for the room, which an
    // export turns down, and straight to `dest` for the recording.
    var sfx = ctx.createGain();
    sfx.connect(monitor); sfx.connect(dest);
    g = { ctx: ctx, monitor: monitor, dest: dest, sfx: sfx };
  } catch (e) {
    g = null;                     // no audio track, or the browser said no
  }
  AUDIO_GRAPHS.set(v, g);
  return g;
}

const RATIOS = [
  ['9:16', 9 / 16, 'Vertical'],
  ['1:1',  1,      'Square'],
  ['16:9', 16 / 9, 'Original'],
];

function edTime(s) {
  s = Math.max(0, s || 0);
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}.${String(Math.floor((s % 1) * 10))}`;
}

/* Draw one frame of the edit. Single source of truth for how the output looks —
   the preview and BOTH encoders call this, so what the user sees while
   scrubbing is exactly what gets exported. */
/* How long a cue stays up after its own end, when nothing has replaced it yet.
   Speech has small gaps between words and cues break on them, so without this
   the caption strobes off and back on several times a second. It is BOUNDED on
   purpose: if a transcript ends early, the screen goes clean instead of
   freezing on the last line forever and looking broken. */
const CAP_HOLD_S = 0.8;

/* ── Transitions ─────────────────────────────────────────────────────────────
   Small, and pure functions of media time, which is the only way they can be
   right: paintFrame draws one frame for one `t`, so a transition is just what
   the frame looks like at that distance from the cut's start or end. The
   preview and both exporters get the identical picture for free. */
const TRANS_DUR = 0.45;
const ease = x => 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3);   // ease-out cubic

function activeCaption(segs, t) {
  if (!segs || !segs.length) return null;
  // Linear scan: a clip is seconds long and has a handful of segments, so this
  // is cheaper than the bookkeeping a binary search would need per frame.
  let held = null;
  for (const s of segs) {
    if (t >= s.start && t <= s.end) return s;
    // Segments are in order, so the last one that qualifies here is the most
    // recent cue — that is the one worth holding through a short gap.
    if (s.end < t && t - s.end <= CAP_HOLD_S) held = s;
  }
  return held;
}

/* Canvas filter support, probed once. The blur backdrop does not depend on it
   any more (see blurBackdrop) — where it exists it only smooths the last of
   the upscale, where it does not the picture is still blurred. */
let _CTX_FILTER = null;
function ctxCanFilter(ctx) {
  if (_CTX_FILTER === null) {
    try { ctx.filter = 'blur(2px)'; _CTX_FILTER = ctx.filter !== 'none'; ctx.filter = 'none'; }
    catch { _CTX_FILTER = false; }
  }
  return _CTX_FILTER;
}

/* ── Blur backdrop ───────────────────────────────────────────────────────────
   Two offscreen downscales (1/4, then 1/16 of the output) and one scaled-up
   draw: the upscale's bilinear filtering IS the blur. It costs a fraction of
   ctx.filter over a full 720x1280 frame every tick, which is what the first
   version did during playback AND export, and it works on every canvas —
   filter or not. Where ctx.filter exists a small radius on the tiny canvas
   takes the blockiness out of the upscale. The two canvases are reused
   across frames and resized only when the output shape changes. */
const _BG = [null, null];
function bgCanvas(i, w, h) {
  let c = _BG[i];
  if (!c) { c = document.createElement('canvas'); _BG[i] = c; }
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  return c;
}
function blurBackdrop(ctx, video, w, h, vw, vh, over) {
  const a = bgCanvas(0, Math.max(4, Math.round(w / 4)), Math.max(4, Math.round(h / 4)));
  const b = bgCanvas(1, Math.max(2, Math.round(w / 16)), Math.max(2, Math.round(h / 16)));
  const ac = a.getContext('2d'), bc = b.getContext('2d');
  // Cover the small canvas with an overscanned copy so the softened edges
  // never fade to black inside the frame.
  const s = Math.max(a.width / vw, a.height / vh) * over;
  ac.drawImage(video, (a.width - vw * s) / 2, (a.height - vh * s) / 2, vw * s, vh * s);
  // The one filter pass happens on the 1/16 canvas — a few thousand pixels,
  // not a million. Putting it on the final upscaled draw instead costs a
  // full-frame blur every tick, which is exactly the bill this avoids.
  bc.save();
  if (ctxCanFilter(ctx)) bc.filter = 'blur(1.5px)';
  bc.drawImage(a, 0, 0, b.width, b.height);
  bc.restore();
  ctx.save();
  ctx.imageSmoothingEnabled = true;
  try { ctx.imageSmoothingQuality = 'high'; } catch (e) {}
  const o = 1.08;   // draw past the edges so the softened border stays outside the frame
  ctx.drawImage(b, -w * (o - 1) / 2, -h * (o - 1) / 2, w * o, h * o);
  ctx.restore();
}

/* ── Captions ────────────────────────────────────────────────────────────────
   Short-form style: a heavy sans, white, either a thick dark outline with a
   soft shadow or a dark plate per line, wrapped to the frame, and the word
   being spoken lit in the brand orange when the cue carries word timings.
   Inter is the dashboard's own self-hosted face, so it is the one guaranteed
   to be loaded when a frame is drawn — the first version asked for Sora,
   which this page never loads, and silently drew the fallback. */
const CAP_FONT = 'Inter, system-ui, -apple-system, sans-serif';
const CAP_ACCENT = '#F7A745';

/* ── Templates ───────────────────────────────────────────────────────────────
   The five layouts streamer clips actually ship in, copied from what is on
   TikTok, Shorts and Reels right now, each one a set of the editor's own
   knobs. One click applies it; every knob stays adjustable afterwards, so a
   template is a starting point rather than a lock. `tab` is where the panel
   lands, on the control the template most wants a human to check. */
const SPLIT_TOP = 0.4;   // facecam panel height as a fraction of the frame
const TEMPLATES = [
  { id: 'camgame', name: 'Cam + Game',
    desc: 'Facecam on top, gameplay under it: the streamer-clip standard. Drag the preview to put the window on the camera.',
    tab: 'frame',
    set: { ratio: '9:16', layout: 'split', fill: 'blur', zoom: 2.4, offX: -0.3, offY: -0.2,
           capPos: 'low', capHi: true, capUpper: true, capWord: true, capSize: 0.045,
           transIn: 'zoom', transOut: 'fade', textAnim: true, sfxIn: 'whoosh', sfxOut: 'none' } },
  { id: 'full', name: 'Full Frame',
    desc: 'A centred vertical crop with clean outlined captions. IRL and just-chatting clips.',
    tab: 'trim',
    set: { ratio: '9:16', layout: 'single', fill: 'crop', zoom: 1, offX: 0, offY: 0,
           capPos: 'bottom', capHi: false, capUpper: false, capWord: true, capSize: 0.055,
           transIn: 'fade', transOut: 'fade', textAnim: true, sfxIn: 'none', sfxOut: 'none' } },
  { id: 'blur', name: 'Blur Bars',
    desc: 'The whole 16:9 frame kept, blurred fill above and below, boxed captions under it. Gameplay where the HUD matters.',
    tab: 'captions',
    set: { ratio: '9:16', layout: 'single', fill: 'blur', zoom: 1, offX: 0, offY: 0,
           capPos: 'low', capHi: true, capUpper: true, capWord: true, capSize: 0.05,
           transIn: 'fade', transOut: 'fade', textAnim: true, sfxIn: 'whoosh', sfxOut: 'none' } },
  { id: 'punch', name: 'Punch In',
    desc: 'Zoomed on the reaction with big boxed captions. Reaction and rage clips.',
    tab: 'frame',
    set: { ratio: '9:16', layout: 'single', fill: 'crop', zoom: 1.35, offX: 0, offY: 0,
           capPos: 'bottom', capHi: true, capUpper: true, capWord: true, capSize: 0.07,
           transIn: 'zoom', transOut: 'none', textAnim: true, sfxIn: 'hit', sfxOut: 'none' } },
  { id: 'hook', name: 'Hook Title',
    desc: 'A bold line at the top for the first three seconds of attention, captions below. Type the hook in the Text tab.',
    tab: 'text',
    set: { ratio: '9:16', layout: 'single', fill: 'crop', zoom: 1, offX: 0, offY: 0,
           capPos: 'bottom', capHi: false, capUpper: false, capWord: true, capSize: 0.055,
           textPos: 'top', textSize: 0.09,
           transIn: 'none', transOut: 'fade', textAnim: true, sfxIn: 'pop', sfxOut: 'none' } },
];

function capWrap(ctx, words, maxW) {
  const lines = [];
  let cur = [];
  const width = (arr) => ctx.measureText(arr.map(x => x.w).join(' ')).width;
  for (const wd of words) {
    if (cur.length && width(cur.concat([wd])) > maxW) { lines.push(cur); cur = [wd]; }
    else cur.push(wd);
  }
  if (cur.length) lines.push(cur);
  return lines.slice(-3);
}

/* ── Sound effects, synthesized ──────────────────────────────────────────────
   WHY SYNTHESIZED AND NOT SAMPLE FILES. A sample needs an asset with a licence
   we would have to vouch for, a fetch the dashboard's script rules would have
   to allow, and a file that plays identically in a live AudioContext and in
   the OfflineAudioContext the export renders through. A few oscillators and a
   noise burst need none of that: the same function schedules the same nodes
   whether the destination is the speakers or an offline render, so the
   preview and the exported file carry the identical sound. The noise is
   seeded, so two renders of one clip are byte-identical.

   Each effect is (ctx, dest, at, vol): schedule yourself at time `at` on
   `ctx`, feeding `dest`. `at` is already in the context's clock. */
const SFX_KINDS = [['none', 'None'], ['whoosh', 'Whoosh'], ['hit', 'Hit'], ['pop', 'Pop'], ['riser', 'Riser'], ['ding', 'Ding']];
const _NOISE = typeof WeakMap === 'function' ? new WeakMap() : null;
function _noise(ctx) {
  let b = _NOISE && _NOISE.get(ctx);
  if (b) return b;
  const sr = ctx.sampleRate, n = Math.floor(sr * 1.2);
  b = ctx.createBuffer(1, n, sr);
  const d = b.getChannelData(0);
  let s = 12345;                                  // seeded: deterministic renders
  for (let i = 0; i < n; i++) { s = (s * 1664525 + 1013904223) >>> 0; d[i] = (s / 4294967296) * 2 - 1; }
  if (_NOISE) _NOISE.set(ctx, b);
  return b;
}
function _env(ctx, dest, at, attack, decay, peak) {
  const g = ctx.createGain();
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), at + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, at + attack + decay);
  g.connect(dest);
  return g;
}
const SFX = {
  whoosh(ctx, dest, at, vol) {
    const src = ctx.createBufferSource(); src.buffer = _noise(ctx);
    const f = ctx.createBiquadFilter(); f.type = 'bandpass'; f.Q.value = 1.2;
    f.frequency.setValueAtTime(300, at); f.frequency.exponentialRampToValueAtTime(3200, at + 0.32);
    // 3.6, not ~1: a bandpass on noise sheds most of its energy. Measured
    // offline at 0.9 the whoosh peaked at 0.10 against the hit's 0.60.
    src.connect(f); f.connect(_env(ctx, dest, at, 0.06, 0.34, 3.6 * vol));
    src.start(at); src.stop(at + 0.45);
  },
  hit(ctx, dest, at, vol) {
    const o = ctx.createOscillator(); o.type = 'sine';
    o.frequency.setValueAtTime(140, at); o.frequency.exponentialRampToValueAtTime(38, at + 0.28);
    o.connect(_env(ctx, dest, at, 0.005, 0.3, 1.0 * vol)); o.start(at); o.stop(at + 0.35);
    const n = ctx.createBufferSource(); n.buffer = _noise(ctx);
    const f = ctx.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = 1800;
    n.connect(f); f.connect(_env(ctx, dest, at, 0.003, 0.05, 0.5 * vol)); n.start(at); n.stop(at + 0.08);
  },
  pop(ctx, dest, at, vol) {
    const o = ctx.createOscillator(); o.type = 'sine';
    o.frequency.setValueAtTime(760, at); o.frequency.exponentialRampToValueAtTime(320, at + 0.07);
    o.connect(_env(ctx, dest, at, 0.004, 0.09, 0.8 * vol)); o.start(at); o.stop(at + 0.12);
  },
  riser(ctx, dest, at, vol) {
    // Builds for 0.8s and lands AT `at`; clamped so it never starts in the past.
    const st = Math.max(ctx.currentTime || 0, at - 0.8);
    const src = ctx.createBufferSource(); src.buffer = _noise(ctx);
    const f = ctx.createBiquadFilter(); f.type = 'bandpass'; f.Q.value = 2;
    f.frequency.setValueAtTime(250, st); f.frequency.exponentialRampToValueAtTime(4200, at);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, st); g.gain.exponentialRampToValueAtTime(1.6 * vol, at);   // same bandpass loss as the whoosh
    g.gain.exponentialRampToValueAtTime(0.0001, at + 0.06); g.connect(dest);
    src.connect(f); f.connect(g); src.start(st); src.stop(at + 0.1);
  },
  ding(ctx, dest, at, vol) {
    [[1320, 0.6], [2640, 0.25]].forEach(([hz, a]) => {
      const o = ctx.createOscillator(); o.type = 'sine'; o.frequency.value = hz;
      o.connect(_env(ctx, dest, at, 0.004, 0.6, a * vol)); o.start(at); o.stop(at + 0.7);
    });
  },
};
// A plan is [{kind, at, gain}] with `at` in seconds from the cut's start.
// `base` is the context time the cut's start corresponds to.
function scheduleSfx(ctx, dest, plan, base) {
  (plan || []).forEach(p => {
    const f = SFX[p.kind];
    if (!f) return;
    try { f(ctx, dest, Math.max(ctx.currentTime || 0, base + p.at), p.gain == null ? 0.6 : p.gain); } catch (e) {}
  });
}
// The editor's two choices as a plan: one sound where the cut starts, one
// landing just before it ends.
function sfxPlanFor(span, sfxIn, sfxOut, gain) {
  const plan = [];
  if (sfxIn && sfxIn !== 'none') plan.push({ kind: sfxIn, at: 0, gain });
  if (sfxOut && sfxOut !== 'none') plan.push({ kind: sfxOut, at: Math.max(0, span - 0.35), gain });
  return plan;
}

function drawCaption(ctx, cue, o) {
  const { w, h } = o;
  const fs = Math.round(h * (o.capSize || 0.055));
  const t = o.t || 0;
  // Words with an on/off flag. A cue without timings is one line of words
  // that are never "on"; the layout is the same either way.
  let words;
  if (cue.words && cue.words.length) {
    let idx = -1;
    cue.words.forEach((x, i) => { if (t >= x[0] - 0.05) idx = i; });
    words = cue.words.map((x, i) => ({ w: x[2], on: o.capWord && i === idx, st: x[0] }));
  } else {
    words = String(cue.text || '').split(' ').filter(Boolean).map(x => ({ w: x, on: false }));
  }
  if (!words.length) return;
  if (o.capUpper) words = words.map(x => ({ w: x.w.toUpperCase(), on: x.on }));
  ctx.font = `800 ${fs}px ${CAP_FONT}`;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  const lines = capWrap(ctx, words, w * 0.86);
  const capPos = o.capPos || 'bottom';
  // 0.78 keeps captions clear of the platform's own bottom chrome; 'low'
  // (0.88) is for people who want them under the action, and 'top' for when
  // the interesting part of the frame is at the bottom.
  const baseY = h * (capPos === 'top' ? 0.16 : capPos === 'middle' ? 0.5
                   : capPos === 'low' ? 0.88 : 0.78);
  const lh = fs * 1.22;
  const space = ctx.measureText(' ').width;
  lines.forEach((ln, i) => {
    const ly = baseY + (i - (lines.length - 1) / 2) * lh;
    const widths = ln.map(x => ctx.measureText(x.w).width);
    const lineW = widths.reduce((a, b) => a + b, 0) + space * (ln.length - 1);
    let x = (w - lineW) / 2;
    if (o.capHighlight) {
      // A solid plate behind the words. Reads on any background, where a
      // stroke alone can still disappear into busy gameplay.
      const padX = fs * 0.36, padY = fs * 0.2;
      const rx = x - padX, ry = ly - fs * 0.6 - padY, rw = lineW + padX * 2, rh = fs * 1.2 + padY * 2;
      ctx.fillStyle = 'rgba(0,0,0,.74)';
      if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(rx, ry, rw, rh, fs * 0.24); ctx.fill(); }
      else ctx.fillRect(rx, ry, rw, rh);
    }
    ln.forEach((wd, j) => {
      // Word pop: the word that just lit up lands from 1.14x to 1x over 90 ms.
      // Scaled about its own centre so the line does not shift. Needs the
      // word's start time, which only timed cues carry.
      const pop = (wd.on && o.capPop !== false && wd.st != null) ? Math.max(0, 1 - (t - wd.st) / 0.09) : 0;
      ctx.save();
      if (pop > 0) {
        const cx = x + widths[j] / 2, s = 1 + 0.14 * pop;
        ctx.translate(cx, ly); ctx.scale(s, s); ctx.translate(-cx, -ly);
      }
      if (!o.capHighlight) {
        // Outline plus a soft shadow: the outline holds the letterforms, the
        // shadow lifts them off a bright frame where a stroke alone looks thin.
        ctx.save();
        ctx.shadowColor = 'rgba(0,0,0,.55)';
        ctx.shadowBlur = fs * 0.28;
        ctx.shadowOffsetY = fs * 0.06;
        ctx.lineWidth = Math.max(2, fs * 0.19);
        ctx.strokeStyle = 'rgba(0,0,0,.92)';
        ctx.lineJoin = 'round';
        ctx.strokeText(wd.w, x, ly);
        ctx.restore();
      }
      ctx.fillStyle = wd.on ? CAP_ACCENT : '#fff';
      ctx.fillText(wd.w, x, ly);
      ctx.restore();
      x += widths[j] + space;
    });
  });
}

function paintFrame(ctx, video, o) {
  const { w, h, zoom, offX, offY, text, textSize, textPos, caption } = o;
  const fill = o.fill || 'crop';
  // Every crop scales the source: a 16:9 frame reframed to 9:16 is drawn at
  // 1.78x, and the default resampler for that is bilinear. 'high' costs
  // nothing measurable per frame and is the difference between a soft
  // upscale and a clean one.
  ctx.imageSmoothingEnabled = true;
  try { ctx.imageSmoothingQuality = 'high'; } catch (e) {}
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, w, h);

  const vw = video.videoWidth || 16, vh = video.videoHeight || 9;
  const layout = o.layout || 'single';

  // Time from the cut's start and to its end. Both are Infinity when the
  // caller gave no cut, which disables every transition below without a
  // special case anywhere.
  const tin  = (o.inPt  != null && o.t != null) ? (o.t - o.inPt)  : Infinity;
  const tout = (o.outPt != null && o.t != null) ? (o.outPt - o.t) : Infinity;
  // Zoom punch: the whole picture settles from 1.12x to 1x over TRANS_DUR.
  // A transform around the video block, so every layout gets it the same way.
  const punch = (o.transIn === 'zoom' && tin < TRANS_DUR) ? 1 + 0.12 * (1 - ease(tin / TRANS_DUR)) : 1;
  ctx.save();
  if (punch !== 1) { ctx.translate(w / 2, h / 2); ctx.scale(punch, punch); ctx.translate(-w / 2, -h / 2); }

  if (layout === 'split') {
    // Facecam on top, gameplay under it: the streamer-clip standard. The top
    // panel is a window into the source, cut around the camera (zoom is the
    // window's tightness, offX/offY where it sits); the bottom keeps the
    // whole frame at full width. Blurred fill behind both so nothing is
    // ever a hard black bar.
    blurBackdrop(ctx, video, w, h, vw, vh, 1.12);
    ctx.fillStyle = 'rgba(0,0,0,.35)';
    ctx.fillRect(0, 0, w, h);
    const topH = Math.round(h * SPLIT_TOP);
    let rw = vw / Math.max(1, zoom), rh = rw * topH / w;
    if (rh > vh) { rh = vh; rw = rh * w / topH; }
    const sx = Math.max(0, Math.min(vw - rw, (0.5 + offX) * vw - rw / 2));
    const sy = Math.max(0, Math.min(vh - rh, (0.5 + offY) * vh - rh / 2));
    ctx.drawImage(video, sx, sy, rw, rh, 0, 0, w, topH);
    const bh = h - topH, gs = Math.min(w / vw, bh / vh), gw = vw * gs, gh = vh * gs;
    ctx.drawImage(video, (w - gw) / 2, topH + (bh - gh) / 2, gw, gh);
    ctx.fillStyle = 'rgba(0,0,0,.5)';
    ctx.fillRect(0, topH - 2, w, 4);                 // the seam
  } else if (fill === 'blur') {
    // Contain the video and put a blurred, over-scaled copy behind it. Nothing
    // is cropped off the sides, which is the point — a 16:9 clip forced into
    // 9:16 by cover loses most of the frame. The backdrop is a cover-scaled
    // copy (Math.max(w / vw, h / vh)) drawn through the downscale pipeline.
    blurBackdrop(ctx, video, w, h, vw, vh, 1.12);
    ctx.fillStyle = 'rgba(0,0,0,.28)';             // hold the foreground forward
    ctx.fillRect(0, 0, w, h);
    const cs = Math.min(w / vw, h / vh) * zoom;
    const cw = vw * cs, ch = vh * cs;
    const cx = (w - cw) / 2 + offX * w, cy = (h - ch) / 2 + offY * h;
    // A soft shadow under the picture separates it from a backdrop that is
    // made of the same colours. Drawn as a rect, which is cheap; the video
    // covers it.
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,.6)';
    ctx.shadowBlur = Math.round(w * 0.04);
    ctx.shadowOffsetY = Math.round(w * 0.01);
    ctx.fillStyle = '#000';
    ctx.fillRect(cx, cy, cw, ch);
    ctx.restore();
    ctx.drawImage(video, cx, cy, cw, ch);
  } else {
    // Cover: fill the frame, crop the overflow. Letterboxing a vertical export
    // would defeat the point of reframing for a phone screen.
    const scale = Math.max(w / vw, h / vh) * zoom;
    const dw = vw * scale, dh = vh * scale;
    ctx.drawImage(video, (w - dw) / 2 + offX * w, (h - dh) / 2 + offY * h, dw, dh);
  }
  ctx.restore();                              // end of the zoom-punch transform

  // Auto-caption first, so a manual title drawn at the same spot sits on top
  // rather than being hidden behind it.
  if (caption) drawCaption(ctx, caption, o);

  if (text) {
    const fs = Math.round(h * textSize);
    ctx.font = `900 ${fs}px ${CAP_FONT}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const y = textPos === 'top' ? h * 0.12 : textPos === 'middle' ? h * 0.5 : h * 0.88;
    // The title rises into place over the first 0.35s and fades in with it.
    // Off with textAnim:false; off automatically when there is no cut.
    const rise = (o.textAnim !== false && tin < 0.35) ? 1 - ease(tin / 0.35) : 0;
    // NOTE: this whole script lives inside a Python triple-quoted string, so a
    // bare backslash-n here would be parsed by PYTHON into a real newline and
    // break the JS string literal. Split on a character code instead — no
    // escape, nothing for Python to eat.
    const lines = String(text).split(String.fromCharCode(10)).slice(0, 3);
    ctx.save();
    if (rise > 0) ctx.globalAlpha = 1 - rise;
    lines.forEach((ln, i) => {
      const ly = y + (i - (lines.length - 1) / 2) * fs * 1.15 + rise * fs * 1.2;
      ctx.save();
      ctx.shadowColor = 'rgba(0,0,0,.5)';
      ctx.shadowBlur = fs * 0.25;
      ctx.lineWidth = Math.max(2, fs * 0.16);
      ctx.strokeStyle = 'rgba(0,0,0,.85)';
      ctx.lineJoin = 'round';
      ctx.strokeText(ln, w / 2, ly);      // outline first, so text reads on any background
      ctx.restore();
      ctx.fillStyle = '#fff';
      ctx.fillText(ln, w / 2, ly);
    });
    ctx.restore();
  }

  // Fades last, over everything: a fade to black that left the captions lit
  // would look like a bug, not a transition.
  const fadeIn  = (o.transIn  === 'fade' && tin  < TRANS_DUR) ? 1 - ease(tin  / TRANS_DUR) : 0;
  const fadeOut = (o.transOut === 'fade' && tout < TRANS_DUR) ? 1 - ease(tout / TRANS_DUR) : 0;
  const fade = Math.max(fadeIn, fadeOut);
  if (fade > 0.001) {
    ctx.fillStyle = 'rgba(0,0,0,' + fade.toFixed(3) + ')';
    ctx.fillRect(0, 0, w, h);
  }
}

/* Mirrors src/publish/platforms.py check_fit. Only the COMPARISON is here —
   every number comes from the spec the server sent, so the limits cannot drift
   between the two. Ordered worst-first: a hard rejection matters more than
   losing Shorts eligibility, which matters more than a crop. */
/* Epoch seconds -> the user's own local time. Times are stored UTC precisely
   so this conversion happens once, here, in the browser that knows the zone. */
/* Epoch seconds -> the value a datetime-local input expects, which is LOCAL
   wall-clock with no zone. toISOString() would hand it UTC and silently shift
   every displayed time by the user's offset. */
function toLocalInput(ts) {
  const d = new Date(ts * 1000);
  const p = n => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth()+1) + '-' + p(d.getDate())
       + 'T' + p(d.getHours()) + ':' + p(d.getMinutes());
}

function qWhen(ts) {
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const t = d.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
  return sameDay ? t : d.toLocaleDateString([], {month:'short', day:'numeric'}) + ' ' + t;
}

function fitIssues(pf, secs, ratio, caption, fmt) {
  const out = [];
  // Format first: a hard refusal at the upload page, and trimming cannot fix
  // it. MediaRecorder falls back to WebM on browsers with no H.264 encoder.
  const f = String(fmt||'').toLowerCase().replace(/^[.]/,'');
  if (f && (pf.formats||[]).indexOf(f) === -1)
    out.push(pf.label + ' will not accept a .' + f + ' file — it needs ' +
             (pf.formats||[]).map(x=>'.'+x).join(' or ') +
             '. Your browser could not make MP4; try Chrome, Edge or Safari.');
  if (secs > pf.hard_max_s)
    out.push(Math.round(secs) + 's is over ' + pf.label + "'s " +
             Math.round(pf.hard_max_s) + 's limit — it will be rejected.');
  else if (secs > pf.ideal_max_s)
    out.push(pf.id === 'youtube'
      ? Math.round(secs) + 's is over the ' + Math.round(pf.ideal_max_s) +
        's Shorts cutoff — posts as a normal video, not a Short.'
      : Math.round(secs) + 's is over ' + Math.round(pf.ideal_max_s) +
        's, where ' + pf.label + ' reach usually drops off.');
  if (ratio && ratio !== pf.preferred_ratio)
    out.push(pf.label + ' expects ' + pf.preferred_ratio + '; ' + ratio +
             ' gets cropped or letterboxed.');
  if (caption && caption.length > pf.caption_max)
    out.push('Caption is ' + caption.length + ' characters; ' + pf.label +
             ' allows ' + pf.caption_max + '.');
  return out;
}

/* ── Thumbnails for the timeline ─────────────────────────────────────────────
   A second, silent video element seeks through the clip and paints one small
   frame per slot. Its own element rather than the editor's: seeking the
   preview video to build a filmstrip would visibly scrub the stage. Delivered
   in batches so the strip fills in as it is built rather than appearing all at
   once several seconds later. */
const THUMB_N = 16;

function buildThumbs(url, dur, onBatch, isGone) {
  const tv = document.createElement('video');
  tv.muted = true; tv.preload = 'auto'; tv.crossOrigin = 'anonymous'; tv.playsInline = true;
  tv.src = url;
  const c = document.createElement('canvas');
  c.width = 128; c.height = 72;
  const ctx = c.getContext('2d');
  const out = new Array(THUMB_N).fill('');
  const seekTo = (t) => new Promise(res => {
    let done = false;
    const fin = () => { if (done) return; done = true; tv.removeEventListener('seeked', fin); res(); };
    tv.addEventListener('seeked', fin);
    setTimeout(fin, 1500);                    // never hang the strip on a lost event
    tv.currentTime = t;
  });
  const run = async () => {
    await new Promise(res => {
      if (tv.readyState >= 1) return res();
      tv.addEventListener('loadedmetadata', res, { once: true });
      tv.addEventListener('error', res, { once: true });
      setTimeout(res, 4000);
    });
    for (let i = 0; i < THUMB_N; i++) {
      if (isGone()) break;
      await seekTo(Math.min(dur - 0.05, (i + 0.5) / THUMB_N * dur));
      try {
        const vw = tv.videoWidth || 16, vh = tv.videoHeight || 9;
        const s = Math.max(c.width / vw, c.height / vh);
        ctx.fillStyle = '#000'; ctx.fillRect(0, 0, c.width, c.height);
        ctx.drawImage(tv, (c.width - vw * s) / 2, (c.height - vh * s) / 2, vw * s, vh * s);
        out[i] = c.toDataURL('image/jpeg', 0.6);
      } catch (e) { break; }               // a tainted canvas: leave the strip plain
      if (i % 4 === 3 || i === THUMB_N - 1) onBatch(out.slice());
    }
    tv.removeAttribute('src'); try { tv.load(); } catch (e) {}
  };
  run();
}

/* Output size per shape: 1080x1920, 1080x1080 or 1920x1080 for any source of
   720p and up, the 720 class only below that. This DOES upscale a 720p clip,
   on purpose: TikTok, Shorts and Reels re-encode every upload to 1080x1920
   with their own scaler, and a 720x1280 file handed to that pipeline comes
   out visibly softer than the same picture delivered at 1080x1920 — the
   scaling happens either way; doing it here with a high-quality resampler
   keeps it out of theirs. Even dimensions, always — H.264 refuses odd ones. */
function outputSize(ratio, vw, vh) {
  const aspect = (RATIOS.find(r => r[0] === ratio) || RATIOS[0])[1];
  const srcShort = Math.min(vw || 1080, vh || 1920);
  const hd = srcShort >= 720;
  let w, h;
  if (aspect < 1)       { h = hd ? 1920 : 1280; w = h * aspect; }
  else if (aspect === 1){ h = hd ? 1080 : 720;  w = h; }
  else                  { h = hd ? 1080 : 720;  w = h * aspect; }
  return { w: Math.round(w / 2) * 2, h: Math.round(h / 2) * 2, hd };
}

/* ── Timeline ────────────────────────────────────────────────────────────────
   One pointer model for everything on the strip: press on a handle drags that
   cut point, press on the playhead scrubs, press anywhere else jumps there and
   then scrubs. Pointer capture keeps a drag alive when the finger leaves the
   strip, which on a phone is every drag. The element never re-renders during
   a drag — positions are written straight to the DOM from the pointer events
   and React catches up when the pointer lifts. */
function EdTimeline({ dur, inPt, outPt, thumbs, headRef, disabled, onIn, onOut, onSeek, onDragState }) {
  const ref = useRef(null);
  const drag = useRef(null);
  const pct = (t) => (dur ? Math.max(0, Math.min(1, t / dur)) * 100 : 0);
  const timeAt = (clientX) => {
    const r = ref.current.getBoundingClientRect();
    return Math.max(0, Math.min(dur, (clientX - r.left) / r.width * dur));
  };
  const down = (e) => {
    if (disabled || !dur) return;
    const kind = e.target.dataset.h || 'seek';
    drag.current = kind;
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch (x) {}
    onDragState(true);
    move(e);
    e.preventDefault();
  };
  const move = (e) => {
    if (!drag.current) return;
    const t = timeAt(e.clientX);
    if (drag.current === 'in') onIn(t);
    else if (drag.current === 'out') onOut(t);
    else onSeek(t);
  };
  const up = (e) => {
    if (!drag.current) return;
    drag.current = null;
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch (x) {}
    onDragState(false);
  };
  return (
    <div className={'ed-tl' + (disabled ? ' off' : '')} ref={ref}
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
      <div className="ed-film" aria-hidden="true">
        {thumbs.map((src, i) => src
          ? <img key={i} src={src} alt="" draggable="false"/>
          : <span key={i}/>)}
      </div>
      <div className="ed-dim l" style={{width: pct(inPt) + '%'}}/>
      <div className="ed-dim r" style={{width: (100 - pct(outPt)) + '%'}}/>
      <div className="ed-sel" style={{left: pct(inPt) + '%', width: (pct(outPt) - pct(inPt)) + '%'}}/>
      <div className="ed-hd l" data-h="in" style={{left: pct(inPt) + '%'}} title="Drag to set the start"><i data-h="in"/></div>
      <div className="ed-hd r" data-h="out" style={{left: pct(outPt) + '%'}} title="Drag to set the end"><i data-h="out"/></div>
      <div className="ed-ph" ref={headRef} data-h="head" style={{left: 0}}><b data-h="head"/></div>
    </div>
  );
}

function ClipEditor({ clip, onClose, onExported, captionsOn = false, platforms = [], schedulerOn = false }) {
  // captionsOn is the RELEASE flag, not a plan gate. With it false the panel is
  // hidden entirely rather than rendered as a button that 503s on every click —
  // a visible control that always fails is the Kick-tab mistake again, and this
  // one shipped to paying users while CAPTIONS_ENABLED was unset on prod.
  // The editor paints frames into .ed-stage's canvas, and it sits inside
  // .ed-bg, which blurs the whole viewport. Same cost as a player, so it
  // counts as one for as long as the editor is open.
  usePlayerOpen(true);
  const [dur, setDur]       = useState(0);
  const [srcDims, setDims]  = useState([0, 0]);
  const [inPt, setIn]       = useState(0);
  const [outPt, setOut]     = useState(0);
  const [ratio, setRatio]   = useState('9:16');
  const [zoom, setZoom]     = useState(1);
  const [offX, setOffX]     = useState(0);
  const [offY, setOffY]     = useState(0);
  const [text, setText]     = useState('');
  const [textSize, setTS]   = useState(0.075);
  const [textPos, setTP]    = useState('bottom');
  const [fill, setFill]     = useState('crop');
  const [layout, setLayout] = useState('single');
  const [tpl, setTpl]       = useState('');
  const [capSize, setCapSize] = useState(0.055);
  const [capPos, setCapPos]   = useState('bottom');
  const [capHi, setCapHi]     = useState(false);
  const [capUpper, setCapUpper] = useState(false);
  const [capWord, setCapWord]   = useState(true);
  // Effects: small transitions and synthesized sound effects. Templates set
  // these too; the Effects tab exposes them.
  const [transIn, setTransIn]   = useState('none');     // 'none' | 'fade' | 'zoom'
  const [transOut, setTransOut] = useState('none');     // 'none' | 'fade'
  const [textAnim, setTextAnim] = useState(true);
  const [sfxIn, setSfxIn]       = useState('none');
  const [sfxOut, setSfxOut]     = useState('none');
  const [sfxGain, setSfxGain]   = useState(0.6);
  const sfxCtxRef = useRef(null);   // preview sound when the clip's own graph is unavailable
  const [playing, setPlay]  = useState(false);
  const [busy, setBusy]     = useState(false);
  const [pct, setPct]       = useState(0);
  const [err, setErr]       = useState('');
  const [done, setDone]     = useState('');
  const [tab, setTab]       = useState('trim');
  const [thumbs, setThumbs] = useState(() => new Array(THUMB_N).fill(''));
  const [hint, setHint]     = useState(true);

  // The exported file is KEPT, not just downloaded. Handing it to the native
  // share sheet is the whole "post to TikTok/IG/YouTube" story: one tap on a
  // phone, into the real app, with no OAuth and no platform app-review. Dropping
  // the blob after download would force a re-export to share.
  const [outFile, setOutFile] = useState(null);  // {blob, ext, name}

  const [caps, setCaps]     = useState(null);   // [{start,end,text}]
  const [capOn, setCapOn]   = useState(true);
  const [capJob, setCapJob] = useState(null);   // {status,pct} while running
  const [capErr, setCapErr] = useState('');

  const videoRef = useRef(null);
  const canvRef  = useRef(null);
  const stageRef = useRef(null);
  const rootRef  = useRef(null);
  const rafRef   = useRef(0);
  const cancelRef = useRef(false);
  // The playhead and the clock are driven from the animation loop, NOT from
  // render. The loop paints the canvas imperatively and changes no state, so
  // React does not re-render while the video plays — anything positioned from
  // `videoRef.current.currentTime` during render is frozen at wherever it was
  // when the last state change happened. Calling setState 60x/second instead
  // would re-render the whole editor every frame, which is the wrong trade.
  const headRef  = useRef(null);
  const clockRef = useRef(null);
  // The loop reads everything through ONE ref that render refreshes, so it is
  // registered once and never sees a stale closure. `dirty` is the other half
  // of that design: a paused editor paints only when something changed, not
  // sixty times a second, so a phone does not cook while someone reads the
  // caption settings.
  const latest   = useRef({});
  const dirtyRef = useRef(true);
  const dragging = useRef(false);
  const pendingSeek = useRef(null);
  const pinch = useRef(null);

  const out = outputSize(ratio, srcDims[0], srcDims[1]);
  const outW = out.w, outH = out.h;

  const opts = () => ({ w: outW, h: outH, zoom, offX, offY, text, textSize, textPos,
    fill, layout, capSize, capPos, capHighlight: capHi, capUpper, capWord,
    // Transitions are functions of where `t` sits inside the cut.
    inPt, outPt, transIn, transOut, textAnim, capPop: true,
    t: videoRef.current ? videoRef.current.currentTime : 0,
    caption: capOn ? activeCaption(caps, videoRef.current ? videoRef.current.currentTime : 0) : null });

  // The sound plan for this cut, and how the PREVIEW plays it: scheduled on
  // the clip's own audio graph (so it mixes with the clip and is what a
  // recorder export records), or on a spare context when the clip's audio
  // cannot be routed. `fromMediaTime` is where playback is right now, so the
  // plan lands at the right offsets whether play started at the in-point or
  // mid-cut; sounds already behind that point are simply not scheduled.
  const sfxPlan = sfxPlanFor(Math.max(0.1, outPt - inPt), sfxIn, sfxOut, sfxGain);
  const fireSfx = (fromMediaTime) => {
    const plan = latest.current.sfx;
    if (!plan || !plan.length) return;
    const v = videoRef.current;
    const g = v ? audioGraph(v, clip.url) : null;
    let ctx, dest;
    if (g) { ctx = g.ctx; dest = g.sfx; }
    else {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!sfxCtxRef.current) sfxCtxRef.current = new AC();
      ctx = sfxCtxRef.current; dest = ctx.destination;
    }
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    const base = ctx.currentTime - (fromMediaTime - latest.current.inPt);
    scheduleSfx(ctx, dest, plan.filter(p => base + p.at >= ctx.currentTime - 0.02), base);
  };

  latest.current = { opts, dur, inPt, outPt, playing, busy, outW, outH, layout, zoom, sfx: sfxPlan, fireSfx };
  // Anything that changes the picture marks the frame dirty. Cheaper than
  // diffing: the loop paints once and clears it.
  useEffect(() => { dirtyRef.current = true; },
    [outW, outH, zoom, offX, offY, text, textSize, textPos, fill, layout, capSize, capPos, capHi, capUpper, capWord, capOn, caps, inPt, outPt]);

  // The caption and title faces must be resident before the first paint and
  // before an export starts, or the first frames go out in the fallback font.
  useEffect(() => {
    if (!(document.fonts && document.fonts.load)) return;
    Promise.all([document.fonts.load('800 40px Inter'), document.fonts.load('900 40px Inter')])
      .then(() => { dirtyRef.current = true; }).catch(() => {});
  }, []);

  // Existing captions on open, plus live progress for a run started in another
  // tab — transcription happens on the server, so it is not tied to this one.
  //
  // This ALSO re-syncs on every WebSocket reconnect (hz_refetch), and it has to.
  // A deploy restarts the server: the asyncio task running the transcription
  // dies, the in-memory job record goes with it, and captions_ready is never
  // sent because there is nobody left to send it. Without this listener the
  // panel sat on "Transcribing... 40%" forever and the only way out was a
  // manual page refresh — which is exactly what the realtime rule forbids.
  useEffect(()=>{
    let gone = false;
    const load = ()=>{
      fetch('/uploads/'+clip.id+'/captions').then(r=>r.ok?r.json():null).then(d=>{
        if(gone||!d) return;
        if(d.captions) setCaps(d.captions.segments||[]);
        const live = d.job && d.job.status==='running';
        if(live) setCapJob(d.job);
        else setCapJob(prev=>{
          if(!prev) return prev;
          // The server says nothing is running. Believe it only if our own job
          // has been up long enough that the POST must have registered — a
          // reconnect landing in the gap between the optimistic setCapJob and
          // the request arriving would otherwise cancel a perfectly good job.
          if(Date.now() - (prev.startedAt||0) < 6000) return prev;
          setCapErr('Captioning stopped — the server restarted. Press Generate to run it again.');
          return null;
        });
      }).catch(()=>{});
    };
    load();
    const onWs = e=>{
      try{
        const m = JSON.parse(e.detail);
        if(m.upload_id !== clip.id) return;
        if(m.event==='captions_progress') setCapJob(p=>({status:'running',pct:m.pct,startedAt:(p&&p.startedAt)||Date.now()}));
        else if(m.event==='captions_ready'){
          setCaps((m.captions&&m.captions.segments)||[]); setCapJob(null); setCapErr('');
        }
        else if(m.event==='captions_failed'){ setCapJob(null); setCapErr(m.message||'Captioning failed'); }
      }catch{}
    };
    window.addEventListener('hz_ws', onWs);
    window.addEventListener('hz_refetch', load);
    return ()=>{
      gone=true;
      window.removeEventListener('hz_ws', onWs);
      window.removeEventListener('hz_refetch', load);
    };
  },[clip.id]);

  const makeCaptions = async () => {
    setCapErr(''); setCapJob({status:'running',pct:0,startedAt:Date.now()});
    try{
      const r = await fetch('/uploads/'+clip.id+'/captions',{method:'POST'});
      if(!r.ok){
        let d='Could not start captioning';
        try{ d=(await r.json()).detail||d; }catch{}
        setCapErr(d); setCapJob(null);
      }
    }catch{ setCapErr('Could not reach the server'); setCapJob(null); }
  };

  // The stage's size in device pixels, kept current by a ResizeObserver so
  // the paint loop never has to read layout on a tick.
  const stageSizeRef = useRef([0, 0]);
  useEffect(() => {
    const el = stageRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const measure = () => {
      const r = el.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      stageSizeRef.current = [Math.round(r.width * dpr), Math.round(r.height * dpr)];
      dirtyRef.current = true;
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ── The paint loop: registered once, reads `latest`. ──
  useEffect(() => {
    const draw = () => {
      const v = videoRef.current, c = canvRef.current;
      const L = latest.current;
      if (v && c && L.opts) {
        // THE PREVIEW IS PAINTED AT THE SIZE IT IS SHOWN, not at the export
        // size. paintFrame draws everything relative to w/h, so a stage 420
        // CSS px tall on a 2x display gets an 840px-tall bitmap rather than a
        // 1920px one — measured identical on screen (Laplacian variance 1374
        // vs 1376 on the same frame) at about a fifth of the pixels per tick.
        // That fifth is what makes scrubbing and playback smooth on a laptop
        // GPU or a phone, where painting two megapixels per frame plus the
        // blur pipeline was the stutter. Never larger than the output, so a
        // huge monitor does not upscale the picture past what export gets.
        //
        // An export paints at the real output size: exportRecorder sizes the
        // canvas itself before it starts recording, and `busy` holds it there.
        // Never resize mid-export: assigning canvas.width resets the surface
        // and invalidates the MediaRecorder capture track, so a shape change
        // landing during a render would truncate the file.
        let tw = L.outW, th = L.outH;
        if (!L.busy) {
          const [sw, sh] = stageSizeRef.current;
          if (sw > 0 && sh > 0) {
            const s = Math.min(1, sw / L.outW, sh / L.outH);
            tw = Math.max(2, Math.round(L.outW * s / 2) * 2);
            th = Math.max(2, Math.round(L.outH * s / 2) * 2);
          }
        }
        if ((c.width !== tw || c.height !== th) && !L.busy) {
          c.width = tw; c.height = th; dirtyRef.current = true;
        }
        const t = v.currentTime;
        const live = L.playing || L.busy || !v.paused;
        // Painting a paused frame again is wasted work; a busy export must
        // paint every tick so captureStream has a fresh frame to record.
        if (dirtyRef.current || live) {
          if (v.readyState >= 2) {
            const o = L.opts();
            paintFrame(c.getContext('2d'), v, c.width === o.w && c.height === o.h ? o : { ...o, w: c.width, h: c.height });
          }
          dirtyRef.current = false;
        }
        if (headRef.current)
          headRef.current.style.left = (L.dur ? Math.min(t, L.dur) / L.dur * 100 : 0) + '%';
        if (clockRef.current) {
          const s = edTime(t);
          if (clockRef.current.textContent !== s) clockRef.current.textContent = s;
        }
        // Preview loops inside the cut. Checking a trim means watching the
        // ends, and a preview that stops dead at the out-point makes you
        // press play again for every look.
        if (L.playing && !L.busy && t >= L.outPt - 0.02) {
          v.currentTime = L.inPt;
          if (L.fireSfx) L.fireSfx(L.inPt);         // the loop restarts the sounds too
        }
      }
      rafRef.current = requestAnimationFrame(draw);
    };
    rafRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(rafRef.current);
  }, []);

  // A seek lands a new decoded frame AFTER currentTime changes; without this
  // a scrub painted the previous frame and looked a step behind the finger.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const mark = () => {
      dirtyRef.current = true;
      // A scrub queues at most one seek at a time — the next target waits
      // for this one to land, so the decoder is never flooded.
      if (pendingSeek.current != null && !v.seeking) {
        const t = pendingSeek.current; pendingSeek.current = null; v.currentTime = t;
      }
    };
    v.addEventListener('seeked', mark);
    v.addEventListener('loadeddata', mark);
    v.addEventListener('pause', mark);
    return () => { v.removeEventListener('seeked', mark); v.removeEventListener('loadeddata', mark); v.removeEventListener('pause', mark); };
  }, []);

  const onMeta = () => {
    const v = videoRef.current;
    if (!v) return;
    const settle = (d) => {
      setDur(d); setIn(0); setOut(d); setDims([v.videoWidth || 0, v.videoHeight || 0]);
      v.currentTime = 0; dirtyRef.current = true;
    };
    if (isFinite(v.duration) && v.duration > 0) { settle(v.duration); return; }
    // A WebM written by MediaRecorder carries NO duration in its header, so
    // the browser reports Infinity until it has scanned the file. Bailing here
    // (the first version did) leaves the editor with a 0s clip, a black canvas
    // and an empty export — and browser-recorded WebM is exactly the kind of
    // file a user uploads. Seeking far past the end forces the scan.
    const onSeek = () => {
      v.removeEventListener('timeupdate', onSeek);
      settle(isFinite(v.duration) && v.duration > 0 ? v.duration : 0);
    };
    v.addEventListener('timeupdate', onSeek);
    v.currentTime = 1e101;
  };

  // Filmstrip, once the length is known.
  useEffect(() => {
    if (!dur) return;
    let gone = false;
    buildThumbs(clip.url, dur, t => { if (!gone) setThumbs(t); }, () => gone);
    return () => { gone = true; };
  }, [dur, clip.url]);

  const seek = (t, clampToCut) => {
    const v = videoRef.current;
    if (!v) return;
    const L = latest.current;
    let x = Math.max(0, Math.min(L.dur || 0, t));
    if (clampToCut) x = Math.max(L.inPt, Math.min(L.outPt, x));
    if (v.seeking) pendingSeek.current = x; else v.currentTime = x;
  };

  const pause = () => { const v = videoRef.current; if (v) v.pause(); setPlay(false); };
  const play = () => {
    const v = videoRef.current;
    if (!v) return;
    const L = latest.current;
    if (v.currentTime < L.inPt || v.currentTime >= L.outPt - 0.02) v.currentTime = L.inPt;
    v.play().catch(() => {});
    if (L.fireSfx) L.fireSfx(v.currentTime);
    setPlay(true);
  };
  const togglePlay = () => { if (busy) return; latest.current.playing ? pause() : play(); };

  const setInAt = (t) => {
    const L = latest.current;
    const x = Math.max(0, Math.min(t, L.outPt - 0.3));
    setIn(x); seek(x);
  };
  const setOutAt = (t) => {
    const L = latest.current;
    const x = Math.min(L.dur, Math.max(t, L.inPt + 0.3));
    setOut(x); seek(x);
  };
  const step = (secs) => { const v = videoRef.current; if (v) seek(v.currentTime + secs); };
  const onDragState = (on) => {
    dragging.current = on;
    if (on && latest.current.playing) pause();
  };

  // Keyboard: the shortcuts every cutting tool shares. Ignored while typing
  // in a field and while an export is running.
  useEffect(() => {
    const onKey = (e) => {
      const tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      const L = latest.current;
      if (e.key === 'Escape') { if (!L.busy) onClose(); return; }
      if (L.busy) return;
      const v = videoRef.current;
      if (e.key === ' ' || e.key === 'k') { e.preventDefault(); togglePlay(); }
      else if (e.key === 'ArrowLeft')  { e.preventDefault(); step(e.shiftKey ? -1 : -1 / 30); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); step(e.shiftKey ? 1 : 1 / 30); }
      else if (e.key === 'i' || e.key === 'I') { if (v) setInAt(v.currentTime); }
      else if (e.key === 'o' || e.key === 'O') { if (v) setOutAt(v.currentTime); }
      else if (e.key === 'Home') { e.preventDefault(); seek(L.inPt); }
      else if (e.key === 'End')  { e.preventDefault(); seek(L.outPt - 0.05); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => { if (rootRef.current) rootRef.current.focus(); }, []);

  const textRef = useRef(null);
  const wantText = useRef(false);
  const SETTERS = { ratio: setRatio, layout: setLayout, fill: setFill, zoom: setZoom, offX: setOffX, offY: setOffY,
                    capPos: setCapPos, capHi: setCapHi, capUpper: setCapUpper, capWord: setCapWord, capSize: setCapSize,
                    textPos: setTP, textSize: setTS,
                    transIn: setTransIn, transOut: setTransOut, textAnim: setTextAnim,
                    sfxIn: setSfxIn, sfxOut: setSfxOut };
  const applyTemplate = (t) => {
    if (busy) return;
    Object.keys(t.set).forEach(k => { if (SETTERS[k]) SETTERS[k](t.set[k]); });
    setTpl(t.id);
    setTab(t.tab);
    setHint(true);
    // The hook template is nothing without its line, so land in the box.
    if (t.id === 'hook' && !text) wantText.current = true;
  };
  useEffect(() => {
    if (tab === 'text' && wantText.current && textRef.current) { textRef.current.focus(); wantText.current = false; }
  }, [tab]);

  // ── Framing by hand: drag the picture, wheel or pinch to zoom. ──
  const stageDown = (e) => {
    if (busy) return;
    const el = stageRef.current;
    try { el.setPointerCapture(e.pointerId); } catch (x) {}
    const p = pinch.current || (pinch.current = { pts: new Map() });
    p.pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    p.startX = offX; p.startY = offY; p.startZoom = zoom;
    p.ox = e.clientX; p.oy = e.clientY;
    if (p.pts.size === 2) {
      const a = [...p.pts.values()];
      p.dist0 = Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y) || 1;
    }
    setHint(false);
    e.preventDefault();
  };
  const stageMove = (e) => {
    const p = pinch.current;
    if (!p || !p.pts.has(e.pointerId)) return;
    p.pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const r = stageRef.current.getBoundingClientRect();
    if (p.pts.size >= 2) {
      const a = [...p.pts.values()];
      const d = Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y) || 1;
      setZoom(Math.max(1, Math.min(4, +(p.startZoom * d / p.dist0).toFixed(3))));
      return;
    }
    // offX/offY are fractions of the OUTPUT frame, so a finger crossing a
    // tenth of the displayed picture moves it a tenth of the frame: a direct
    // 1:1 drag. The picture is the canvas bitmap letterboxed (object-fit)
    // inside the stage, so its on-screen size is derived, not read.
    const L = latest.current;
    const s = Math.min(r.width / L.outW, r.height / L.outH);
    const cw = Math.max(1, L.outW * s), ch = Math.max(1, L.outH * s);
    // In the split layout the drag moves the camera WINDOW over the source,
    // so the picture follows the finger the same way: dragging right shows
    // what is further right, which means the window's centre moves left by
    // the window's own share of the frame.
    const split = L.layout === 'split';
    const k = split ? -1 / Math.max(1, L.zoom) : 1;
    setOffX(Math.max(-0.5, Math.min(0.5, p.startX + (e.clientX - p.ox) / cw * k)));
    setOffY(Math.max(-0.5, Math.min(0.5, p.startY + (e.clientY - p.oy) / ch * k)));
  };
  const stageUp = (e) => {
    const p = pinch.current;
    if (!p) return;
    p.pts.delete(e.pointerId);
    try { stageRef.current.releasePointerCapture(e.pointerId); } catch (x) {}
    if (!p.pts.size) pinch.current = null;
    else { const a = [...p.pts.values()]; p.ox = a[0].x; p.oy = a[0].y; p.startX = offX; p.startY = offY; }
  };
  // Wheel zoom needs a non-passive listener to stop the page scrolling, and
  // React attaches wheel passively — so it is wired by hand.
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const onWheel = (e) => {
      if (latest.current.busy) return;
      e.preventDefault();
      setZoom(z => Math.max(1, Math.min(4, +(z * (1 - Math.sign(e.deltaY) * 0.06)).toFixed(3))));
      setHint(false);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  const download = (blob, ext) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = (clip.filename || 'clip').replace(RE_EXT, '') + `-${ratio.replace(':', 'x')}.${ext}`;
    document.body.appendChild(a); a.click(); a.remove();
    // Revoke late: revoking immediately can cancel the download in some browsers.
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  };

  // Every wait in the export is time-boxed. The first version awaited a
  // 'seeked' that never came and an AudioContext.resume() that could stall,
  // and an export that hangs at 0% with a Cancel button is the worst outcome
  // this screen can produce.
  const waitFor = (target, ev, ms) => new Promise(res => {
    const h = () => { target.removeEventListener(ev, h); res(); };
    target.addEventListener(ev, h);
    setTimeout(h, ms);
  });

  // ── Export: MediaRecorder path ──
  // Records the canvas while the video plays, so it runs in real time.
  const exportRecorder = async () => {
    const v = videoRef.current, c = canvRef.current;
    const type = pickRecorderType();
    if (!type) throw new Error('This browser cannot export video. Try Chrome.');
    const hd = outputSize(ratio, srcDims[0], srcDims[1]).hd;
    // The preview canvas is sized to the stage, not to the export. Size it to
    // the real output HERE, synchronously, before captureStream — the paint
    // loop would do it on its next tick, but by then the capture track would
    // already be attached to the small surface, and resizing a canvas resets
    // it and invalidates that track. `busy` is already set, so the loop
    // leaves it alone from here on.
    if (c.width !== outW || c.height !== outH) { c.width = outW; c.height = outH; }
    if (v.readyState >= 2) paintFrame(c.getContext('2d'), v, opts());
    // 60 for an HD source: Twitch delivers 60 fps and the platforms take it.
    // What the encoder actually keeps is machine-dependent — this is the
    // ceiling, not a promise; the frame-accurate path is the promise.
    const stream = c.captureStream(hd ? 60 : 30);
    // The canvas gives picture only. Add the clip's own audio, or the export
    // is silent — which is exactly what it used to be.
    const g = audioGraph(v, clip.url);
    if (g) {
      try { await Promise.race([g.ctx.resume(), new Promise(r => setTimeout(r, 1000))]); } catch (e) {}
      const at = g.dest.stream.getAudioTracks()[0];
      if (at) stream.addTrack(at);
    }
    const chunks = [];
    // The budget is deliberately generous: a Twitch clip arrives at 6-8 Mbps
    // for 1080p60, and re-encoding the same picture in real time at less than
    // that is where the softness in "my export looks blurry" came from.
    // Real-time encoders spend what they are given; a 30s clip at 16 Mbps is
    // 60 MB, well inside the upload cap.
    const rec = new MediaRecorder(stream, { mimeType: type, videoBitsPerSecond: hd ? 16e6 : 10e6,
                                            audioBitsPerSecond: 160e3 });
    rec.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
    const finished = new Promise(res => { rec.onstop = res; });

    // Seek to the in-point. Assigning the CURRENT time fires no 'seeked' event
    // in most browsers, so waiting unconditionally would hang forever on a
    // clip whose playhead is already parked there.
    if (Math.abs(v.currentTime - inPt) > 0.01) {
      v.currentTime = inPt;
      await waitFor(v, 'seeked', 3000);
    }
    dirtyRef.current = true;

    rec.start(200);
    // Give the recorder a beat to latch onto the track before playback starts.
    // Without it a short trim can finish before the first timeslice is emitted
    // and the export lands empty — which is exactly how this failed the first
    // time it was driven.
    await new Promise(r => setTimeout(r, 150));

    await v.play().catch(() => {});
    // Sound effects into the recorded track, at the cut's offsets. Only
    // possible when the clip's audio graph exists — it is the graph's `dest`
    // the recorder is listening to. (The frame-accurate path renders them
    // offline and needs none of this.)
    if (g) scheduleSfx(g.ctx, g.sfx, latest.current.sfx, g.ctx.currentTime - (v.currentTime - inPt));
    const span = Math.max(0.1, outPt - inPt);
    const started = Date.now();
    let stalled = false;
    await new Promise(res => {
      const tick = () => {
        if (cancelRef.current) return res();
        // A minimum wall-clock floor guarantees at least one timeslice lands
        // even for a very short trim.
        const enough = Date.now() - started >= 400;
        if (enough && (v.currentTime >= outPt || v.ended)) return res();
        // Real time plus a margin: playback that has not reached the out
        // point well after it should have is stuck, not slow.
        if (Date.now() - started > span * 1000 + 6000) { stalled = true; return res(); }
        setPct(Math.min(99, ((v.currentTime - inPt) / span) * 100));
        setTimeout(tick, 100);
      };
      tick();
    });
    v.pause();
    // Flush whatever is buffered before stopping; some builds only emit the
    // tail on request.
    try { rec.requestData(); } catch {}
    await new Promise(r => setTimeout(r, 120));
    rec.stop();
    await Promise.race([finished, new Promise(r => setTimeout(r, 4000))]);
    if (stalled) throw new Error('Playback stalled during the export. Try again, or trim a shorter section.');
    return { blob: new Blob(chunks, { type }), ext: type.includes('mp4') ? 'mp4' : 'webm' };
  };

  // Which export this browser gets. Probed once per output size, async,
  // because isConfigSupported is; null until it answers, and null for good on
  // a browser without WebCodecs or a usable codec — then the recorder runs.
  const [faSup, setFaSup] = useState(null);
  useEffect(() => {
    let gone = false;
    frameAccurateSupport(outW, outH, out.hd ? 60 : 30).then(s => { if (!gone) setFaSup(s); }).catch(() => {});
    return () => { gone = true; };
  }, [outW, outH, out.hd]);

  const runExport = async () => {
    // Exporting while the preview is playing was the reliable way to hang the
    // old editor at 0%: two things driving the same element. Stop the preview
    // first, always.
    pause();
    setErr(''); setDone(''); setBusy(true); setPct(0); cancelRef.current = false;
    latest.current.busy = true;
    const v = videoRef.current;
    // Exporting should not blast the clip across the room — but it MUST still
    // record the sound. With the WebAudio graph in place those are different
    // knobs: turn the monitor down and the recorded track is untouched.
    // v.muted is the fallback for a source we cannot route (see canReadAudio),
    // where the export stays silent exactly as it was before.
    const g = audioGraph(v, clip.url);
    const wasMuted = v.muted;
    const wasGain = g ? g.monitor.gain.value : 0;
    if (g) g.monitor.gain.value = 0;
    else v.muted = true;
    try {
      // Frame-accurate WebCodecs export where the browser can do it (every
      // source frame, the bitrate asked for, no real-time race — see the
      // block comment on exportFrameAccurate); the MediaRecorder path is the
      // fallback, which produces a playable container on every browser.
      const c = canvRef.current;
      if (c.width !== outW || c.height !== outH) { c.width = outW; c.height = outH; }
      let result;
      if (faSup) {
        // Paint for an EXACT media time, not whatever currentTime reads on
        // this tick: the frame being encoded is the one rVFC just presented.
        const paintAt = (t) => paintFrame(c.getContext('2d'), v,
          { ...opts(), t, caption: capOn ? activeCaption(caps, t) : null });
        result = await exportFrameAccurate(faSup, {
          v, c, paint: paintAt, inPt, outPt, outW, outH, fps: out.hd ? 60 : 30, hd: out.hd,
          srcUrl: clip.url, onPct: setPct, cancel: cancelRef, sfx: latest.current.sfx });
      } else {
        result = await exportRecorder();
      }
      if (cancelRef.current || !result) { setDone(''); return; }
      const { blob, ext } = result;
      if (!blob.size) throw new Error('Export produced an empty file.');
      const name = (clip.filename || 'clip').replace(RE_EXT, '')
                   + '-' + ratio.replace(':', 'x') + '.' + ext;
      setOutFile({ blob, ext, name });
      download(blob, ext);
      setPct(100);
      // The render goes to the server as well as to the downloads folder. Not
      // for our benefit — it is what lets the Scheduler tab, and the user's
      // PHONE, share the edited clip. A blob living in one tab's memory is
      // unreachable from the device that has the TikTok app on it.
      // schedulerOn follows the plan (Pro, or admin) — the same gate as the
      // Scheduler tab itself. For anyone else the render is saved to their
      // Clip Editor library and the wording says that: "added to your
      // Scheduler" with no Scheduler tab would be a promise about a screen
      // they cannot see.
      const where = schedulerOn ? 'Scheduler' : 'Clip Editor library';
      setDone('Exported. Saving to your ' + where + '…');
      try {
        const fd = new FormData();
        fd.append('file', new File([blob], name, { type: blob.type || 'video/mp4' }));
        const ur = await fetch('/uploads?source=render', { method: 'POST', body: fd });
        if (!ur.ok) throw new Error((await ur.json().catch(()=>({}))).detail || 'save failed');
        const saved = await ur.json();
        if (schedulerOn) {
          await fetch('/publish/schedule', { method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ upload_id: saved.id, caption: '', platforms: [],
                                   due_at: 0, duration_s: clipSecs, ratio, fmt: ext }) });
        }
        setDone(schedulerOn ? 'Exported and added to your Scheduler.'
                            : 'Exported and saved to your Clip Editor library.');
      } catch (e) {
        // The user still HAS the file — it downloaded. Say what did and did
        // not happen rather than reporting a failed export.
        setDone('');
        setErr('Exported to your downloads, but saving it to the ' + where + ' failed'
               + (e && e.message ? ' (' + e.message + ')' : '') + '.');
      }
      if (onExported) onExported(blob, ext);
    } catch (e) {
      setErr(e && e.message ? e.message : 'Export failed.');
    } finally {
      if (g) g.monitor.gain.value = wasGain;
      else v.muted = wasMuted;
      setBusy(false); setPlay(false);
      latest.current.busy = false;
      dirtyRef.current = true;
      seek(latest.current.inPt);
    }
  };

  const clipSecs = Math.max(0, outPt - inPt);
  const recType = pickRecorderType();
  // Frame-accurate where the browser can, the recorder where it cannot; the
  // button is live if either path exists.
  const canExport = (!!faSup || !!recType) && dur > 0;
  const eta = Math.max(1, Math.round(clipSecs));
  const fmtOut = faSup ? (faSup.ext === 'mp4' ? 'MP4' : 'WebM') : (recType.includes('mp4') ? 'MP4' : 'WebM');
  const framed = zoom !== 1 || offX !== 0 || offY !== 0;
  const shape = RATIOS.find(r => r[0] === ratio) || RATIOS[0];

  const TABS = [['trim', 'Trim'], ['frame', 'Frame'], ['text', 'Text'], ['fx', 'Effects']];
  if (captionsOn) TABS.push(['captions', 'Captions']);

  return (
    <div className="ed-bg" onMouseDown={e => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="ed glass" ref={rootRef} tabIndex={-1}>
        <div className="ed-head">
          <span className="ed-ico"><Icon name="film" size={18}/></span>
          <div className="ed-title">
            <h3>{clip.filename || 'Edit clip'}</h3>
            <div className="ed-sub">
              {dur ? edTime(dur) : '…'}{srcDims[1] ? ' · ' + srcDims[0] + '×' + srcDims[1] : ''}
            </div>
          </div>
          <button className="ed-x" onClick={onClose} disabled={busy} aria-label="Close editor"><Icon name="x" size={16}/></button>
        </div>

        <div className="ed-body">
          <div className="ed-main">
            <div className={'ed-stage' + (busy ? ' busy' : '')} ref={stageRef}
              onPointerDown={stageDown} onPointerMove={stageMove} onPointerUp={stageUp} onPointerCancel={stageUp}>
              <canvas ref={canvRef} width={outW} height={outH}/>
              {!dur && <div className="ed-loading"><span/>Loading clip…</div>}
              {dur > 0 && hint && !busy &&
                <div className="ed-hint">{layout === 'split' ? 'Drag to move the camera window · scroll or pinch to tighten it' : 'Drag to reposition · scroll or pinch to zoom'}</div>}
              {busy && <div className="ed-hint on">Rendering {Math.round(pct)}%</div>}
            </div>

            <video ref={videoRef} src={clip.url} onLoadedMetadata={onMeta} playsInline preload="auto"
              crossOrigin="anonymous" style={{display:'none'}}/>

            <div className="ed-transport">
              <button className={'ed-play' + (playing ? ' on' : '')} onClick={togglePlay} disabled={busy || !dur}
                aria-label={playing ? 'Pause' : 'Play'} title="Space">
                {playing ? <span className="ed-pause"/> : <Icon name="play" size={16}/>}
              </button>
              <button className="ed-step" onClick={()=>step(-1/30)} disabled={busy || !dur} title="Back one frame (←)">‹</button>
              <button className="ed-step" onClick={()=>step(1/30)} disabled={busy || !dur} title="Forward one frame (→)">›</button>
              <span className="ed-clock"><b ref={clockRef}>{edTime(0)}</b><span className="dim"> / {edTime(dur)}</span></span>
              <span className="ed-cut">
                <button className="ed-mark" onClick={()=>{const v=videoRef.current; if(v) setInAt(v.currentTime);}} disabled={busy || !dur} title="Set start here (I)">Set start</button>
                <button className="ed-mark" onClick={()=>{const v=videoRef.current; if(v) setOutAt(v.currentTime);}} disabled={busy || !dur} title="Set end here (O)">Set end</button>
              </span>
            </div>

            <EdTimeline dur={dur} inPt={inPt} outPt={outPt} thumbs={thumbs} headRef={headRef} disabled={busy}
              onIn={setInAt} onOut={setOutAt} onSeek={t=>seek(t)} onDragState={onDragState}/>

            <div className="ed-tlinfo">
              <span><i>Start</i> {edTime(inPt)}</span>
              <span className="mid"><i>Cut</i> {clipSecs.toFixed(1)}s</span>
              <span><i>End</i> {edTime(outPt)}</span>
            </div>
          </div>

          <div className="ed-side">
            {/* ── 1. Style: five big cards. One press sets everything; the
                   sections below are for tweaking, and each says its current
                   setting on its header so nothing has to be opened to be
                   understood. */}
            <div className="ed-tpls">
              <div className="ed-sec-t"><b>1</b> Pick a style</div>
              <div className="ed-tpl-row">
                {TEMPLATES.map(t => (
                  <button key={t.id} className={'ed-tpl' + (tpl === t.id ? ' on' : '')} disabled={busy}
                    onClick={() => applyTemplate(t)} title={t.desc}>
                    <span className={'ed-tpl-ic ' + t.id} aria-hidden="true"><i/><b/></span>
                    <span className="ed-tpl-n">{t.name}</span>
                  </button>
                ))}
              </div>
              {tpl && <div className="ed-note">{(TEMPLATES.find(t => t.id === tpl) || {}).desc}</div>}
            </div>

            {/* ── 2. Adjust: one section open at a time. `tab` is the open
                   section (a template opens the one it cares about). TABS is
                   the section list; captions join it only when the feature
                   is on. */}
            <div className="ed-sec-t"><b>2</b> Adjust</div>
            <div className="ed-panel" role="tablist">
              {TABS.map(([k,l])=>{
                const open = tab === k;
                const sums = {
                  trim: shape[2] + ' ' + ratio + ' · ' + ((inPt===0 && outPt===dur) ? 'whole clip' : clipSecs.toFixed(1) + 's'),
                  frame: (layout==='split' ? 'Cam + game' : (fill==='crop' ? 'Crop' : 'Blur')) + ' · ' + zoom.toFixed(1) + '×',
                  text: text ? '“' + text.slice(0, 22) + (text.length > 22 ? '…' : '') + '” · ' + textPos : 'None',
                  fx: (transIn==='none' && transOut==='none' ? 'No transitions' : [transIn!=='none' ? transIn + ' in' : '', transOut!=='none' ? transOut + ' out' : ''].filter(Boolean).join(', '))
                      + ' · ' + ((sfxIn!=='none' || sfxOut!=='none') ? 'sound on' : 'no sound'),
                  captions: caps ? (capOn ? caps.length + ' lines · ' + capPos : 'Off') : (capJob ? 'Transcribing…' : 'Not generated'),
                };
                return (
                  <div key={k} className={'ed-sec' + (open ? ' open' : '')}>
                    <button className="ed-sec-h" role="tab" aria-selected={open} aria-expanded={open}
                      onClick={()=>setTab(open ? '' : k)}>
                      <span className="ed-sec-l">{l}</span>
                      <span className="ed-sec-s">{sums[k]}</span>
                      <span className="ed-sec-c" aria-hidden="true">›</span>
                    </button>
                    {open && <div className="ed-sec-b">

              {k==='trim' && <>
                <div className="ed-grp">
                  <label>Shape</label>
                  <div className="ed-seg">
                    {RATIOS.map(([kk,,name])=>(
                      <button key={kk} className={ratio===kk?'on':''} disabled={busy}
                        onClick={()=>setRatio(kk)}>{name}<br/><small>{kk}</small></button>
                    ))}
                  </div>
                </div>
                <div className="ed-grp">
                  <label>Length</label>
                  <div className="ed-note">
                    Drag the handles under the video, or park the playhead and press
                    <kbd>I</kbd> for start, <kbd>O</kbd> for end.
                  </div>
                  <div className="ed-row">
                    <button className="rd-btn sm" disabled={busy||!dur||(inPt===0&&outPt===dur)}
                      onClick={()=>{setIn(0);setOut(dur);seek(0);}}>Use the whole clip</button>
                  </div>
                </div>
              </>}

              {k==='frame' && <>
                <div className="ed-grp">
                  <label>Layout</label>
                  <div className="ed-seg">
                    <button className={layout==='single'?'on':''} disabled={busy}
                      onClick={()=>setLayout('single')}>Single<br/><small>one picture</small></button>
                    <button className={layout==='split'?'on':''} disabled={busy}
                      onClick={()=>{setLayout('split'); if(zoom<1.5) setZoom(2.4);}}>Cam + game<br/><small>facecam on top</small></button>
                  </div>
                  {layout==='split' && <div className="ed-note">
                    Drag the preview to put the top window on the camera; zoom to tighten it.
                  </div>}
                </div>
                {layout==='single' && <div className="ed-grp">
                  <label>Fill</label>
                  <div className="ed-seg">
                    <button className={fill==='crop'?'on':''} disabled={busy}
                      onClick={()=>setFill('crop')}>Crop<br/><small>fills the frame</small></button>
                    <button className={fill==='blur'?'on':''} disabled={busy}
                      onClick={()=>setFill('blur')}>Blur<br/><small>keeps it all</small></button>
                  </div>
                </div>}
                <div className="ed-grp">
                  <label>{layout==='split' ? 'Camera window' : 'Zoom'}</label>
                  <div className="ed-row">
                    <input type="range" min="1" max="4" step="0.01" value={zoom} disabled={busy}
                      onChange={e=>setZoom(+e.target.value)}/>
                    <span className="ed-num">{zoom.toFixed(2)}×</span>
                  </div>
                  <div className="ed-note">Drag the preview to move the picture. Scroll or pinch to zoom.</div>
                </div>
                <div className="ed-grp">
                  <div className="ed-row">
                    <span className="ed-num" style={{textAlign:'left',minWidth:16}}>X</span>
                    <input type="range" min="-0.5" max="0.5" step="0.01" value={offX} disabled={busy}
                      onChange={e=>setOffX(+e.target.value)}/>
                  </div>
                  <div className="ed-row">
                    <span className="ed-num" style={{textAlign:'left',minWidth:16}}>Y</span>
                    <input type="range" min="-0.5" max="0.5" step="0.01" value={offY} disabled={busy}
                      onChange={e=>setOffY(+e.target.value)}/>
                  </div>
                  <button className="rd-btn sm" disabled={busy||!framed}
                    onClick={()=>{setZoom(1);setOffX(0);setOffY(0);}}>Reset framing</button>
                </div>
              </>}

              {k==='text' && <div className="ed-grp">
                <textarea className="ed-in" rows="2" value={text} disabled={busy} ref={textRef}
                  placeholder={tpl==='hook' ? 'Your hook, e.g. HE ACTUALLY DID IT' : 'Title on the clip (optional)'} maxLength={120}
                  onChange={e=>setText(e.target.value)}/>
                <div className="ed-seg">
                  {['top','middle','bottom'].map(p=>(
                    <button key={p} className={textPos===p?'on':''} disabled={busy}
                      onClick={()=>setTP(p)}>{p}</button>
                  ))}
                </div>
                <div className="ed-row">
                  <span className="ed-num" style={{textAlign:'left',minWidth:32}}>Size</span>
                  <input type="range" min="0.04" max="0.14" step="0.005" value={textSize} disabled={busy}
                    onChange={e=>setTS(+e.target.value)}/>
                </div>
                <div className="ed-row">
                  <button className={'rd-btn sm'+(textAnim?' grad':'')} disabled={busy}
                    onClick={()=>setTextAnim(v=>!v)} style={{flex:1}}>
                    {textAnim ? 'Title rises in' : 'Title static'}
                  </button>
                </div>
                <div className="ed-note">Up to three lines, burned into the export.</div>
              </div>}

              {k==='fx' && <div className="ed-grp">
                <label>Transitions</label>
                <div className="ed-row">
                  <span className="ed-num" style={{textAlign:'left',minWidth:32}}>In</span>
                  <div className="ed-seg" style={{flex:1}}>
                    {[['none','None'],['fade','Fade'],['zoom','Zoom']].map(([kk,l2])=>(
                      <button key={kk} className={transIn===kk?'on':''} disabled={busy} onClick={()=>setTransIn(kk)}>{l2}</button>
                    ))}
                  </div>
                </div>
                <div className="ed-row">
                  <span className="ed-num" style={{textAlign:'left',minWidth:32}}>Out</span>
                  <div className="ed-seg" style={{flex:1}}>
                    {[['none','None'],['fade','Fade']].map(([kk,l2])=>(
                      <button key={kk} className={transOut===kk?'on':''} disabled={busy} onClick={()=>setTransOut(kk)}>{l2}</button>
                    ))}
                  </div>
                </div>
                <label style={{marginTop:8}}>Sound</label>
                <div className="ed-row">
                  <span className="ed-num" style={{textAlign:'left',minWidth:32}}>Start</span>
                  <select className="ed-in" value={sfxIn} disabled={busy} onChange={e=>setSfxIn(e.target.value)}>
                    {SFX_KINDS.map(([kk,l2])=><option key={kk} value={kk}>{l2}</option>)}
                  </select>
                </div>
                <div className="ed-row">
                  <span className="ed-num" style={{textAlign:'left',minWidth:32}}>End</span>
                  <select className="ed-in" value={sfxOut} disabled={busy} onChange={e=>setSfxOut(e.target.value)}>
                    {SFX_KINDS.map(([kk,l2])=><option key={kk} value={kk}>{l2}</option>)}
                  </select>
                </div>
                <div className="ed-row">
                  <span className="ed-num" style={{textAlign:'left',minWidth:32}}>Vol</span>
                  <input type="range" min="0" max="1" step="0.05" value={sfxGain} disabled={busy}
                    onChange={e=>setSfxGain(+e.target.value)}/>
                </div>
                <div className="ed-note">Press play to hear them. The export carries exactly what you hear.</div>
              </div>}

              {k==='captions' && captionsOn && <div className="ed-grp">
                {!caps && !capJob &&
                  <button className="rd-btn grad sm" onClick={makeCaptions} disabled={busy}>
                    <Icon name="sparkles" size={13}/>&nbsp;Generate captions
                  </button>}
                {capJob &&
                  <>
                    <div className="ed-prog"><i style={{transform:'scaleX(' + ((capJob.pct||0)/100) + ')'}}/></div>
                    <div className="ed-note">Transcribing on the server… {capJob.pct||0}%</div>
                  </>}
                {caps && !capJob && <>
                  <div className="ed-row">
                    <button className={'rd-btn sm'+(capOn?' grad':'')} disabled={busy}
                      onClick={()=>setCapOn(v=>!v)} style={{flex:1}}>
                      {capOn ? 'Captions on' : 'Captions off'}
                    </button>
                    <button className="rd-btn sm" onClick={makeCaptions} disabled={busy}
                      title="Transcribe again">↻</button>
                  </div>
                  <div className="ed-note">
                    {caps.length ? caps.length + ' lines · burned into the export'
                                 : 'No speech detected in this clip.'}
                  </div>
                  <div className="ed-seg">
                    {[['top','Top'],['middle','Middle'],['bottom','Bottom'],['low','Low']].map(([kk,l2])=>(
                      <button key={kk} className={capPos===kk?'on':''} disabled={busy}
                        onClick={()=>setCapPos(kk)}>{l2}</button>
                    ))}
                  </div>
                  <div className="ed-row">
                    <span className="ed-num" style={{textAlign:'left',minWidth:32}}>Size</span>
                    <input type="range" min="0.035" max="0.09" step="0.005" value={capSize}
                      disabled={busy} onChange={e=>setCapSize(+e.target.value)}/>
                  </div>
                  <div className="ed-seg">
                    <button className={!capHi?'on':''} disabled={busy} onClick={()=>setCapHi(false)}>Outline</button>
                    <button className={capHi?'on':''} disabled={busy} onClick={()=>setCapHi(true)}>Boxed</button>
                  </div>
                  <div className="ed-row">
                    <button className={'rd-btn sm'+(capUpper?' grad':'')} disabled={busy}
                      onClick={()=>setCapUpper(v=>!v)} style={{flex:1}}>
                      {capUpper ? 'ALL CAPS' : 'Sentence case'}
                    </button>
                    {caps.some(c=>c.words&&c.words.length) &&
                      <button className={'rd-btn sm'+(capWord?' grad':'')} disabled={busy}
                        onClick={()=>setCapWord(v=>!v)} style={{flex:1}}>
                        {capWord ? 'Word pop on' : 'Word pop off'}
                      </button>}
                  </div>
                  <div className="ed-note">
                    Boxed reads on busy gameplay. "Low" sits where TikTok and Reels draw their buttons.
                  </div>
                </>}
                {capErr && <div className="ed-warn">{capErr}</div>}
              </div>}

                    </div>}
                  </div>
                );
              })}
            </div>

          </div>
        </div>
        <div className="ed-foot">
          {busy && <div className="ed-grp">
            <div className="ed-prog"><i style={{transform:'scaleX(' + (pct/100) + ')'}}/></div>
            <div className="ed-row">
              <span className="ed-note" style={{flex:1}}>Rendering {edTime(Math.min(clipSecs, clipSecs*pct/100))} of {edTime(clipSecs)}</span>
              <button className="rd-btn sm danger" onClick={()=>{cancelRef.current=true;}}>Cancel</button>
            </div>
          </div>}

          {!busy && <button className="rd-btn grad ed-export" onClick={runExport} disabled={!canExport}>
            <Icon name="download" size={14}/>&nbsp;Export · {shape[2]} {ratio} · {clipSecs.toFixed(1)}s
          </button>}

          {!canExport && dur > 0 &&
            <div className="ed-warn">This browser can't export video. Use Chrome, Edge or Safari.</div>}
          {canExport && !busy && !done && !err &&
            <div className="ed-note">{outW}×{outH} {fmtOut} · about {eta}s on your machine. Keep this tab open.</div>}
          {done && <div className="ed-note ok">{done}</div>}
          {err && <div className="ed-warn">{err}</div>}
          {outFile && !busy && <div className="ed-row">
            <button className="rd-btn sm" onClick={()=>download(outFile.blob, outFile.ext)}>Download again</button>
            {/* The render is in the Scheduler now — that is where posting
                lives, so the editor stays about editing. */}
            <span className="ed-note">Caption it and post from the <b>Scheduler</b> tab.</span>
          </div>}
        </div>
      </div>
    </div>
  );
}


/* ── Scheduler ────────────────────────────────────────────────────────────────
   One row of account chips, a tray of exports with no time yet, a month
   calendar, the selected day as a list, and a drawer for one clip. The
   calendar IS the schedule: drag a clip onto a day (it keeps its time of day,
   or gets 6 PM), open it to set the exact time, caption and platforms.

   It POSTS (2026-09-15): an account connected in the chip row is posted to by
   the server at the clip's time (src/publish/poster.py), and the drawer shows
   each platform's outcome as it happens. A platform that is NOT connected
   still gets the reminder + share path. The drawer says which is which — the
   "Auto" tag on a platform chip — because a queue that looks manual where it
   is not posts something the user did not expect. */
/* ── Review prompt ────────────────────────────────────────────────────────────
   Appears after 25 approved clips. Three things it must get right:

   1. "Not now" and "don't ask again" are REAL. A prompt that comes back after
      someone declined is the fastest way to make people resent the product.
   2. Publishing is opt-in and separate from rating. Someone giving 5 stars has
      not agreed to appear on a marketing page under their own name.
   3. It never asks for sentiment first. Showing this only to happy users is
      review gating — prohibited by Google and Trustpilot, and Trustpilot pulls
      profiles over it. Everyone at 25 clips gets the same prompt. */
function ReviewPrompt({ clips, onClose }) {
  const [stars, setStars]   = useState(0);
  const [hover, setHover]   = useState(0);
  const [comment, setCom]   = useState('');
  const [consent, setCon]   = useState(false);
  const [name, setName]     = useState('');
  const [busy, setBusy]     = useState(false);
  const [err, setErr]       = useState('');
  const [sent, setSent]     = useState(false);

  const post = async (payload) => {
    setBusy(true); setErr('');
    try {
      const r = await fetch('/reviews', {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
      if(!r.ok){ let d='Could not send that'; try{ d=(await r.json()).detail||d; }catch{}
        setErr(d); setBusy(false); return false; }
    } catch { setErr('Could not reach the server'); setBusy(false); return false; }
    setBusy(false); return true;
  };

  const submit = async () => {
    if(!stars) { setErr('Pick a rating first.'); return; }
    if(await post({stars, comment, publish_consent: consent, display_name: name})) {
      setSent(true);
      setTimeout(onClose, 1800);
    }
  };
  const later = async () => { await post({action:'snooze'}); onClose(); };
  const never = async () => { await post({action:'never'}); onClose(); };

  return (
    <div className="ed-bg" onMouseDown={e=>{ if(e.target===e.currentTarget) later(); }}>
      <div className="rv glass">
        {sent
          ? <div className="rv-done">
              <Icon name="sparkles" size={22}/>
              <h3>Thank you — that genuinely helps.</h3>
            </div>
          : <>
            <h3>How is Highlightz working out?</h3>
            <p className="rv-sub">
              You have approved {clips} clips. However it is going, we would
              rather hear it than not.
            </p>

            <div className="rv-stars" onMouseLeave={()=>setHover(0)}>
              {[1,2,3,4,5].map(n=>(
                <button key={n} className={'rv-star'+((hover||stars)>=n?' on':'')}
                  onMouseEnter={()=>setHover(n)} onClick={()=>setStars(n)}
                  aria-label={n+' star'+(n>1?'s':'')}>★</button>
              ))}
            </div>

            <textarea className="ed-in" rows="4" value={comment} maxLength={1500}
              placeholder="What is working, and what is not? (optional)"
              onChange={e=>setCom(e.target.value)}/>

            <label className="rv-check">
              <input type="checkbox" checked={consent}
                onChange={e=>setCon(e.target.checked)}/>
              <span>You can show this on the Highlightz site.</span>
            </label>
            {consent &&
              <input className="ed-in" value={name} maxLength={60}
                placeholder="Name to show (leave blank to stay anonymous)"
                onChange={e=>setName(e.target.value)}/>}
            <div className="ed-note">
              Ticking that box is the only thing that makes this public, and we
              still read it first. Leave it unticked and it only ever reaches us.
            </div>

            {err && <div className="ed-warn">{err}</div>}

            <div className="rv-actions">
              <button className="rd-btn grad" onClick={submit} disabled={busy||!stars}>
                Send
              </button>
              <button className="rd-btn sm" onClick={later} disabled={busy}>Not now</button>
              <button className="rd-btn sm" onClick={never} disabled={busy}>
                Don't ask again
              </button>
            </div>
          </>}
      </div>
    </div>
  );
}

/* Calendar helpers. Days are keyed by LOCAL date ('YYYY-MM-DD') because the
   browser is the only place that knows the zone; due_at stays epoch seconds
   on the wire (see toLocalInput / the datetime-local handler). */
const SC_DEFAULT_HOUR = 18;   // a clip dropped on a day with no time yet posts at 6 PM
function dayKey(d) {
  const p = n => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth()+1) + '-' + p(d.getDate());
}
function itemDay(it) { return it.due_at ? dayKey(new Date(it.due_at * 1000)) : ''; }
function scState(it) {
  if (it.status === 'posting') return 'posting';
  if (it.status === 'posted' || it.status === 'skipped') return 'posted';
  if (it.status === 'failed') return 'failed';
  if (it.missed) return 'missed';
  if (it.due) return 'due';
  return 'pending';
}
const SC_LABEL = {posting:'Posting…', posted:'Posted', failed:'Needs attention',
                  missed:'Missed', due:'Due now', pending:'Scheduled'};
const scTime = ts => new Date(ts * 1000).toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});

/* The three accounts a clip can be posted to, as one row of chips: connected
   (with the account name and a disconnect ×), broken (reconnect), not yet
   connected (Connect), or not set up by the operator (soon). */
function AccountChips({ me, connections = [] }) {
  const disconnect = (id) => fetch('/publish/connections/'+id, {method:'DELETE'}).catch(()=>{});
  return (
    <div>
      <div className="sc-top-row">
        {(connections||[]).map(c=>{
          if (c.connected && !c.last_error) return (
            <span key={c.id} className="sc-acct on" title={'Highlightz posts to ' + (c.account_name||c.label) + ' for you'}>
              <i className="dot"/>{c.label} · {c.account_name || 'connected'}
              <button onClick={()=>disconnect(c.id)} aria-label={'Disconnect ' + c.label} title={'Disconnect ' + c.label}>
                <Icon name="x" size={12}/>
              </button>
            </span>);
          if (c.connected) return (
            <a key={c.id} className="sc-acct err" href={'/publish/connect/'+c.id} title={c.last_error}>
              <i className="dot"/>{c.label} · reconnect
            </a>);
          if (c.configured) return (
            <a key={c.id} className="sc-acct" href={'/publish/connect/'+c.id}>
              <Icon name="plus" size={12}/>Connect {c.label}
            </a>);
          return (
            <span key={c.id} className="sc-acct off"
              title={me && me.is_admin ? 'Add this platform’s app keys to .env (see HANDOFF)' : 'Coming soon'}>
              <i className="dot"/>{c.label} · soon
            </span>);
        })}
      </div>
      <p className="sc-hint">
        Connect an account and Highlightz posts your clips to it for you. Only the clips you choose it for, only while it is connected.
      </p>
    </div>
  );
}

/* Exported clips with no time yet. Drag one onto a day, or open it. */
function InboxTray({ items, onOpen }) {
  if (!items.length) return null;
  return (
    <div className="rd-card glass sc-inbox">
      <div className="sc-inbox-head">
        <Icon name="download" size={13}/>
        {items.length} exported, not scheduled yet
        <span className="sc-sub">· drag onto a day, or open one to pick a time</span>
      </div>
      <div className="sc-tray">
        {items.map(it=>(
          <div key={it.id} className="sc-tile" draggable title="Drag onto a day"
            onDragStart={e=>{ e.dataTransfer.setData('text/plain', it.id); e.dataTransfer.effectAllowed = 'move'; }}
            onClick={()=>onOpen(it)}>
            <b>{it.filename}</b>
            <span>{Math.round(it.duration_s||0)}s · {(it.platforms||[]).length
              ? (it.platforms||[]).length + ' platform' + ((it.platforms||[]).length > 1 ? 's' : '')
              : 'no platform yet'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* A month. Six rows of seven, trimmed to five when the sixth is all next
   month. A chip per scheduled clip, colored by state, draggable between days
   unless it is mid-upload or already posted. */
function MonthCalendar({ month, onMonth, items, selected, onSelect, onOpen, onMove }) {
  const [over, setOver] = useState('');
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const start = new Date(first); start.setDate(1 - first.getDay());
  const cells = [];
  for (let i = 0; i < 42; i++) { const d = new Date(start); d.setDate(start.getDate() + i); cells.push(d); }
  const rows = cells[35].getMonth() === month.getMonth() ? cells : cells.slice(0, 35);
  const byDay = {};
  (items||[]).forEach(it=>{ const k = itemDay(it); if (k) (byDay[k] = byDay[k] || []).push(it); });
  Object.values(byDay).forEach(l=>l.sort((a,b)=>a.due_at - b.due_at));
  const todayKey = dayKey(new Date());
  const shift = n => onMonth(new Date(month.getFullYear(), month.getMonth() + n, 1));
  const today = () => { const n = new Date(); onMonth(new Date(n.getFullYear(), n.getMonth(), 1)); onSelect(todayKey); };
  const dropOn = (e, d) => {
    e.preventDefault(); setOver('');
    const id = e.dataTransfer.getData('text/plain');
    if (id) onMove(id, d);
  };
  return (
    <div className="rd-card glass sc-cal">
      <div className="sc-cal-head">
        <h3>{month.toLocaleDateString([], {month:'long', year:'numeric'})}</h3>
        <button className="rd-btn sm" onClick={today}>Today</button>
        <button className="sc-cal-nav" onClick={()=>shift(-1)} aria-label="Previous month">‹</button>
        <button className="sc-cal-nav" onClick={()=>shift(1)} aria-label="Next month">›</button>
      </div>
      <div className="sc-dow">{['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].map(d=><span key={d}>{d}</span>)}</div>
      <div className="sc-grid">
        {rows.map(d=>{
          const k = dayKey(d);
          const list = byDay[k] || [];
          // State classes are prefixed: a bare `today` collides with the
          // TodayHeader's global .today rule and pushes the day number right.
          const cls = 'sc-day' + (d.getMonth() !== month.getMonth() ? ' is-out' : '')
                    + (k === todayKey ? ' is-today' : '') + (k === selected ? ' is-sel' : '')
                    + (over === k ? ' is-over' : '');
          return (
            <div key={k} className={cls} onClick={()=>onSelect(k)}
              onDragOver={e=>{ e.preventDefault(); if (over !== k) setOver(k); }}
              onDragLeave={()=>{ if (over === k) setOver(''); }}
              onDrop={e=>dropOn(e, d)}>
              <span className="n">{d.getDate()}</span>
              <div className="chips">
                {list.slice(0, 3).map(it=>{
                  const st = scState(it);
                  const canDrag = st !== 'posting' && st !== 'posted';
                  return (
                    <div key={it.id} className={'sc-chip ' + st} draggable={canDrag}
                      title={it.filename + ' · ' + SC_LABEL[st]}
                      onDragStart={e=>{ e.dataTransfer.setData('text/plain', it.id); e.dataTransfer.effectAllowed = 'move'; }}
                      onClick={e=>{ e.stopPropagation(); onOpen(it); }}>
                      <i/><span className="t">{scTime(it.due_at)}</span><span>{it.filename}</span>
                    </div>
                  );
                })}
                {list.length > 3 && <div className="sc-more">+{list.length - 3} more</div>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* The selected day, as a list. On a phone the calendar cells only have room
   for dots, so this is where the names are; on a desktop it is the agenda. */
function DayList({ day, items, onOpen }) {
  const list = items.filter(it=>itemDay(it) === day).sort((a,b)=>a.due_at - b.due_at);
  const d = new Date(day + 'T12:00');
  return (
    <div className="rd-card glass sc-daylist">
      <div className="sc-inbox-head">
        <Icon name="clock" size={13}/>{d.toLocaleDateString([], {weekday:'long', month:'long', day:'numeric'})}
      </div>
      {list.length === 0
        ? <div className="sc-sub">Nothing scheduled. Drag a clip onto this day, or open one and pick a time.</div>
        : list.map(it=>{
            const st = scState(it);
            return (
              <div key={it.id} className="sc-row" onClick={()=>onOpen(it)}>
                <span className="when">{scTime(it.due_at)}</span>
                <span className="name">{it.filename}</span>
                <span className={'st ' + st}>{SC_LABEL[st]}</span>
              </div>
            );
          })}
    </div>
  );
}

/* One clip, in a drawer: caption, where it goes, when, and what happened.
   Everything saves as you go (PATCH on blur/change), and the drawer follows
   the item over the socket, so a post in progress updates in place. */
function ScheduleDrawer({ item, platforms, connections = [], onClose, onDrop }) {
  const [cap, setCap]     = useState(item.caption || '');
  const [when, setWhen]   = useState(item.due_at ? toLocalInput(item.due_at) : '');
  const [picked, setPicked] = useState(new Set(item.platforms || []));
  const [copied, setCopied] = useState(false);
  const [busyErr, setBusyErr] = useState('');
  const [shareable, setShareable] = useState(false);

  // Follow the item when it changes underneath us (a drag on the calendar, a
  // result arriving) — but never the caption mid-edit.
  useEffect(()=>{
    setPicked(new Set(item.platforms || []));
    setWhen(item.due_at ? toLocalInput(item.due_at) : '');
  }, [item.id, item.due_at, (item.platforms || []).join(',')]);

  // Only offer the share sheet if this browser can actually take a file. It is
  // absent on most desktops, so the download + upload-page path below is the
  // real path there, not a fallback apology.
  useEffect(()=>{
    try {
      const probe = new File([new Blob([1])], 'x.mp4', {type:'video/mp4'});
      setShareable(!!(navigator.canShare && navigator.share && navigator.canShare({files:[probe]})));
    } catch { setShareable(false); }
  },[]);

  // Which chosen platforms the SERVER will post to (connected, healthy) and
  // which the user still posts by hand. Same split the poster makes.
  const connected = new Set((connections||[]).filter(c=>c.connected && !c.last_error).map(c=>c.id));
  const results = item.results || {};
  const auto   = [...picked].filter(p=>connected.has(p));
  const manual = [...picked].filter(p=>!connected.has(p));
  const posting = item.status === 'posting';
  const done    = item.status === 'posted';
  const st = scState(item);

  const save = async (patch) => {
    setBusyErr('');
    try {
      const r = await fetch('/publish/schedule/'+item.id, {method:'PATCH',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify(patch)});
      if(!r.ok){ let d='Could not save'; try{ d=(await r.json()).detail||d; }catch{} setBusyErr(d); }
    } catch { setBusyErr('Could not reach the server'); }
  };

  const toggle = (id) => {
    if (posting) return;
    const next = new Set(picked);
    next.has(id) ? next.delete(id) : next.add(id);
    setPicked(next);
    save({platforms:[...next]});
  };

  // Post now / Retry. The server clears failed platforms, keeps posted ones,
  // and uploads in the background; the drawer follows over the socket.
  const postNow = async () => {
    setBusyErr('');
    try {
      const r = await fetch('/publish/schedule/'+item.id+'/post', {method:'POST'});
      if(!r.ok){ let d='Could not start posting'; try{ d=(await r.json()).detail||d; }catch{} setBusyErr(d); }
    } catch { setBusyErr('Could not reach the server'); }
  };

  const share = async () => {
    setBusyErr('');
    try {
      const r = await fetch('/uploads/'+item.upload_id+'/file');
      if(!r.ok) throw new Error('clip is no longer on the server');
      const blob = await r.blob();
      const f = new File([blob], item.filename || 'clip.mp4',
                         {type: blob.type || 'video/mp4'});
      if(!(navigator.canShare && navigator.canShare({files:[f]})))
        throw new Error('this browser cannot share files');
      await navigator.share({files:[f], text: cap || ''});
    } catch(e){
      if(e && e.name === 'AbortError') return;   // user backed out of the sheet
      setBusyErr(e && e.message ? 'Could not share: ' + e.message : 'Could not share.');
    }
  };

  const copy = async () => {
    try { await navigator.clipboard.writeText(cap||''); setCopied(true); setTimeout(()=>setCopied(false),1600); }
    catch { setBusyErr('Could not copy — select the text and copy it.'); }
  };

  const issues = (platforms||[]).filter(pf=>picked.has(pf.id))
    .map(pf=>fitIssues(pf, item.duration_s||0, item.ratio||'', cap, item.fmt||'')[0]).filter(Boolean);
  const byId = {}; (platforms||[]).forEach(pf=>{ byId[pf.id] = pf; });

  return (
    <>
      <div className="sc-drawer-bg" onMouseDown={onClose}/>
      <div className="sc-drawer" role="dialog" aria-label="Scheduled clip">
        <div className="sc-dr-head">
          <b>{item.filename}</b>
          <span className={'sc-state ' + st}>{SC_LABEL[st]}</span>
          <button className="sc-cal-nav" onClick={onClose} aria-label="Close"><Icon name="x" size={14}/></button>
        </div>
        <div className="sc-dr-body">
          <video src={'/uploads/'+item.upload_id+'/file'} controls preload="metadata"/>

          <div className="sc-lbl">Caption</div>
          <textarea className="ed-in" rows="3" value={cap} disabled={posting}
            placeholder="Caption + hashtags — written once, used everywhere"
            onChange={e=>setCap(e.target.value)} onBlur={()=>save({caption:cap})}/>

          <div className="sc-lbl">Post to</div>
          <div className="sc-pchips">
            {(platforms||[]).map(pf=>{
              const on = picked.has(pf.id);
              const isAuto = on && connected.has(pf.id);
              return (
                <button key={pf.id} className={'sc-pchip' + (on ? ' on' : '')} onClick={()=>toggle(pf.id)} disabled={posting}>
                  {on ? '✓ ' : ''}{pf.label}{isAuto && <small title="Highlightz posts this one for you">Auto</small>}
                </button>
              );
            })}
          </div>
          {issues.map((t,i)=><div key={i} className="pub-warn">{t}</div>)}
          <div className="sc-sub">
            Connected accounts are posted to for you at this time; the rest get a reminder and one-tap share.
            {manual.length > 0 && <> Open: {manual.map((p,i)=>(
              <span key={p}>{i ? ', ' : ' '}<a href={(byId[p]||{}).upload_url} target="_blank" rel="noopener noreferrer">{(byId[p]||{}).label || p}</a></span>
            ))}</>}
          </div>

          <div className="sc-lbl">{auto.length ? 'Posts at' : 'Remind at'}</div>
          <input className="ed-in" type="datetime-local" value={when} disabled={posting}
            onChange={e=>{ setWhen(e.target.value);
              const t = e.target.value ? Math.floor(new Date(e.target.value).getTime()/1000) : 0;
              save({due_at: t}); }}/>

          {Object.keys(results).length > 0 && <>
            <div className="sc-lbl">Results</div>
            <div className="sc-res">
              {Object.entries(results).map(([p, res])=>(
                <div key={p}>
                  <b>{(byId[p]||{}).label || p}</b>
                  {res.status === 'posted'
                    ? <span className="pub-ok">Posted{res.url ? <> · <a href={res.url} target="_blank" rel="noopener noreferrer">View</a></> : ''}{res.note ? <span className="sc-note"> {res.note}</span> : null}</span>
                    : res.status === 'posting'
                      ? <span className="pub-ok">Uploading…</span>
                      : <span className="pub-warn">{res.error}</span>}
                </div>
              ))}
            </div>
          </>}
          {busyErr && <div className="ed-warn">{busyErr}</div>}
        </div>
        <div className="sc-dr-foot">
          {auto.length > 0 && !posting && !done &&
            <button className="rd-btn sm grad" onClick={postNow}>
              <Icon name="upload" size={13}/>&nbsp;{st === 'failed' ? 'Retry' : 'Post now'}
            </button>}
          {shareable && manual.length > 0 && <button className="rd-btn sm" onClick={share}>
            <Icon name="upload" size={13}/>&nbsp;Share to an app
          </button>}
          <a className="rd-btn sm" href={'/uploads/'+item.upload_id+'/file'}
             download={item.filename}>Download</a>
          <button className="rd-btn sm" onClick={copy} disabled={!cap}>
            {copied ? 'Copied' : 'Copy caption'}
          </button>
          {!done && !posting && manual.length > 0 &&
            <button className="rd-btn sm" onClick={()=>save({status:'posted'})}>Mark posted</button>}
          <button className="rd-btn sm danger" onClick={()=>{ onDrop(item.id); onClose(); }} disabled={posting}>Remove</button>
        </div>
      </div>
    </>
  );
}


/* Autopilot: approve a clip and the server renders and queues it. Pro,
   off by default. Saves on every change (PUT /autopilot) and carries the
   browser's timezone so "daily at 18:00" means the user's 18:00. */
function AutopilotCard({ me, ap, connections = [], captionsOn = false, onSaved }) {
  const cfg = (ap && ap.config) || null;
  const [busy, setBusy] = useState(false);
  const [ran, setRan] = useState('');
  if (!cfg) return null;
  const connected = (connections||[]).filter(c=>c.connected && !c.last_error);
  const save = async (patch) => {
    setBusy(true);
    try {
      const r = await fetch('/autopilot', {method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({...cfg, ...patch, tz_offset_min: -new Date().getTimezoneOffset()})});
      if (r.ok && onSaved) onSaved((await r.json()).config);
    } catch {}
    setBusy(false);
  };
  const runNow = async () => {
    setRan('');
    try { const r = await fetch('/autopilot/run', {method:'POST'}); setRan(r.ok ? 'Started. Watch the calendar.' : 'Could not start.'); }
    catch { setRan('Could not reach the server.'); }
  };
  const on = !!cfg.enabled;
  return (
    <div className={'rd-card glass ap' + (on ? ' on' : '')}>
      <div className="ap-head">
        <div className="ap-title">
          <span className="si"><Icon name="zap" size={15}/></span>
          <div>
            <h3>Autopilot</h3>
            <div className="desc" style={{margin:0}}>Approve a clip and it is cut for vertical, captioned and posted for you.</div>
          </div>
        </div>
        <button className={'ap-switch' + (on ? ' on' : '')} role="switch" aria-checked={on} disabled={busy}
          onClick={()=>save({enabled: !on})} aria-label="Autopilot on or off"><i/></button>
      </div>
      {on && <div className="ap-body">
        <div className="ap-grp">
          <label>Style</label>
          <div className="ed-seg">
            {[['full','Full Frame'],['blur','Blur Bars'],['punch','Punch In'],['hook','Hook Title']].map(([k,l])=>(
              <button key={k} className={cfg.template===k?'on':''} disabled={busy} onClick={()=>save({template:k})}>{l}</button>
            ))}
          </div>
        </div>
        <div className="ap-grp">
          <label>Post to</label>
          {connected.length === 0
            ? <div className="sc-sub">Connect an account above. Until then Autopilot still cuts each clip and puts it on the calendar as a reminder.</div>
            : <div className="sc-pchips">
                {connected.map(c=>{ const picked = cfg.platforms.includes(c.id); return (
                  <button key={c.id} className={'sc-pchip'+(picked?' on':'')} disabled={busy}
                    onClick={()=>save({platforms: picked ? cfg.platforms.filter(p=>p!==c.id) : [...cfg.platforms, c.id]})}>
                    {picked ? '✓ ' : ''}{c.label}
                  </button>); })}
              </div>}
        </div>
        <div className="ap-grp">
          <label>When</label>
          <div className="ed-seg">
            {[['now','Right away'],['spaced','Spread out'],['daily','Once a day']].map(([k,l])=>(
              <button key={k} className={cfg.timing===k?'on':''} disabled={busy} onClick={()=>save({timing:k})}>{l}</button>
            ))}
          </div>
          {cfg.timing==='spaced' && <div className="ap-row">
            <span>Every</span>
            <select className="ed-in" value={cfg.spacing_h} disabled={busy} onChange={e=>save({spacing_h:+e.target.value})}>
              {[1,2,3,4,6,8,12,24].map(h=><option key={h} value={h}>{h} hour{h>1?'s':''}</option>)}
            </select>
          </div>}
          {cfg.timing==='daily' && <div className="ap-row">
            <span>At</span>
            <input className="ed-in" type="time" value={cfg.daily_at} disabled={busy} onChange={e=>save({daily_at:e.target.value})}/>
            <span className="sc-sub">your local time</span>
          </div>}
        </div>
        <div className="ap-grp">
          <label>Caption</label>
          <input className="ed-in" defaultValue={cfg.caption_text} disabled={busy} maxLength={2200}
            onBlur={e=>{ if (e.target.value !== cfg.caption_text) save({caption_text:e.target.value}); }}/>
          <div className="sc-sub">{'{title}'}, {'{channel}'} and {'{game}'} are filled in from the clip.</div>
        </div>
        <div className="ap-grp ap-toggles">
          <button className={'sc-pchip'+(cfg.title?' on':'')} disabled={busy} onClick={()=>save({title:!cfg.title})}>
            {cfg.title ? '✓ ' : ''}Title on the video
          </button>
          {captionsOn && <button className={'sc-pchip'+(cfg.captions?' on':'')} disabled={busy} onClick={()=>save({captions:!cfg.captions})}>
            {cfg.captions ? '✓ ' : ''}Auto-captions
          </button>}
          <button className="rd-btn sm" onClick={runNow} disabled={busy} title="Every clip approved this week that has a file and is not scheduled yet">
            Run on my approved clips
          </button>
          {ran && <span className="sc-sub">{ran}</span>}
        </div>
        {ap && ap.font_ok === false && <div className="ed-warn">The server has no font for titles and captions, so clips render without text. (Admin: set AUTOPILOT_FONT.)</div>}
      </div>}
    </div>
  );
}

function ScheduleScreen({ me, queue = [], platforms = [], connections = [], uploadsOn = true, autopilot = null, onAutopilot = null, captionsOn = false }) {
  const [month, setMonth] = useState(()=>{ const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), 1); });
  const [selected, setSelected] = useState(dayKey(new Date()));
  const [openId, setOpenId] = useState(null);
  const drop = (id) => fetch('/publish/schedule/'+id, {method:'DELETE'}).catch(()=>{});

  const items = queue || [];
  const inbox = items.filter(i=>!i.due_at && (i.status === 'pending' || i.status === 'failed'));
  const scheduled = items.filter(i=>i.due_at > 0);
  // Derived from the queue, not copied: a result arriving over the socket
  // updates the open drawer in place.
  const openItem = items.find(i=>i.id === openId) || null;

  // A drop keeps the clip's time of day if it had one; a clip from the tray
  // gets the default hour. Local wall clock, resolved to an instant here.
  const move = async (id, d) => {
    const it = items.find(i=>i.id === id);
    if (!it) return;
    const had = it.due_at ? new Date(it.due_at * 1000) : null;
    const t = new Date(d.getFullYear(), d.getMonth(), d.getDate(),
                       had ? had.getHours() : SC_DEFAULT_HOUR, had ? had.getMinutes() : 0);
    setSelected(dayKey(d));
    await fetch('/publish/schedule/'+id, {method:'PATCH',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({due_at: Math.floor(t.getTime()/1000)})}).catch(()=>{});
  };

  // Plan gate mirrors the backend 403 with an upgrade card, the same shape
  // as the Clip Editor's. After every hook, so hook order stays stable.
  if (me && me.plan_limits && !me.plan_limits.uploads && !me.is_admin) {
    return (
      <div className="rd-wrap">
        <div className="rd-card glass" style={{textAlign:'center',padding:'48px 24px'}}>
          <div style={{marginBottom:12,color:'var(--acc)'}}><Icon name="clock" size={40}/></div>
          <h3 style={{fontSize:17,marginBottom:8,justifyContent:'center'}}>Scheduler is a Pro feature</h3>
          <div className="desc" style={{maxWidth:460,margin:'0 auto 20px'}}>
            Connect YouTube, TikTok and Instagram and have every clip you export
            posted for you at the time you pick. Included with Pro, with the Clip
            Editor and the VOD scanner.
          </div>
          <a href="/billing/portal" className="rd-btn grad" style={{textDecoration:'none',display:'inline-flex',gap:8,alignItems:'center'}}>
            <Icon name="zap" size={14}/>Upgrade to Pro — $25/month
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="rd-wrap">
      {/* First run only: the three steps say where clips come from and what
          to do with them. Once there is a clip the calendar says it. */}
      {items.length === 0 && <div className="rd-how">
        {[['download','1','Export a clip','Anything you export in the Clip Editor lands here.'],
          ['chat','2','Write it once','One caption, reused for every platform. We check it fits before it goes out.'],
          ['clock','3','Post it','Drop it on a day. Connected accounts are posted to for you; the rest get a reminder and one-tap share.']
        ].map(([icon,n,title,body])=>(
          <div className="rd-step" key={n}>
            <span className="sn">{n}</span>
            <div>
              <div className="st"><Icon name={icon} size={13}/> {title}</div>
              <div className="sb">{body}</div>
            </div>
          </div>
        ))}
      </div>}

      <AccountChips me={me} connections={connections}/>
      <AutopilotCard me={me} ap={autopilot} connections={connections} captionsOn={captionsOn} onSaved={onAutopilot}/>

      {!uploadsOn &&
        <div className="ed-warn" style={{marginTop:12}}>
          The Clip Editor is switched off, so nothing can reach the Scheduler yet.
        </div>}

      <InboxTray items={inbox} onOpen={it=>setOpenId(it.id)}/>
      <MonthCalendar month={month} onMonth={setMonth} items={scheduled} selected={selected}
        onSelect={setSelected} onOpen={it=>setOpenId(it.id)} onMove={move}/>
      <DayList day={selected} items={scheduled} onOpen={it=>setOpenId(it.id)}/>

      {openItem && <ScheduleDrawer item={openItem} platforms={platforms} connections={connections}
        onClose={()=>setOpenId(null)} onDrop={drop}/>}
    </div>
  );
}

function UploadScreen({ me, uploadsOn = true, importOn = false, captionsOn = false, platforms = [],
                        openUpload = null, onOpened = null }) {
  const [uploads, setUploads] = useState([]);
  const [quota, setQuota]     = useState(null);
  const [over, setOver]       = useState(false);
  const [prog, setProg]       = useState({});   // localId -> {name, pct, err}
  const [err, setErr]         = useState('');
  const fileRef = useRef(null);
  const [editing, setEditing] = useState(null);

  const load = useCallback(()=>{
    // Don't call an endpoint that is deliberately 503ing: when only the import
    // half is live this screen still mounts, and a pointless failing request
    // on every reconnect is noise in the logs for no gain.
    if(!uploadsOn) return;
    fetch('/uploads').then(r=>r.ok?r.json():null).then(d=>{
      if(!d) return;
      setUploads(d.uploads||[]);
      setQuota(d.quota||null);
    }).catch(()=>{});
  },[uploadsOn]);

  // Mount + every WS reconnect (deploy, sleep, network blip). Without the
  // hz_refetch listener the library would silently go stale after a restart.
  useEffect(()=>{
    load();
    window.addEventListener('hz_refetch', load);
    return ()=>window.removeEventListener('hz_refetch', load);
  },[load]);

  // Arriving from a clip card's "Edit clip". The parent has already copied the
  // file into the library and switched the route here; this is the last step —
  // open the editor on it, and hand the parent back its null so navigating
  // away and returning does not re-open the same clip.
  //
  // Also inserts the record directly rather than waiting for `load()`: this
  // screen mounts at the same moment, and opening the editor on an upload the
  // list has not fetched yet would otherwise race.
  useEffect(()=>{
    if(!openUpload) return;
    setUploads(p=>p.some(u=>u.id===openUpload.id)?p:[openUpload,...p]);
    setEditing(openUpload);
    onOpened && onOpened();
  },[openUpload]);

  // Live updates from the user's OTHER tabs — upload on your laptop, see it
  // appear on your phone without a refresh.
  useEffect(()=>{
    const onWs = e=>{
      try{
        const m = JSON.parse(e.detail);
        if(m.event==='upload_added'){
          setUploads(p=>p.some(u=>u.id===m.upload.id)?p:[m.upload,...p]);
          if(m.quota) setQuota(m.quota);
        } else if(m.event==='upload_removed'){
          setUploads(p=>p.filter(u=>u.id!==m.upload_id));
          if(m.quota) setQuota(m.quota);
        }
      }catch{}
    };
    window.addEventListener('hz_ws', onWs);
    return ()=>window.removeEventListener('hz_ws', onWs);
  },[]);

  // XHR rather than fetch: fetch gives no upload progress, and these files are
  // big enough that a silent 60-second wait reads as a broken page.
  const sendOne = (file) => new Promise(resolve=>{
    const localId = Math.random().toString(36).slice(2);
    setProg(p=>({...p,[localId]:{name:file.name,pct:0}}));
    const fd = new FormData();
    fd.append('file', file);
    const xhr = new XMLHttpRequest();
    xhr.open('POST','/uploads');
    xhr.upload.onprogress = ev=>{
      if(!ev.lengthComputable) return;
      const pct = Math.round(ev.loaded/ev.total*100);
      setProg(p=>p[localId]?{...p,[localId]:{...p[localId],pct}}:p);
    };
    xhr.onload = ()=>{
      if(xhr.status>=200 && xhr.status<300){
        // The WS event also delivers this; de-dupe by id so the optimistic
        // insert and the broadcast can't produce two cards.
        try{
          const up = JSON.parse(xhr.responseText);
          setUploads(p=>p.some(u=>u.id===up.id)?p:[up,...p]);
          // Open the editor on the clip that just landed. Uploading is a means
          // to editing, so making the user find a button afterwards is pure
          // friction. Only the first of a batch opens, so dropping five files
          // doesn't fight the user for the screen.
          setEditing(prev => prev || up);
        }catch{}
        setProg(p=>{const n={...p};delete n[localId];return n;});
        load();
      } else {
        let msg = 'Upload failed';
        try{ msg = JSON.parse(xhr.responseText).detail || msg; }catch{}
        setProg(p=>p[localId]?{...p,[localId]:{...p[localId],err:msg}}:p);
        setErr(msg);
      }
      resolve();
    };
    xhr.onerror = ()=>{
      setProg(p=>p[localId]?{...p,[localId]:{...p[localId],err:'Network error'}}:p);
      resolve();
    };
    xhr.send(fd);
  });

  // Sequential, not parallel: the box has 1 vCPU and a 2 GB RAM ceiling, and
  // three concurrent 300 MB uploads is how you starve the clipping workers.
  const send = async (files) => {
    setErr('');
    for(const f of Array.from(files)) await sendOne(f);
  };

  const onDrop = e=>{
    e.preventDefault(); setOver(false);
    if(e.dataTransfer.files?.length) send(e.dataTransfer.files);
  };

  const del = async id=>{
    const r = await fetch('/uploads/'+id,{method:'DELETE'});
    if(r.ok){ setUploads(p=>p.filter(u=>u.id!==id)); load(); }
  };

  // Plan gate mirrors the backend 403 with an upgrade card rather than a form
  // that errors. After every hook, so hook order stays stable while /me loads.
  if (me && me.plan_limits && !me.plan_limits.uploads) {
    return (
      <div className="rd-scroll">
        <div className="rd-settings">
          <div className="rd-section-title"><h2>Clip Editor</h2></div>
          <div className="rd-card glass" style={{textAlign:'center',padding:'48px 24px'}}>
            <div style={{marginBottom:12,color:'var(--acc)'}}><Icon name="upload" size={40}/></div>
            <h3 style={{fontSize:17,marginBottom:8,justifyContent:'center'}}>Clip Editor is a Pro feature</h3>
            <div className="desc" style={{maxWidth:460,margin:'0 auto 20px'}}>
              Bring your own clips into Highlightz to edit and publish. Included with
              Pro, along with the VOD scanner, 10 monitored streams and a 200-clip queue.
            </div>
            <a href="/billing/portal" className="rd-btn grad" style={{textDecoration:'none',display:'inline-flex',gap:8,alignItems:'center'}}>
              <Icon name="zap" size={14}/>Upgrade to Pro — $25/month
            </a>
          </div>
        </div>
      </div>
    );
  }

  const pct = quota && quota.limit ? Math.min(100, Math.round(quota.used/quota.limit*100)) : 0;
  const running = Object.entries(prog);

  return (
    <div className="rd-scroll">
      <div className="rd-settings">
        <div className="rd-section-title">
          <h2>Clip Editor</h2>
          {uploadsOn &&
            <span className="cnt">{uploads.length} clip{uploads.length===1?'':'s'} in your library</span>}
        </div>

        {/* Admins bypass the release flags to exercise features on prod. Say
            so plainly, and name the exact flag still off — previewing a hidden
            feature looks identical to a launched one, and that is how
            something ships by accident. */}
        {me && me.is_admin && me.features && !(me.features.uploads && me.features.clip_import) &&
          <div style={{display:'flex',alignItems:'center',gap:8,padding:'8px 12px',borderRadius:12,
                       background:'rgba(255,138,76,.12)',border:'1px solid rgba(255,138,76,.32)',
                       fontSize:12,color:'var(--pending)',fontWeight:600}}>
            <Icon name="cog" size={15}/>
            <span>Admin preview — parts of this screen are hidden from your users. Set{' '}
              {!me.features.clip_import && <code style={{fontFamily:'monospace'}}>CLIP_IMPORT_ENABLED=true</code>}
              {!me.features.clip_import && !me.features.uploads && ' and '}
              {!me.features.uploads && <code style={{fontFamily:'monospace'}}>UPLOADS_ENABLED=true</code>}
              {' '}to launch.</span>
          </div>}

        {/* The flow is not guessable from a dropzone alone: nothing on screen
            says an editor exists, what it can do, or where the result goes.
            Three steps, stated once, at the top. */}
        <div className="rd-how">
          {[['upload','1','Add a clip','Drop a file in, or pick one you already uploaded.'],
            ['film','2','Edit it','Trim, reframe for TikTok or Reels, add a caption.'],
            ['download','3','Export','Renders on your device and saves to your downloads.']
          ].map(([icon,n,title,body])=>(
            <div className="rd-step" key={n}>
              <span className="sn">{n}</span>
              <div>
                <div className="st"><Icon name={icon} size={13}/> {title}</div>
                <div className="sb">{body}</div>
              </div>
            </div>
          ))}
        </div>

        {/* Import is complete on its own and ships independently of uploads. */}
        {importOn && <TwitchImport/>}

        {uploadsOn && <>
        <div className="rd-card glass">
          <h3><span className="si"><Icon name="upload" size={15}/></span>Add clips</h3>
          <div className="desc">
            Drop a clip in and it opens in the editor — trim it, reframe it for
            vertical, add a caption, export. MP4, MOV or WebM, up to{' '}
            {quota?fmtBytes(quota.max_file):'300 MB'} each.
          </div>

          <div className={'rd-drop'+(over?' over':'')}
            onClick={()=>fileRef.current&&fileRef.current.click()}
            onDragOver={e=>{e.preventDefault();setOver(true);}}
            onDragLeave={()=>setOver(false)}
            onDrop={onDrop}>
            <div className="di"><Icon name="upload" size={30}/></div>
            <div className="dt">Drop a clip here to open the editor</div>
            <div className="ds">or click to choose a file · MP4, MOV or WebM</div>
          </div>
          <input ref={fileRef} type="file" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
            multiple style={{display:'none'}}
            onChange={e=>{ if(e.target.files?.length) send(e.target.files); e.target.value=''; }}/>

          {/* Anything already uploaded is one click from the editor. Without
              this the only visible route in is "upload something", which is a
              dead end for a user who already has clips here and just wants to
              re-cut one. */}
          {uploads.length>0 && <div style={{marginTop:12}}>
            <div className="ed-note" style={{marginBottom:8}}>
              Or edit one you've already uploaded:
            </div>
            <div className="rd-picks">
              {uploads.filter(u=>u.source!=='render').slice(0,8).map(u=>(
                <button key={u.id} className="rd-pick" onClick={()=>setEditing(u)}
                  title={'Edit ' + u.filename}>
                  <Icon name="film" size={13}/>
                  <span>{u.filename}</span>
                </button>
              ))}
            </div>
          </div>}

          {running.length>0 && <div style={{marginTop:12}}>
            {running.map(([id,p])=>(
              <div className="rd-uprow" key={id}>
                <div style={{fontSize:12,fontWeight:600,maxWidth:180,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{p.name}</div>
                <div className="pb"><i style={{width:(p.err?100:p.pct)+'%',background:p.err?'var(--danger)':undefined}}/></div>
                <div style={{fontSize:12,color:p.err?'var(--danger)':'var(--fg-3)',minWidth:76,textAlign:'right'}}>
                  {p.err ? p.err : (p.pct<100?p.pct+'%':'Processing...')}
                </div>
              </div>
            ))}
          </div>}

          {err && <div style={{marginTop:12,fontSize:12,color:'var(--danger)'}}>{err}</div>}

          {quota && <div style={{marginTop:16}}>
            <div className={'rd-quota'+(pct>=90?' rd-quota-full':'')}><i style={{width:pct+'%'}}/></div>
            <div style={{fontSize:12,color:'var(--fg-3)'}}>
              {fmtBytes(quota.used)} of {fmtBytes(quota.limit)} used · {fmtBytes(quota.remaining)} free
            </div>
          </div>}
        </div>

        <div className="rd-card glass">
          <h3><span className="si"><Icon name="film" size={15}/></span>Your clips</h3>
          <div className="desc">Everything you've uploaded. Hit Edit on any of them to trim,
            reframe and export — publishing straight to TikTok lands here next.</div>
          {uploads.length===0
            ? <div className="rd-grid-empty" style={{padding:'32px 0'}}>
                <div className="ic"><Icon name="film" size={38}/></div>
                <div className="big">No clips uploaded yet</div>
                <div>Drop a clip above and the editor opens automatically.</div>
              </div>
            : <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(260px,1fr))',gap:12}}>
                {uploads.map(u=>(
                  <div className="rd-up" key={u.id}>
                    <video src={u.url} controls preload="metadata"/>
                    <div className="ub">
                      <div style={{minWidth:0,flex:1}}>
                        <div className="un" title={u.filename}>{u.filename}</div>
                        <div className="um">{fmtBytes(u.size)} · {u.kind.toUpperCase()}</div>
                      </div>
                      <button className="rd-btn sm" onClick={()=>setEditing(u)} title="Edit clip">
                        <Icon name="film" size={13}/>&nbsp;Edit
                      </button>
                      <button className="rd-btn danger sm" onClick={()=>del(u.id)} title="Delete clip">
                        <Icon name="trash" size={13}/>
                      </button>
                    </div>
                  </div>
                ))}
              </div>}
        </div>
        </>}
      </div>
      {editing && <ClipEditor clip={editing} onClose={()=>setEditing(null)} captionsOn={captionsOn} platforms={platforms}
        schedulerOn={!!(me && (me.plan_limits?.uploads || me.is_admin))}/>}
    </div>
  );
}

function ScanActivity({ job }) {
  // Ticks locally once a second. This is the part that actually answers "is it
  // hung?": the sweep and the percentage both come from the server, so if the
  // job or the socket died they would freeze together and look identical to a
  // slow scan. A counter driven by the browser's own clock keeps moving only
  // while the tab is alive, and stops the moment the job reports done.
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  // created_at (the server's own start time) wins, so reopening the tab or
  // reconnecting mid-scan shows how long the job has REALLY been running
  // instead of restarting the clock. started_at is only the fallback for a job
  // first seen over the socket, which carries no created_at.
  const startedAt = job.created_at ? job.created_at * 1000 : (job.started_at || now);
  const secs = Math.max(0, Math.floor((now - startedAt) / 1000));
  const mins = Math.floor(secs / 60);
  const elapsed = mins > 0 ? mins + 'm ' + (secs % 60) + 's' : secs + 's';

  // Named per phase because "Scanning chat…" through a multi-minute audio
  // decode is actively misleading — the user is told the wrong thing is slow.
  const LABEL = {
    fetch: 'Reading chat replay…',
    audio: 'Listening to the stream…',
    score: 'Scoring moments…',
  };
  const label = LABEL[job.phase] || 'Scoring moments…';
  const detail = job.phase === 'audio' && job.audio_seconds
    ? Math.floor(job.audio_seconds / 60) + ' min of audio decoded'
    : (job.phase === 'fetch' && job.messages
        ? job.messages.toLocaleString() + ' messages'
        : '');

  return (
    <div style={{marginBottom:12}}>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'baseline',
                   fontSize:12,color:'var(--fg-2)',marginBottom:4,gap:8}}>
        <span style={{minWidth:0}}>
          <span className="rd-livedot"/>{label}
          {detail && <span style={{color:'var(--fg-3)'}}> · {detail}</span>}
        </span>
        <span style={{fontVariantNumeric:'tabular-nums',flexShrink:0,color:'var(--fg-3)'}}>
          {elapsed} · {Math.round(job.progress||0)}%
        </span>
      </div>
      <div className="rd-track working" style={{height:6}}>
        <div className="rd-fill" style={{width:(job.progress||0)+'%',background:'var(--grad)',
                                         transition:'width var(--dur-slow) ease'}}/>
      </div>
      {job.phase === 'audio' && (
        <div style={{fontSize:12,color:'var(--fg-3)',marginTop:4,lineHeight:1.5}}>
          Audio scans take a few minutes — the stream is being listened to so loud
          moments get caught even when chat is quiet. You can leave this tab.
        </div>
      )}
    </div>
  );
}

function VodScreen({ clips, me }) {
  // Whether this box decodes VOD audio (VOD_AUDIO_ENABLED). It changes both what
  // a scan does and how long it takes, so the screen describes the scan it is
  // actually running rather than the chat-only one it used to be.
  const audioOn = !!(me && me.features && me.features.vod_audio);
  const [url, setUrl]         = useState('');
  const [preset, setPreset]   = useState('default');
  const [jobs, setJobs]       = useState([]);
  const [scanning, setScanning] = useState(false);
  const [err, setErr]         = useState('');
  const [activeJob, setActiveJob] = useState(null);
  const jobRef = useRef({});

  useEffect(()=>{
    const load = ()=>fetch('/vod/jobs').then(r=>r.ok?r.json():[]).then(j=>{ setJobs(j); j.forEach(jb=>{ jobRef.current[jb.id]=jb; }); }).catch(()=>{});
    load();
    // Re-pull on WS reconnect/deploy so an in-flight scan doesn't freeze stale.
    window.addEventListener('hz_refetch', load);
    return ()=>window.removeEventListener('hz_refetch', load);
  },[]);

  // Listen for VOD events from the parent WebSocket
  useEffect(()=>{
    const handler = e => {
      try {
        const msg = JSON.parse(e.detail);
        if(msg.event==='vod_progress'){
          setJobs(prev=>prev.map(j=>j.id===msg.job_id?{...j,progress:msg.progress,
            // phase/messages/audio_seconds ride along in the broadcast already
            // (api.py spreads **meta); they were being dropped here, which is
            // why the label always read "Scanning chat".
            ...(msg.phase?{phase:msg.phase}:{}),
            ...(msg.messages!==undefined?{messages:msg.messages}:{}),
            ...(msg.audio_seconds!==undefined?{audio_seconds:msg.audio_seconds}:{}),
            started_at: j.started_at || Date.now(),
            ...(msg.vod_title?{vod_title:msg.vod_title,channel:msg.channel,duration:msg.duration,game:msg.game}:{})}:j));
        } else if(msg.event==='vod_moment'){
          setJobs(prev=>prev.map(j=>j.id===msg.job_id?{...j,moments:[...(j.moments||[]),msg.moment]}:j));
        } else if(msg.event==='vod_done'){
          setJobs(prev=>prev.map(j=>j.id===msg.job_id?{...j,status:'done',progress:100}:j));
          setScanning(false);
        } else if(msg.event==='vod_error'){
          setJobs(prev=>prev.map(j=>j.id===msg.job_id?{...j,status:'failed',error:msg.error}:j));
          setErr(msg.error||'Analysis failed');
          setScanning(false);
        }
      } catch {}
    };
    window.addEventListener('hz_ws', handler);
    return ()=>window.removeEventListener('hz_ws', handler);
  },[]);

  const analyze = async () => {
    const u = url.trim();
    if(!u){setErr('Paste a Twitch VOD URL first');return;}
    setErr(''); setScanning(true);
    try {
      const r = await fetch('/vod/analyze', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({vod_url:u, preset}),
      });
      if(!r.ok){ const d=await r.json().catch(()=>({})); setErr(d.detail||'Failed to start'); setScanning(false); return; }
      const job = await r.json();
      setJobs(prev=>[job,...prev]);
      setActiveJob(job.id);
      setUrl('');
    } catch { setErr('Network error'); setScanning(false); }
  };

  const cancelJob = async (id) => {
    await fetch(`/vod/jobs/${id}`,{method:'DELETE'});
    setJobs(prev=>prev.filter(j=>j.id!==id));
    if(activeJob===id){setActiveJob(null);setScanning(false);}
  };

  const fmtDuration = s => {
    if(!s) return '';
    const h=Math.floor(s/3600), m=Math.floor((s%3600)/60);
    return h>0?`${h}h ${m}m`:`${m}m`;
  };

  const shown = activeJob ? jobs.filter(j=>j.id===activeJob) : jobs;
  const PRESETS=['default','fps','chess','irl','small','variety','moba','casino','sports'];

  // Plan gate: the VOD scanner is Pro-only. The backend enforces this (403 on
  // /vod/analyze); the UI mirrors it with an upgrade card instead of a form
  // that errors. Checked here — after every hook — so hook order stays stable
  // while /me loads.
  if (me && me.plan_limits && !me.plan_limits.vod) {
    return (
      <div className="rd-scroll">
        <div className="rd-settings">
          <div className="rd-section-title"><h2>Past Streams</h2></div>
          <div className="rd-card glass" style={{textAlign:'center',padding:'48px 24px'}}>
            <div style={{marginBottom:12,color:'var(--acc)'}}><Icon name="film" size={40}/></div>
            <h3 style={{fontSize:17,marginBottom:8,justifyContent:'center'}}>VOD scanning is a Pro feature</h3>
            <div className="desc" style={{maxWidth:440,margin:'0 auto 20px'}}>
              Scan past broadcasts for highlights you missed — the formula replays the
              whole VOD's chat and surfaces the best moments. Included with Pro, along
              with 10 monitored streams and a 200-clip review queue.
            </div>
            <a href="/billing/portal" className="rd-btn grad" style={{textDecoration:'none',display:'inline-flex',gap:8,alignItems:'center'}}>
              <Icon name="zap" size={14}/>Upgrade to Pro — $25/month
            </a>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="rd-scroll">
      <div className="rd-settings">
        <div className="rd-section-title"><h2>Past Streams</h2><span className="cnt">Scan finished streams for highlights</span></div>

        <div className="rd-card glass">
          <h3><span className="si"><Icon name="video" size={15}/></span>Analyze a VOD</h3>
          <div className="desc">Paste a Twitch VOD URL and the bot will scan it for highlight moments{audioOn?' — chat replay plus the stream\u2019s audio.':' — no video download needed.'}</div>
          <div style={{display:'flex',flexDirection:'column',gap:12,marginTop:4}}>
            <input
              className="rd-input"
              placeholder="https://www.twitch.tv/videos/123456789"
              value={url}
              onChange={e=>setUrl(e.target.value)}
              onKeyDown={e=>e.key==='Enter'&&!scanning&&analyze()}
              disabled={scanning}
            />
            <div style={{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
              <select className="rd-select" style={{height:40}} value={preset} onChange={e=>setPreset(e.target.value)} disabled={scanning}>
                {PRESETS.map(p=><option key={p} value={p}>{p[0].toUpperCase()+p.slice(1)}</option>)}
              </select>
              <button className="rd-btn grad" onClick={analyze} disabled={scanning} style={{opacity:scanning?.6:1}}>
                {scanning?<><Spinner/>Scanning…</>:<><Icon name="zap" size={14}/>Scan VOD</>}
              </button>
              {jobs.length>1 && <button className="rd-btn sm" onClick={()=>setActiveJob(null)} style={{marginLeft:'auto'}}>
                All scans ({jobs.length})
              </button>}
            </div>
          </div>
          {err && <div style={{marginTop:8,padding:'8px 12px',borderRadius:10,background:'rgba(255,90,120,.08)',border:'1px solid rgba(255,90,120,.2)',color:'var(--danger)',fontSize:12}}>{err}</div>}
          <div style={{marginTop:12,padding:'8px 12px',borderRadius:10,background:'rgba(255,255,255,.03)',border:'1px solid var(--hair)',fontSize:12,color:'var(--fg-3)',lineHeight:1.6}}>
            <strong style={{color:'var(--fg-2)'}}>How it works:</strong> The bot pulls the VOD{audioOn?' chat replay and its audio track':' chat replay'}, then scans second-by-second with the same scoring engine as live monitoring — chat velocity, keywords, sentiment{audioOn?', and audio spikes':''}. When the score crosses the threshold, a moment is found. Each moment links to that exact timestamp in the VOD, and lands in your review queue automatically.{audioOn?' Audio scans take a few minutes; nothing is recorded or stored — only loudness is measured.':''}
          </div>
        </div>

        {shown.length>0 && shown.map(job=>(
          <div key={job.id} className="rd-card glass">
            <div style={{display:'flex',alignItems:'flex-start',gap:12,marginBottom:12}}>
              {job.thumbnail_url
                ? <img src={job.thumbnail_url} alt="" onError={e=>{e.target.style.display='none'}} style={{width:80,height:45,borderRadius:8,objectFit:'cover',flexShrink:0}}/>
                : <div style={{width:80,height:45,borderRadius:8,background:'var(--grad-soft)',flexShrink:0,display:'grid',placeItems:'center'}}><Icon name="video" size={18} style={{color:'var(--acc)'}}/></div>}
              <div style={{flex:1,minWidth:0}}>
                <div style={{fontWeight:700,fontSize:14,marginBottom:4,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>
                  {job.vod_title||`VOD ${job.vod_id}`}
                </div>
                <div style={{fontSize:12,color:'var(--fg-2)',display:'flex',gap:8,flexWrap:'wrap'}}>
                  {job.channel && <span>{job.channel}</span>}
                  {job.game && <span>{job.game}</span>}
                  {job.duration>0 && <span><Icon name="clock" size={11} style={{display:'inline',verticalAlign:'middle',marginRight:4}}/>{fmtDuration(job.duration)}</span>}
                </div>
              </div>
              <div style={{display:'flex',gap:4,alignItems:'center',flexShrink:0}}>
                {job.status==='running' && <button className="rd-btn sm danger" onClick={()=>cancelJob(job.id)}>Cancel</button>}
                {job.status==='done' && <button className="rd-btn sm" onClick={()=>cancelJob(job.id)} title="Remove"><Icon name="trash" size={13}/></button>}
              </div>
            </div>

            {job.status==='running' && <ScanActivity job={job}/>}

            {job.status==='failed' && (
              <div style={{padding:'8px 12px',borderRadius:10,background:'rgba(255,90,120,.08)',border:'1px solid rgba(255,90,120,.2)',color:'var(--danger)',fontSize:12,marginBottom:12}}>
                {job.error||'Analysis failed'}
              </div>
            )}

            {job.status==='done' && (
              <div style={{display:'flex',alignItems:'center',gap:8,fontSize:12,color:'var(--live)',fontWeight:600,marginBottom:12}}>
                <Icon name="check" size={14}/>
                {(job.moments||[]).length===0
                  ? 'No highlight moments found in this VOD.'
                  : `Found ${(job.moments||[]).length} highlight moment${(job.moments||[]).length===1?'':'s'} — added to your review queue`}
              </div>
            )}

            {(job.moments||[]).length>0 && (
              <div style={{display:'flex',flexDirection:'column',gap:8}}>
                <div className="rd-eyebrow" style={{marginBottom:4}}>Moments found · {(job.moments||[]).length}</div>
                {(job.moments||[]).map(m=>{
                  const sc = Math.round(m.score||0);
                  return (
                    <div key={m.id} style={{
                      display:'flex',alignItems:'center',gap:12,padding:'8px 12px',
                      borderRadius:12,background:'rgba(255,255,255,.03)',border:'1px solid var(--hair)',
                    }}>
                      <span style={{
                        minWidth:36,height:36,borderRadius:10,
                        background: sc>=75?'var(--live-soft)':sc>=50?'var(--pending-soft)':'var(--grad-soft)',
                        color: sc>=75?'var(--live)':sc>=50?'var(--pending)':'var(--acc)',
                        display:'grid',placeItems:'center',fontWeight:800,fontSize:12,flexShrink:0,
                        fontVariantNumeric:'tabular-nums',
                      }}>{sc}</span>
                      <div style={{flex:1,minWidth:0}}>
                        <div style={{fontWeight:600,fontSize:12}}>{m.timestamp}</div>
                        <div style={{fontSize:12,color:'var(--fg-3)',marginTop:4}}>
                          {(m.trigger_signals||[]).filter(s=>s.value>0.1).map(s=>s.type.replace('CHAT_','').replace('_',' ')).join(' · ')}
                        </div>
                      </div>
                      <a href={m.twitch_url} target="_blank" rel="noopener"
                         className="rd-btn sm" style={{textDecoration:'none',flexShrink:0}}>
                        <Icon name="play" size={12}/>Watch
                      </a>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ))}

        {jobs.length===0 && !scanning && (
          <div className="rd-grid-empty" style={{paddingTop:32}}>
            <div className="ic"><Icon name="video" size={42}/></div>
            <div className="big">No VOD scans yet</div>
            <div>Paste a Twitch VOD URL above to find highlight moments from any past stream.</div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ONE INPUT, ONE BUTTON. See the .fr note in the stylesheet for what this
   replaced and why. It deliberately renders INSTEAD of the app shell rather
   than inside it: a nav rail and a platform switch are answers to questions
   somebody with no channels has not asked yet.

   It calls the same onAdd the normal panel calls, so there is one add path and
   no second copy of the validation to drift. */
function FirstRun({ onAdd }) {
  const [ch, setCh] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const inputRef = useRef(null);
  useEffect(()=>{ if(inputRef.current) inputRef.current.focus(); },[]);
  const submit = async (e) => {
    if (e) e.preventDefault();
    // Deliberately written without a regex. This whole file is a Python
    // triple-quoted string, so a JS regex literal escaping a dot is an
    // invalid PYTHON escape — today a warning, and slated to become a
    // SyntaxError that stops the module importing. String operations do the
    // same job and cannot trip it.
    let raw = ch.trim();
    const cut = raw.lastIndexOf('twitch.tv/');
    if (cut >= 0) raw = raw.slice(cut + 10);
    const name = Array.from(raw).filter(c =>
      (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
      (c >= '0' && c <= '9') || c === '_').join('');
    if (!name) { setErr('Enter a Twitch channel name.'); return; }
    setBusy(true); setErr('');
    try { await onAdd(name, 'default', 'twitch'); }
    catch { setErr('Could not start watching that channel.'); }
    setBusy(false);
  };
  return (
    <div className="fr">
      <div className="fr-in">
        <img className="fr-mark" src="/static/logo-mark.png" alt="Highlightz"/>
        <h1>Add a channel to watch.</h1>
        <p className="fr-sub">Any live Twitch channel. Yours, or someone else&rsquo;s.</p>
        <form className="fr-row" onSubmit={submit}>
          <input ref={inputRef} className="rd-input" value={ch} placeholder="twitch.tv/"
            onChange={e=>setCh(e.target.value)} aria-label="Twitch channel name"
            autoComplete="off" spellCheck="false"/>
          <button className="rd-btn grad" type="submit" disabled={busy||!ch.trim()}>
            <Icon name="zap" size={15}/>{busy?'Starting':'Watch'}
          </button>
        </form>
        {err && <div className="fr-err">{err}</div>}
        <div className="fr-note">Scoring starts within seconds of the channel going live.</div>
      </div>
    </div>
  );
}

/* THE 1.5s WAKE. Three beats, driven by class not by keyframes, so every step
   is transform and opacity and reduced-motion can flatten the whole thing to
   its final frame by disabling one transition rule.

   The numeral counts from zero to whatever the channel is actually scoring.
   It is tabular and min-width:3ch so the box cannot resize as digits land —
   a counter that reflows its own container is the jitter this project already
   fixed once on the landing page. */
function WakeSequence({ channel, platform = 'twitch', score, onDone }) {
  const [beat, setBeat] = useState(0);
  const [n, setN] = useState(0);
  const reduced = typeof matchMedia === 'function' &&
    matchMedia('(prefers-reduced-motion: reduce)').matches;
  // Plays on EVERY channel added (owner, 2026-09-15: "pop up every time …
  // a person can just skip it by clicking"), so it has to get out of the way
  // on demand: a click anywhere, Escape or Enter ends it. `finished` makes
  // onDone fire once whichever of the timer and the click comes first.
  const finished = useRef(false);
  const done = () => { if (finished.current) return; finished.current = true; onDone(); };
  useEffect(()=>{
    const onKey = (e) => { if (e.key === 'Escape' || e.key === 'Enter' || e.key === ' ') done(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  },[]);
  useEffect(()=>{
    if (reduced) { setBeat(3); setN(score||0); const t=setTimeout(done,300); return ()=>clearTimeout(t); }
    const ts = [setTimeout(()=>setBeat(1), 30),
                setTimeout(()=>setBeat(2), 400),
                setTimeout(()=>setBeat(3), 900),
                setTimeout(done, 1500)];
    return ()=>ts.forEach(clearTimeout);
  },[]);
  useEffect(()=>{
    if (beat < 3 || reduced) return;
    const target = score||0; const started = Date.now();
    const id = setInterval(()=>{
      const t = Math.min(1,(Date.now()-started)/520);
      setN(Math.round(target*t));
      if (t>=1) clearInterval(id);
    }, 40);
    return ()=>clearInterval(id);
  },[beat]);
  const cls = 'wake' + (beat>=1?' b1':'') + (beat>=2?' b2':'') + (beat>=3?' b3':'');
  const platName = platform === 'kick' ? 'Kick' : 'Twitch';
  return (
    <div className={cls} role="status" aria-live="polite" onClick={done} title="Click to skip">
      <div className="wake-in">
        <span className="wake-chip"><span className="dot"/>{channel} · {platName}</span>
        <div className="wake-frame">
          <span className="wake-score">{n}</span>
          <div className="wake-rule"/>
          <div className="wake-lab">live score &middot; watching</div>
          <div className="wake-status">Watching {channel} on {platName}. Clips appear the moment one fires.</div>
        </div>
        <div className="wake-skip">Click anywhere to skip</div>
      </div>
    </div>
  );
}

/* WelcomeOverlay lived here: a fixed-inset blurred modal with a logo, a
   heading, a lead paragraph, five numbered steps and a CTA — about 250 words
   standing between a new account and the product it had just signed up for.
   Nothing in it was untrue and nothing in it was needed: every claim is on the
   landing page and in the walkthrough, both of which the user has already had
   the chance to read, and the one claim that matters — that a score moves and
   crosses a line — is now shown rather than described, 1.5 seconds after they
   type a channel name.

   Its render was removed with the rest of phase 4; the component is deleted
   here so it cannot be reinstated by uncommenting one line. */

// Shared "not ready yet" screen. Two callers with different palettes: Kick
// (green) and held-back features like Clip Editor (the app's purple), so the
// screen reads as part of whatever the user was looking at.
const UC_THEME = {
  kick:   { a:'#53fc18', b:'#39b515' },
  violet: { a:'var(--acc)', b:'#b86adc' },
};

function UnderConstruction({ theme='kick', title='Kick is coming soon', children, note }) {
  const { a, b } = UC_THEME[theme] || UC_THEME.kick;
  const tint = (o)=>theme==='kick'?`rgba(83,252,24,${o})`:`rgba(184,106,220,${o})`;
  return (
    <div style={{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',
                 textAlign:'center',minHeight:'70vh',padding:'32px 24px',gap:24}}>
      <div style={{width:96,height:96,borderRadius:26,display:'grid',placeItems:'center',color:a,
                   background:tint(.1),border:'1px solid '+tint(.32),
                   boxShadow:'0 12px 40px -14px '+tint(.45)}}><Icon name="cog" size={44}/></div>
      <div style={{display:'inline-flex',alignItems:'center',gap:8,padding:'8px 16px',borderRadius:999,
                   background:tint(.12),border:'1px solid '+tint(.35),
                   color:a,fontWeight:800,fontSize:12,letterSpacing:'.14em',textTransform:'uppercase'}}>
        <span style={{width:8,height:8,borderRadius:'50%',background:a,boxShadow:'0 0 10px '+a}}/>
        Under Construction
      </div>
      <h1 style={{margin:0,fontSize:44,fontWeight:900,letterSpacing:'-.02em',
                  background:`linear-gradient(135deg,${a},${b})`,
                  WebkitBackgroundClip:'text',WebkitTextFillColor:'transparent',backgroundClip:'text'}}>
        {title}
      </h1>
      <p style={{margin:0,maxWidth:560,fontSize:16,lineHeight:1.6,color:'var(--fg-2)'}}>
        {children}
      </p>
      <p style={{margin:0,fontSize:12,color:'var(--fg-3)'}}>
        {note || "Thanks for your patience — we'll flip this on the moment it's solid."}
      </p>
    </div>
  );
}

function KickUnderConstruction() {
  return (
    <UnderConstruction theme="kick" title="Kick is coming soon">
      We're building fully-automated Kick clipping to the same standard as our Twitch detection.
      It isn't ready yet, so this section is temporarily closed off. In the meantime, switch back to
      <b style={{color:'var(--fg)'}}> Twitch</b> to keep capturing highlights.
    </UnderConstruction>
  );
}

function UploadsUnderConstruction() {
  return (
    <UnderConstruction theme="violet" title="Clip Editor is coming soon">
      Bring your own clips in to edit, reframe for vertical, and publish straight to
      TikTok and Instagram. The upload side is built — we're finishing the editing and
      publishing half before switching it on, because half a feature is worse than none.
    </UnderConstruction>
  );
}

function RdApp() {
  const [route, setRoute] = useState('review');
  // Mobile nav drawer. Desktop CSS ignores the class entirely (the rail is
  // always visible there), so this state is inert above the breakpoint.
  const [navOpen, setNavOpen] = useState(false);
  // THE WELCOME MODAL IS RETIRED. It opened on a brand-new account with ~250
  // words and five numbered steps, blocking the product behind a document. The
  // first-run screen below replaces it: one input, one button. The localStorage
  // key is still read once, and only to decide whether somebody has been here
  // before — never to show the overlay again.
  const [wake, setWake] = useState(null);       // {channel, score} during the 1.5s wake
  const seenBefore = (()=>{ try { return !!localStorage.getItem('hz_welcome_seen'); } catch { return true; } })();
  const [streams, setStreams] = useState({});
  const [scores, setScores] = useState({});
  const [profiles, setProfiles] = useState({});
  const [histories, setHistories] = useState({});
  const [clips, setClips] = useState({});
  const [activePlatform, setActivePlatform] = useState(()=>{ try{return localStorage.getItem('hz_platform')||'twitch';}catch{return 'twitch';} });
  const switchPlatform = p => {
    setActivePlatform(p);
    try{localStorage.setItem('hz_platform',p);}catch{}
  };
  const [toast, setToast] = useState('');
  const [modalClip, setModalClip] = useState(null);
  const [me, setMe] = useState({username:'', avatar_url:''});
  // Publishing targets + their limits, from the server so the editor's
  // fit-check and src/publish/platforms.py can never disagree.
  const [platforms, setPlatforms] = useState([]);
  // The posting queue, and the accounts the server can post to. Both are
  // server state that changes under an open tab (the poster writes results
  // as it uploads; a connect finishes in another tab), so both are pulled in
  // refetchAll and updated by their events.
  const [queue, setQueue] = useState([]);
  const [connections, setConnections] = useState([]);
  const refetchConnections = useCallback(()=>{
    fetch('/publish/connections').then(r=>r.ok?r.json():null)
      .then(d=>{ if(d) setConnections(d.platforms||[]); }).catch(()=>{});
  },[]);
  // Autopilot settings: {config, font_ok, captions_available}. Null until
  // /autopilot answers (403 below Pro leaves it null and the card hidden).
  const [autopilot, setAutopilot] = useState(null);
  const refetchAutopilot = useCallback(()=>{
    fetch('/autopilot').then(r=>r.ok?r.json():null).then(d=>{ if(d) setAutopilot(d); }).catch(()=>{});
  },[]);
  // {clips} while the review prompt is open, null otherwise.
  const [reviewAsk, setReviewAsk] = useState(null);
  // Clips DELETED by the pending cap. Not 'missed' — the new clip is kept
  // and the oldest unreviewed one is dropped, which is what the notice says.
  const [lostClips, setLostClips] = useState(null);
  // Channels Twitch is refusing to clip FOR THIS USER. Server-scoped and
  // server-filtered: it only ever contains this account's channels, and rows
  // the user dismissed are already gone by the time they arrive here.
  const [refusals, setRefusals] = useState([]);
  // Announcements from the operator, in front of everything until dismissed.
  // Server-filtered: only the ones this account has not closed arrive here.
  const [announcements, setAnnouncements] = useState([]);
  // Full showcase entries (ordered) — the Landing Page screen renders these,
  // and the clip modal only needs the id set, so derive that from them.
  const [featured, setFeatured] = useState([]);
  const featuredIds = featured.map(f=>f.id);
  const toastTimer = useRef(null);

  const flash = useCallback(msg => {
    setToast(msg);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(()=>setToast(''), 3000);
  }, []);

  // UNDO. Rather than every destructive button remembering to offer it, this
  // hangs off clip_removed — the one event all four of them (reject, delete,
  // cull, clear queue) already broadcast. One hook point, and any future
  // destructive action gets undo for free. Debounced because clearing a queue
  // emits one event per clip and the buffer only has one entry to report.
  // Unread FEEDBACK REPLIES for this user (for an admin the same endpoint
  // reports unanswered feedback instead — see the server).
  const [fbUnread, setFbUnread] = useState(0);
  const loadFbUnread = useCallback(()=>{
    fetch('/feedback/unread-count').then(r=>r.ok?r.json():null)
      .then(d=>{ if(d) setFbUnread(d.count||0); }).catch(()=>{});
  }, []);
  const [undoable, setUndoable] = useState(null);
  const undoTimer = useRef(null);
  const checkUndo = useCallback(() => {
    clearTimeout(undoTimer.current);
    undoTimer.current = setTimeout(() => {
      fetch('/clips/undo').then(r=>r.ok?r.json():null)
        .then(d => setUndoable(d && d.id ? d : null)).catch(()=>{});
    }, 400);
  }, []);
  const doUndo = useCallback(() => {
    const id = undoable && undoable.id;
    setUndoable(null);
    fetch('/clips/undo' + (id ? '?entry_id=' + encodeURIComponent(id) : ''), {method:'POST'})
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) flash('Restored ' + d.restored + ' clip' + (d.restored===1?'':'s')); })
      .catch(()=>{});
  }, [undoable, flash]);

  // The walkthrough, shown on its own tab. Static content, but it is pulled in
  // refetchAll for the same reason the platform list is: a deploy that rewrites
  // a step has to reach an already-open tab, and a reconnect is the only moment
  // we get to notice one happened.
  const [tutorial, setTutorial] = useState(null);

  // Single source of truth for loading all live state. Called once on mount and
  // again on every WebSocket (re)connect so the UI fully self-heals after any
  // disconnect (laptop sleep, network blip, server restart on deploy) without a
  // manual page refresh — anything that changed during the gap is pulled fresh.
  const refetchAll = useCallback(()=>{
    loadFbUnread();
    Promise.all([fetch('/clips').then(r=>r.json()), fetch('/streams').then(r=>r.json())])
      .then(([ca,sa])=>{
        setClips(Object.fromEntries(ca.map(c=>[c.id,c])));
        setStreams(Object.fromEntries(sa.map(s=>[s.channel,s])));
      }).catch(()=>{});
    fetch('/profiles').then(r=>r.json()).then(arr=>{
      setProfiles(Object.fromEntries(arr.map(p=>[p.channel,p])));
    }).catch(()=>{});
    fetch('/me').then(r=>r.json()).then(data=>{
      setMe(data);
      // The broadcast covers the live case; this covers a tab opened after
      // the milestone was crossed, and any reconnect.
      if(data && data.review_prompt) setReviewAsk(p=>p||{clips:0});
      // The event is the live nudge; this is the state, so the notice
      // survives a reload and a reconnect.
      // REPLACE, never `p=>p||...`. Keeping the first value meant a later
      // /me could not lower the count or clear the banner, so once it appeared
      // it stayed for the life of the tab — half of why it felt permanent.
      // The server counts only misses since the last dismissal, so this both
      // updates and clears correctly.
      if(data && data.clips_lost_24h > 0)
        setLostClips({missed_24h:data.clips_lost_24h, plan:data.plan,
                      limit:(data.plan_limits||{}).max_pending,
                      next_plan:(data.next_plan||{}).plan,
                      next_limit:(data.next_plan||{}).max_pending,
                      next_price:(data.next_plan||{}).price});
      else setLostClips(null);
    }).catch(()=>{});
    // Static config, but it still belongs here: refetchAll runs on every
    // reconnect, so a deploy that changes a platform limit reaches open
    // tabs without anyone being told to refresh.
    // In refetchAll, not just on mount: a channel can start or stop being
    // refused while the tab sits open, and a reconnect (sleep, deploy) is the
    // one moment we get to notice we missed the event that said so.
    fetch('/clip-refusals').then(r=>r.json()).then(d=>setRefusals(d.rows||[])).catch(()=>{});
    // Announcements: the socket event reaches tabs open at the moment of
    // sending; this reaches everyone else on their next open or reconnect.
    fetch('/announcements').then(r=>r.json()).then(d=>setAnnouncements(d.rows||[])).catch(()=>{});
    fetch('/publish/platforms').then(r=>r.json()).then(d=>setPlatforms(d.platforms||[])).catch(()=>{});
    fetch('/publish/schedule').then(r=>r.json()).then(d=>setQueue(d.items||[])).catch(()=>{});
    refetchConnections();
    refetchAutopilot();
    // Which clips are featured on the landing page (admin curation state).
    fetch('/landing/showcase').then(r=>r.json()).then(d=>setFeatured(d.clips||[])).catch(()=>{});
    fetch('/tutorial/content').then(r=>r.ok?r.json():null)
      .then(d=>{ if(d) setTutorial(d); }).catch(()=>{});
    // Tell screen-local data sources (VOD jobs, Settings stats) to re-pull too,
    // so they self-heal on reconnect/deploy instead of going stale.
    window.dispatchEvent(new CustomEvent('hz_refetch'));
  },[]);
  const wsBootstrapped = useRef(false);

  useEffect(()=>{
    refetchAll();
    // Surface Kick OAuth results from redirect params
    const _params = new URLSearchParams(location.search);
    if (_params.get('kick_linked')) {
      flash('Kick account connected successfully!');
      setRoute('account');
      history.replaceState(null,'',location.pathname);
    } else if (_params.get('kick_error')) {
      const detail = _params.get('kick_detail');
      flash('Kick connection failed' + (detail ? ': ' + decodeURIComponent(detail) : ' — check server logs'));
      history.replaceState(null,'',location.pathname);
    } else if (_params.get('connected')) {
      // Back from a YouTube/TikTok/Instagram consent screen. The connection
      // itself arrives over the socket; this just lands them on the tab.
      const which = {youtube:'YouTube', tiktok:'TikTok', instagram:'Instagram'}[_params.get('connected')] || 'Account';
      flash(which + ' connected — Highlightz can post there for you now.');
      setRoute('schedule');
      history.replaceState(null,'',location.pathname);
    } else if (_params.get('connect_error')) {
      flash('Could not connect ' + decodeURIComponent(_params.get('connect_error')));
      setRoute('schedule');
      history.replaceState(null,'',location.pathname);
    }
  },[]);

  // Escape closes the nav drawer, and a resize up to desktop drops the open
  // state so returning to mobile doesn't reopen it unasked.
  useEffect(()=>{
    const onKey = e => { if(e.key==='Escape') setNavOpen(false); };
    const onResize = () => { if(window.innerWidth > 700) setNavOpen(false); };
    window.addEventListener('keydown', onKey);
    window.addEventListener('resize', onResize);
    return ()=>{ window.removeEventListener('keydown', onKey); window.removeEventListener('resize', onResize); };
  },[]);

  useEffect(()=>{
    const proto = location.protocol==='https:'?'wss':'ws';
    let ws;
    const connect = ()=>{
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      // The very first open is covered by the mount fetch; every open after that
      // is a reconnect, so pull all state fresh to heal whatever was missed while
      // the socket was down. No manual refresh ever needed.
      ws.onopen = ()=>{ if(wsBootstrapped.current) refetchAll(); wsBootstrapped.current = true; };
      ws.onmessage = e=>{
        const msg = JSON.parse(e.data);
        // Re-broadcast on the in-page channel so other screens (e.g. Settings
        // usage stats) can react live to clip changes without their own socket.
        if(['clip_ready','clip_updated','clip_removed'].includes(msg.event))
          window.dispatchEvent(new CustomEvent('hz_ws',{detail:e.data}));
        if(msg.event==='clip_removed') checkUndo();
        // An answer to your feedback. Light the nav badge immediately and let
        // the Feedback screen pull the thread — it may not even be open.
        if(msg.event==='feedback_reply'){
          loadFbUnread();
          window.dispatchEvent(new CustomEvent('hz_fb_reply'));
          flash('You have a reply to your feedback');
        }
        // Us reaching out FIRST, on a thread they never opened. Same plumbing,
        // different words: telling somebody they have "a reply to your
        // feedback" when they never sent any reads as a bug in our product,
        // not as a message from us.
        if(msg.event==='feedback_message'){
          loadFbUnread();
          window.dispatchEvent(new CustomEvent('hz_fb_reply'));
          flash('New message from Highlightz');
        }
        // The other direction — a user answered on their thread. Only admins
        // receive this (it is fanned out by id, never broadcast to everyone).
        if(msg.event==='feedback_new'){
          loadFbUnread();
          window.dispatchEvent(new CustomEvent('hz_fb_reply'));
          flash((msg.username||'Someone') + ' replied to their feedback');
        }
        // A crowd suggestion arrives on the same event as a caught clip —
        // deliberately, so the realtime path needed no new wiring — but
        // "New clip from aceu" would credit the detector for a moment it
        // missed. The toast is the only notice a user watching another screen
        // gets, so it says which of the two just happened.
        if(msg.event==='clip_ready'){setClips(p=>({...p,[msg.clip.id]:msg.clip}));
          flash(msg.clip.suggested
            ? 'Highlight from '+msg.clip.channel
            : 'New clip from '+msg.clip.channel);}
        else if(msg.event==='clip_updated'){
          setClips(p=>({...p,[msg.clip.id]:msg.clip}));
          setModalClip(prev=>prev&&prev.id===msg.clip.id?msg.clip:prev);
        }
        else if(msg.event==='clip_removed'){setClips(p=>{const n={...p};delete n[msg.clip_id];return n;});}
        // The capture finished and the clip is now downloadable. It arrives
        // SECONDS AFTER clip_ready — the tail of the moment has to be
        // broadcast and buffered before it can be cut — so the card has
        // already been on screen without a Download button, and this is what
        // makes it appear without a refresh. Patching the one field rather
        // than refetching: the clip is otherwise unchanged, and a full pull
        // would fight an open review queue for no reason.
        else if(msg.event==='clip_file_ready'){
          // Both fields, not just has_file: the card reads file_state now, and
          // leaving it at 'pending' would keep saying "Preparing" over a file
          // that is sitting on disk ready to serve.
          const rdy = o => ({...o, has_file:true, file_state:'ready'});
          setClips(p=>p[msg.clip_id]?{...p,[msg.clip_id]:rdy(p[msg.clip_id])}:p);
          setModalClip(prev=>prev&&prev.id===msg.clip_id?rdy(prev):prev);
          // The same event also closes a fetch THIS tab asked for. If the
          // person pressed Download, the file is on disk now — start it, so
          // the button they already pressed does not need pressing again.
          if(wantDownload.current.delete(msg.clip_id))
            window.location.assign('/clips/'+msg.clip_id+'/file?download=1');
        }
        // A fetch from Twitch did not produce a file. Say so and put the
        // Download button back, or the card sits on "Fetching" forever.
        else if(msg.event==='clip_fetch_failed'){
          wantDownload.current.delete(msg.clip_id);
          setFileState(msg.clip_id, 'missed');
          flash(msg.message||'Could not fetch that clip from Twitch');
        }
        else if(msg.event==='stream_added'||msg.event==='stream_updated'){setStreams(p=>({...p,[msg.stream.channel]:msg.stream}));}
        else if(msg.event==='stream_removed'){setStreams(p=>{const n={...p};delete n[msg.channel];return n;});}
        else if(msg.event==='stream_status'){setStreams(p=>p[msg.channel]?{...p,[msg.channel]:{...p[msg.channel],status:msg.status}}:p);}
        else if(msg.event==='score_update'){
          // 'at' powers the per-card engine heartbeat: updates arrive ~1/s from
          // a running worker, so a growing gap means stalled, not quiet.
          setScores(p=>({...p,[msg.channel]:{score:msg.score,breakdown:msg.breakdown||{},at:Date.now()}}));
          setHistories(p=>{const h=[...(p[msg.channel]||[]),msg.score].slice(-40);return{...p,[msg.channel]:h};});
        }
        else if(msg.event==='profile_updated'){setProfiles(p=>({...p,[msg.profile.channel]:msg.profile}));}
        else if(msg.event==='showcase_updated'){
          // Landing-page curation changed (another admin tab, or this one) —
          // keep every open Landing Page screen and clip modal in sync.
          fetch('/landing/showcase').then(r=>r.json()).then(d=>setFeatured(d.clips||[])).catch(()=>{});
        }
        else if(msg.event==='roles_updated'){
          // Admin granted/revoked a role (e.g. trainer) — re-pull /me so the
          // nav reflects it live, without a refresh.
          refetchAll();
          flash('Your account roles were updated.');
        }
        else if(msg.event==='streams_paused_idle'){flash('Your streams were paused after 8 hours of inactivity. Restart them from the Live Streams tab.');}
        // The row itself is already gone via stream_removed. This says WHY, and
        // that it is not permanent — a stream vanishing with no explanation is
        // indistinguishable from a bug to the person it happens to.
        else if(msg.event==='streams_stopped_by_admin'){
          flash(msg.channel
            ? 'Monitoring of ' + msg.channel + ' was stopped by an admin to free up server capacity. You can start it again from the Live Streams tab.'
            : (msg.count||0) + ' of your streams were stopped by an admin to free up server capacity. You can start them again from the Live Streams tab.');
        }
        else if(msg.event==='subscription_expired'){
          // Backend stopped this user's streams because their trial/subscription
          // lapsed. Pull fresh state so the account screen and stream list reflect
          // it live instead of waiting for a refresh.
          refetchAll();
          flash(msg.message||'Your subscription has expired — streams have been stopped.');
        }
        else if(msg.event==='subscription_active'){
          // Trial→paid, past_due recovery, or admin grant — refresh so the trial
          // banner / paywall clears and the account screen reflects access live.
          refetchAll();
          flash(msg.message||"Subscription active — you're all set.");
        }
        else if(msg.event==='clip_failed'){
          // A triggered/forced clip failed to capture — tell the user instead of
          // leaving them staring at a moment that never becomes a clip.
          flash(msg.message||'A clip could not be captured.');
        }
        else if(msg.event==='stream_error'){
          // A stream session hit a REAL error and is reconnecting. A channel
          // that is merely offline no longer comes through here at all — it
          // arrives as stream_status "offline", which is what it always was.
          // msg.error, not msg.message: the backend has only ever sent `error`,
          // so this always fell through to the generic string and the specific
          // reason was never shown to anyone.
          flash(msg.error||'A stream hit an error — reconnecting.');
        }
        // Forward VOD events to VodScreen via custom event
        else if(['vod_progress','vod_moment','vod_done','vod_error'].includes(msg.event)){
          window.dispatchEvent(new CustomEvent('hz_ws',{detail:e.data}));
        }
        // Forward Clip Editor events so a second open tab (or your phone)
        // reflects an upload/delete live instead of after a refresh.
        else if(['upload_added','upload_removed'].includes(msg.event)){
          window.dispatchEvent(new CustomEvent('hz_ws',{detail:e.data}));
        }
        // Captioning runs on the SERVER, so its progress has to arrive over the
        // socket — the tab that started it may not even be the one watching.
        else if(['captions_progress','captions_ready','captions_failed'].includes(msg.event)){
          window.dispatchEvent(new CustomEvent('hz_ws',{detail:e.data}));
        }
        // Forward team scoring ticks to the Training screen's live counter
        else if(msg.event==='miss_notice_dismissed'){ setLostClips(null); }
        // A channel started or stopped being refused. Re-pull rather than
        // patching from the event: the server is the only thing that knows
        // which rows this user may see and which they have dismissed, and
        // duplicating that filter in the browser is how the two drift apart.
        // Fires on a refusal, on a dismissal in another tab, and on recovery —
        // so the banner appears and disappears without a refresh.
        else if(msg.event==='refusals_changed'){
          fetch('/clip-refusals').then(r=>r.json())
            .then(d=>setRefusals(d.rows||[])).catch(()=>{});
        }
        // An announcement from the operator: in front of everything, now.
        // Appended (deduplicated by id) rather than refetched — the payload
        // IS the announcement, and a fetch would only re-read it.
        else if(msg.event==='announcement' && msg.announcement){
          setAnnouncements(p=>p.some(a=>a.id===msg.announcement.id)?p:[...p, msg.announcement]);
        }
        // Retired by the operator, or dismissed in one of this user's other
        // tabs. Either way it comes down here too.
        else if(msg.event==='announcement_retired' || msg.event==='announcement_seen'){
          setAnnouncements(p=>p.filter(a=>a.id!==msg.id));
        }
        // Clearing recents in one tab must clear them in every open tab. The
        // suggestion list lives inside AddStreamPanel and is fetched on open
        // rather than on mount, so there is no top-level state to update and
        // refetchAll() would not reach it — an in-page event is how the panel
        // hears about it wherever it happens to be mounted.
        else if(msg.event==='suggestions_cleared'){
          window.dispatchEvent(new CustomEvent('hz_suggestions_cleared'));
        }
        else if(msg.event==='clip_missed'){
          // Two different causes, two different messages. A backlog is not a
          // full queue: the upgrade banner would be telling them to buy a
          // bigger queue to fix something a bigger queue does not touch.
          if(msg.reason==='backlog'){
            flash('Too many moments at once — one on ' + (msg.channel||'your stream') + ' could not be captured in time');
          } else {
            setLostClips(msg);
            flash('Queue full — a highlight on ' + (msg.channel||'your stream') + ' was not clipped');
          }
        }
        else if(msg.event==='review_prompt'){ setReviewAsk({clips: msg.clips||0}); }
        else if(msg.event==='reviews_updated'){ /* landing page only; nothing to do here */ }
        else if(msg.event==='schedule_added'||msg.event==='schedule_updated'){
          setQueue(q=>{
            const rest = q.filter(i=>i.id!==msg.item.id);
            return [...rest, msg.item].sort((x,y)=>x.due_at-y.due_at);
          });
        }
        else if(msg.event==='schedule_removed'){
          setQueue(q=>q.filter(i=>i.id!==msg.item_id));
        }
        else if(msg.event==='autopilot_changed'){
          setAutopilot(a=>({...(a||{}), config: msg.config}));
        }
        else if(msg.event==='publish_connections_changed'){
          // Sent on connect, disconnect, and when the poster finds a token
          // dead. The event carries nothing; the list is the state.
          refetchConnections();
        }
        else if(msg.event==='schedule_due'){
          // The list is the source of truth (`due` is derived from the clock on
          // every read), so this only nudges — a missed event cannot lose a
          // reminder, it just arrives on the next fetch instead.
          setQueue(q=>q.map(i=>i.id===msg.item.id?msg.item:i));
          flash('Time to post: ' + (msg.item.filename||'your clip'));
        }
        else if(msg.event==='training_scored'){
          window.dispatchEvent(new CustomEvent('hz_ws',{detail:e.data}));
        }
      };
      ws.onclose = ()=>setTimeout(connect,3000);
    };
    connect();
    const ping = setInterval(()=>ws?.readyState===1&&ws.send('ping'),30000);
    return ()=>{clearInterval(ping);ws?.close();};
  },[flash]);

  const addStream = async(channel,preset,platform='twitch')=>{
    try{
      const r=await fetch('/streams',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel,platform,preset})});
      if(!r.ok){const e=await r.json();flash('Error: '+(e.detail||'failed'));return;}
      const s=await r.json();
      // Use s.channel (backend-normalised, lowercased) as the key so the
      // subsequent WebSocket stream_added event doesn't create a duplicate entry.
      const first = Object.keys(streams).length === 0;
      setStreams(p=>({...p,[s.channel]:s}));
      // The wake plays on EVERY channel added, Twitch or Kick (owner,
      // 2026-09-15; it used to play only on the very first). It is skippable
      // with a click, which is what keeps it from being a wall in front of a
      // dashboard the user is already using. The first-run flag still only
      // flips on the first add: it gates the welcome overlay, not the wake.
      if (first) { try { localStorage.setItem('hz_welcome_seen','1'); } catch {} }
      setWake({channel:s.channel, platform:s.platform||platform, score:0});
    }catch{flash('Failed to add stream');}
  };
  const removeStream = async(channel)=>{
    await fetch(`/streams/${encodeURIComponent(channel)}`,{method:'DELETE'});
    // Remove both the normalised key and any stale mixed-case key the user may
    // have typed, so the card disappears immediately without a refresh.
    setStreams(p=>{const n={...p};delete n[channel];delete n[channel.toLowerCase()];return n;});
    setScores(p=>{const n={...p};delete n[channel];delete n[channel.toLowerCase()];return n;});
    flash('Removed '+channel);
  };
  const forceClip = async(channel)=>{
    const r=await fetch(`/streams/${encodeURIComponent(channel)}/force-clip`,{method:'POST'});
    flash(r.ok?'Test clip queued for '+channel:'Failed — is stream active?');
  };
  const approveClip = async(id)=>{
    const r=await fetch(`/clips/${id}/approve`,{method:'POST'});
    if(r.ok){const u=await r.json();setClips(p=>({...p,[id]:u}));return true;}
    // A 403 is the weekly library cap, and it is the one failure here that is
    // not an error: the clip is fine, it is still in review, and the server
    // already wrote the sentence explaining that. Showing "it may have been
    // removed. Refreshing..." would say the opposite of what happened and
    // throw away a queue position the user can still use.
    if(r.status===403){
      const d=await r.json().catch(()=>null);
      flash((d&&d.detail)||'You have kept all the clips your plan allows this week.');
      return false;
    }
    flash('Could not approve clip — it may have been removed. Refreshing...');refetchAll();
    return false;
  };
  const rejectClip = async(id)=>{
    await fetch(`/clips/${id}/reject`,{method:'POST'});
    setClips(p=>{const n={...p};delete n[id];return n;});
  };
  // Which Twitch clips this admin already has, so the Grab button can say
  // "In yours" instead of offering an action the server will refuse.
  const myClipUrls = new Set(Object.values(clips).map(c=>c.twitch_url).filter(Boolean));

  const dismissMissNotice = ()=>{
    setLostClips(null);                       // instant; the POST is bookkeeping
    fetch('/me/dismiss-miss-notice',{method:'POST'}).catch(()=>{});
  };

  // One channel at a time, because one dismissal must not hide a different
  // channel breaking tomorrow. Removed locally first so the X is instant; the
  // POST persists it and its broadcast closes it in the user's other tabs.
  const dismissRefusal = (channel)=>{
    setRefusals(p=>p.filter(r=>r.channel!==channel));
    fetch('/clip-refusals/'+encodeURIComponent(channel)+'/dismiss',{method:'POST'})
      .catch(()=>{});
  };

  // "Got it" on an announcement. Removed locally so the modal closes at once
  // (revealing the next one, if any); the POST persists it on the account and
  // its broadcast closes the same modal in this user's other tabs.
  const dismissAnnouncement = (id)=>{
    setAnnouncements(p=>p.filter(a=>a.id!==id));
    fetch('/announcements/'+encodeURIComponent(id)+'/seen',{method:'POST'}).catch(()=>{});
  };

  // FETCH A CLIP'S VIDEO FROM TWITCH, on request. Cards and the modal ask for
  // it over the in-page event channel (hz_fetch_clip) rather than a prop
  // threaded through five render sites; this is the one place that owns it.
  //
  // Optimistic: the clip flips to 'fetching' immediately so the button cannot
  // be pressed twice, and the outcome arrives over the socket — clip_file_ready
  // (the same event a live capture emits) or clip_fetch_failed. If the person
  // pressed DOWNLOAD, the id is remembered so the file starts downloading by
  // itself when it lands; nobody should have to press the button twice.
  const wantDownload = useRef(new Set());
  const setFileState = (id, state)=>{
    setClips(p=>p[id]?{...p,[id]:{...p[id],file_state:state}}:p);
    setModalClip(prev=>prev&&prev.id===id?{...prev,file_state:state}:prev);
  };
  useEffect(()=>{
    const onFetch = async (e)=>{
      const {id, download} = (e.detail||{});
      if(!id) return;
      if(download) wantDownload.current.add(id);
      setFileState(id, 'fetching');
      try{
        const r = await fetch(`/clips/${id}/fetch`,{method:'POST'});
        if(r.ok){
          const d = await r.json().catch(()=>({}));
          // Already on disk: the socket will not say anything, so act now.
          if(d.file_state==='ready'){
            setClips(p=>p[id]?{...p,[id]:{...p[id],has_file:true,file_state:'ready'}}:p);
            setModalClip(prev=>prev&&prev.id===id?{...prev,has_file:true,file_state:'ready'}:prev);
            if(wantDownload.current.delete(id)) window.location.assign(`/clips/${id}/file?download=1`);
          }
          return;
        }
        let d='Could not fetch that clip';
        try{ d=(await r.json()).detail||d; }catch{}
        flash(d);
      }catch{ flash('Could not reach the server'); }
      wantDownload.current.delete(id);
      setFileState(id, 'missed');
    };
    window.addEventListener('hz_fetch_clip', onFetch);
    return ()=>window.removeEventListener('hz_fetch_clip', onFetch);
  },[]);

  const grabFeature = async (id)=>{
    try{
      const r = await fetch(`/admin/showcase/${id}/grab`, {method:'POST'});
      if(!r.ok){
        let d='Could not grab that clip';
        try{ d=(await r.json()).detail||d; }catch{}
        flash(d); return;
      }
      // The clip_ready broadcast puts it in the library; this is just the
      // confirmation that the click did something.
      flash('Added to your Clip Library');
    }catch{ flash('Could not reach the server'); }
  };

  const loadFeatured = ()=>fetch('/landing/showcase').then(r=>r.json())
    .then(d=>setFeatured(d.clips||[])).catch(()=>{});
  const toggleFeature = async(id)=>{
    const r=await fetch(`/admin/showcase/${id}`,{method:'POST'});
    if(!r.ok){
      const e=await r.json().catch(()=>({}));
      flash(e.detail||'Could not update landing page examples');return;
    }
    const d=await r.json();
    await loadFeatured();   // server owns order and the cap
    flash(d.featured ? 'Added to the landing page examples' : 'Removed from the landing page examples');
  };
  const moveFeature = async(id,dir)=>{
    const r=await fetch(`/admin/showcase/${id}/move?dir=${dir}`,{method:'POST'});
    if(r.ok) await loadFeatured();
  };
  // Where a featured clip appears: the hero wall, the examples grid, or both.
  const setPlacement = async(id,where,on)=>{
    const r=await fetch(`/admin/showcase/${id}/placement`,{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({where, on})});
    if(!r.ok){
      const e=await r.json().catch(()=>({}));
      flash(e.detail||'Could not change where that clip appears');return;
    }
    await loadFeatured();
  };
  // Captured clip → editor, with no download and no re-upload in between. The
  // server copies the file it already holds into the upload library and hands
  // back the record; all this does is carry the user to the tab it landed on.
  //
  // Idempotent on the server: sending the same clip twice returns the upload
  // that already exists rather than spending the quota again, so a double
  // click is harmless and needs no guard here beyond the busy flag.
  const [editorTarget, setEditorTarget] = useState(null);
  const [editorBusy, setEditorBusy] = useState(false);
  const sendToEditor = async(clip)=>{
    if(editorBusy) return;
    setEditorBusy(true);
    // Copying tens of megabytes is not instant on a busy box, and a button
    // that does nothing visible for two seconds reads as broken.
    flash('Opening in the editor...');
    try{
      const r = await fetch(`/clips/${clip.id}/to-editor`,{method:'POST'});
      if(!r.ok){
        let d='Could not open that clip in the editor';
        try{ d=(await r.json()).detail||d; }catch{}
        flash(d); return;
      }
      const up = await r.json();
      setEditorTarget(up);
      setRoute('uploads');
    }catch{ flash('Could not reach the server'); }
    finally{ setEditorBusy(false); }
  };

  const deleteClip = async(id)=>{
    if(!confirm('Delete this clip? This cannot be undone.')) return;
    // True delete — housekeeping only. Never routes through /reject: deleting
    // is "clear this out", not "this was a bad clip", so it must not move the
    // channel's threshold or feed the training data.
    await fetch(`/clips/${id}`,{method:'DELETE'});
    setClips(p=>{const n={...p};delete n[id];return n;});
    flash('Clip deleted');
  };

  const platformStreams = Object.fromEntries(Object.entries(streams).filter(([,s])=>s.platform===activePlatform));
  const platformClips   = Object.fromEntries(Object.entries(clips).filter(([,c])=>c.platform===activePlatform));
  const pending = Object.values(platformClips).filter(c=>c.status==='pending').length;
  // Held-back feature: released for everyone, or an admin previewing it.
  // Default false while /me is still loading, so the tab never flashes the
  // real screen before the flag arrives.
  const uploadsOn = !!(me && (me.features?.uploads || me.is_admin));
  const importOn  = !!(me && (me.features?.clip_import || me.is_admin));
  // Same shape as the two above: release flag, admin bypass. Threaded into
  // the editor so the Auto-captions panel is hidden rather than dead when
  // CAPTIONS_ENABLED is off.
  const captionsOn = !!(me && (me.features?.captions || me.is_admin));
  // The tab is worth showing if EITHER half is live. Import is complete on its
  // own (browse every clip on your channel); uploads are what's held back.
  const clipTabOn = uploadsOn || importOn;
  // Tabs bounced to the review queue for non-admins. Both held-back tabs used
  // to be here — not gated on a release flag, because UPLOADS_ENABLED once
  // went true in production and handed every Pro subscriber a working Editor
  // and Scheduler before either was a decision.
  //
  // THE EDITOR LEFT THIS LIST ON 2026-09-15 ("open up the editor to pro users
  // now") and THE SCHEDULER FOLLOWED THE SAME DAY ("I need the scheduler to
  // be working and integrated now"). Both are gated the way the VOD scanner
  // is — the tab shows for everyone, the screen itself is the paywall for
  // anyone without `plan_limits.uploads`, and the endpoints refuse
  // independently. The list stays so the mechanism is here for the next
  // held-back screen.
  const adminOnlyTabs = [];
  // Whether a clip card may offer "Edit clip". It has to match what the user
  // can actually reach: the release flag (or the screen is
  // UploadsUnderConstruction) AND a plan that includes the editor (or an
  // admin), or the button walks them to the paywall. The endpoint refuses
  // independently — this only stops us offering a dead end.
  const editorOn = uploadsOn && !!(me && (me.plan_limits?.uploads || me.is_admin));
  const onEditClip = editorOn ? sendToEditor : null;
  // The screen actually rendered. The nav is the only way in today (`route`
  // lives in React state alone), but that is a property of the current code,
  // not a guarantee — normalise so a future deep link or restored route cannot
  // walk into one of these. Falls back to the review queue.
  const view = (adminOnlyTabs.includes(route) && !(me && me.is_admin)) ? 'review' : route;

  let screen;
  // Kick is an admin-only beta: admins get every tab, everyone else the
  // "coming soon" screen. KICK_BLOCKED is the single source of truth, shared
  // with the nav below so a tab can never be clickable-but-dead (or
  // greyed-out-but-working). The API refuses non-admin Kick channels too.
  const kickOpen = !!(me && me.is_admin);
  if(activePlatform==='kick' && !kickOpen && KICK_BLOCKED.includes(view)) screen=<KickUnderConstruction/>;
  else if(view==='uploads' && !clipTabOn) screen=<UploadsUnderConstruction/>;
  else if(view==='review') screen=<ReviewScreen {...{streams:platformStreams,scores,clips:platformClips,onApprove:approveClip,onReject:rejectClip,onOpen:setModalClip,onEdit:onEditClip,lost:lostClips,me,onDismissLost:dismissMissNotice,refusals,onDismissRefusal:dismissRefusal,onGoTutorial:()=>setRoute('tutorial')}}/>;
  else if(view==='streams') screen=<StreamsScreen {...{streams:platformStreams,scores,profiles,histories,clips:platformClips,activePlatform,onAdd:addStream,onRemove:removeStream,onForce:forceClip}}/>;
  else if(view==='library') screen=<LibraryScreen {...{clips:platformClips,onOpen:setModalClip,onDelete:deleteClip,onEdit:onEditClip,onGoReview:()=>setRoute('review')}}/>;
  else if(view==='vod') screen=<VodScreen clips={platformClips} me={me}/>;
  else if(view==='tutorial') screen=<TutorialScreen doc={tutorial} onGo={setRoute}/>;
  else if(view==='schedule') screen=<ScheduleScreen me={me} queue={queue} platforms={platforms} connections={connections} uploadsOn={uploadsOn}
      autopilot={autopilot} onAutopilot={cfg=>setAutopilot(a=>({...(a||{}), config:cfg}))} captionsOn={captionsOn}/>;
  else if(view==='uploads') screen=<UploadScreen me={me} uploadsOn={uploadsOn} importOn={importOn} captionsOn={captionsOn} platforms={platforms}
      openUpload={editorTarget} onOpened={()=>setEditorTarget(null)}/>;
  else if(view==='training') screen=<TrainingScreen/>;
  else if(view==='landing') screen=<LandingScreen clips={clips} featured={featured} onToggle={toggleFeature} onMove={moveFeature} onGrab={grabFeature} onPlace={setPlacement} myUrls={myClipUrls}/>;
  else if(view==='account') screen=<AccountScreen me={me}/>;
  else if(view==='feedback') screen=<FeedbackScreen onSeen={loadFbUnread}/>;
  else screen=<SettingsScreen {...{streams}}/>;

  // FIRST RUN. Rendered INSTEAD of the shell, not inside it: a nav rail, a
  // platform switch and a live pill are answers to questions somebody with no
  // channels has not asked yet. The whole app appears the moment there is
  // something for it to hold.
  //
  // Gated on streams AND clips, not streams alone. Somebody who added a
  // channel, collected clips and later removed the channel is not a new user,
  // and dropping them onto a bare input would read as their account having
  // been wiped. `me.id` gates on /me having landed, so the screen cannot flash
  // before the app knows what the account has.
  if (me && me.id && Object.keys(streams).length === 0
      && Object.keys(clips).length === 0 && !seenBefore) {
    return <FirstRun onAdd={addStream}/>;
  }

  return (
    <div className={'rd-app'+(activePlatform==='kick'?' kick-theme':'')} id="rd-app" data-grad="violet" data-density="comfortable" data-glow="on">

      <div className={'rd-navscrim'+(navOpen?' open':'')} onClick={()=>setNavOpen(false)}/>
      <nav className={'rd-nav'+(navOpen?' open':'')}>
        <span className="logo"><img src="/static/logo-mark.png" alt="Highlightz"/></span>
        {NAV.filter(n=>(!n.labelerOnly||(me&&(me.is_labeler||me.is_admin)))&&(!n.adminOnly||(me&&me.is_admin))).map(n=>{
          // On Kick every platform-specific tab is closed off, so the button is
          // genuinely disabled — not just visually dimmed. `disabled` is what
          // actually stops the click; the class only makes that visible.
          const blocked = activePlatform==='kick' && !kickOpen && KICK_BLOCKED.includes(n.id);
          return (
          <button key={n.id} disabled={blocked} aria-disabled={blocked}
            title={blocked?'Not available on Kick yet':undefined}
            className={'rd-navitem'+(route===n.id?' active':'')+(blocked?' blocked':'')}
            onClick={()=>{ if(blocked) return; setRoute(n.id); setNavOpen(false); }}>
            {n.id==='review'&&pending>0&&!blocked&&<span className="navbadge">{pending}</span>}
            {n.id==='feedback'&&fbUnread>0&&<span className="navbadge">{fbUnread}</span>}
            <span className="ic"><Icon name={n.icon} size={22}/></span>
            <span>{n.label}</span>
          </button>
          );
        })}
        <span className="sp"/>
        <button className="rd-navitem" style={{background:'none',border:'none',cursor:'pointer',color:'inherit'}} title="Sign out" onClick={()=>fetch('/logout',{method:'POST'}).then(()=>{location.href='/login';})}>
          <span className="ic"><Icon name="logout" size={18}/></span>
          <span>Out</span>
        </button>
      </nav>
      <div className="rd-frame">
        <header className="rd-header">
          <button className="rd-menubtn" aria-label="Menu" onClick={()=>setNavOpen(o=>!o)}><Icon name="menu" size={19}/></button>
          <div><div className="htitle">{HEAD[route][0]}</div><div className="hsub">{HEAD[route][1]}</div></div>
          <div className="spacer"/>
          <div className="plat-switch">
            <div className={'plat-sw-pill '+(activePlatform==='kick'?'kick':'twitch')}/>
            <button className={'plat-sw-btn '+(activePlatform==='twitch'?'sw-on-twitch':'sw-off')} onClick={()=>switchPlatform('twitch')}>Twitch</button>
            <button className={'plat-sw-btn '+(activePlatform==='kick'?'sw-on-kick':'sw-off')} onClick={()=>switchPlatform('kick')}>Kick</button>
          </div>
          <span className="rd-live"><span className="dot"/>Live</span>
          <button className="rd-user-chip" title="Account" style={{border:'none',cursor:'pointer'}} onClick={()=>setRoute('account')}>
            {me.avatar_url
              ? <img src={me.avatar_url} alt={me.username}/>
              : <span className="uc-init">{(me.username||'?')[0].toUpperCase()}</span>}
            <span className="uc-name">{me.username||'Account'}</span>
          </button>
        </header>
        {me.subscription_status==='trialing' && <div style={{display:'flex',alignItems:'center',gap:8,padding:'8px 24px',background:'rgba(145,70,255,.1)',borderBottom:'1px solid rgba(145,70,255,.22)',fontSize:12,color:'var(--acc)',fontWeight:600}}>
          <span style={{width:7,height:7,borderRadius:'50%',background:'var(--live)',boxShadow:'0 0 8px var(--live)',flexShrink:0}}/>
          <span>Free trial — {me.trial_days_left||0} day{(me.trial_days_left||0)===1?'':'s'} left. <span style={{color:'var(--fg-2)',fontWeight:500}}>{me.trial_converts?'Your card is charged when it ends — cancel before then and you pay nothing.':'Subscribe to keep access when it ends — promo codes get 50% off your first month.'}</span></span>
          <a href={me.trial_converts?'/billing/portal':'/billing/checkout'} style={{marginLeft:'auto',color:'#fff',background:'#9146ff',textDecoration:'none',padding:'4px 12px',borderRadius:8,fontWeight:700,whiteSpace:'nowrap'}}>{me.trial_converts?'Manage':'Subscribe'}</a>
        </div>}
        <main className="rd-screen">{screen}</main>
      </div>
      {reviewAsk && <ReviewPrompt clips={reviewAsk.clips}
        onClose={()=>setReviewAsk(null)}/>}
      <UndoToast entry={undoable} onUndo={doUndo} onDismiss={()=>setUndoable(null)}/>
      <RdToast msg={toast}/>
      {announcements.length > 0 && <AnnouncementModal a={announcements[0]} onSeen={dismissAnnouncement}/>}
      <ClipModal clip={modalClip} onClose={()=>setModalClip(null)} onApprove={approveClip} onReject={rejectClip}
        onEdit={onEditClip}
        isAdmin={!!me.is_admin} featured={!!modalClip&&featuredIds.includes(modalClip.id)} onFeature={toggleFeature}/>
      {wake && <WakeSequence channel={wake.channel} platform={wake.platform}
        score={(scores[wake.channel]||{}).score||0}
        onDone={()=>{ setWake(null); flash('Monitoring '+wake.channel); }}/>}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<RdApp/>);
</script>
</body>
</html>"""
