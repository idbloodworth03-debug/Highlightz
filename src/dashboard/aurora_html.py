"""Aurora dashboard HTML — single-file React SPA served at GET /."""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Highlightz</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root {
  --rd-bg: #08080b; --rd-bg-2: #0d0d12;
  --panel: rgba(255,255,255,.035); --panel-2: rgba(255,255,255,.055); --panel-hi: rgba(255,255,255,.08);
  --hair: rgba(255,255,255,.08); --hair-2: rgba(255,255,255,.14);
  --fg: #f6f6f9; --fg-2: #9c9caa; --fg-3: #5d5d6b;
  --acc: #c79bff; --acc-2: #a855f7;
  --grad: linear-gradient(135deg,#f943ff 0%,#a855f7 52%,#7c6bff 100%);
  --grad-soft: linear-gradient(135deg,rgba(249,67,255,.18),rgba(124,107,255,.18));
  --glow: 0 0 0 1px rgba(199,155,255,.35),0 8px 30px -6px rgba(168,85,247,.45);
  --live: #2ee08a; --live-soft: rgba(46,224,138,.14);
  --pending: #ffc25c; --pending-soft: rgba(255,194,92,.14);
  --danger: #ff5a78; --danger-soft: rgba(255,90,120,.14);
  --r-sm:10px; --r-md:14px; --r-lg:18px; --r-xl:24px; --r-pill:999px;
  --font:'Inter',system-ui,-apple-system,sans-serif;
  --shadow-1:0 1px 2px rgba(0,0,0,.4); --shadow-2:0 10px 30px -10px rgba(0,0,0,.6);
  --shadow-card:0 18px 40px -16px rgba(0,0,0,.65);
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{font-family:var(--font);color:var(--fg);background:var(--rd-bg);-webkit-font-smoothing:antialiased;overflow:hidden}
button{font-family:inherit;cursor:pointer}
::selection{background:rgba(199,155,255,.3)}
.rd-app{position:relative;height:100vh;display:grid;grid-template-columns:104px 1fr;isolation:isolate}
.rd-frame{display:grid;grid-template-rows:68px 1fr;min-height:0;overflow:hidden}
.rd-screen{min-height:0;overflow:hidden;display:flex;flex-direction:column}
.rd-app::before{content:'';position:fixed;inset:0;z-index:-2;
  background:radial-gradient(900px 480px at 18% -8%,rgba(168,85,247,.20),transparent 60%),
    radial-gradient(760px 420px at 92% 6%,rgba(249,67,255,.13),transparent 55%),
    radial-gradient(700px 600px at 60% 110%,rgba(124,107,255,.12),transparent 60%),var(--rd-bg)}
.rd-app::after{content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:radial-gradient(120% 120% at 50% 0%,transparent 60%,rgba(0,0,0,.55))}
.glass{background:var(--panel);border:1px solid var(--hair);-webkit-backdrop-filter:blur(22px) saturate(140%);backdrop-filter:blur(22px) saturate(140%)}
.rd-header{display:flex;align-items:center;gap:18px;padding:0 22px;border-bottom:1px solid var(--hair);
  background:rgba(10,10,14,.55);-webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px);z-index:5}
.rd-live{display:inline-flex;align-items:center;gap:7px;font-size:12px;font-weight:600;color:var(--live);
  background:var(--live-soft);padding:6px 12px;border-radius:var(--r-pill);border:1px solid rgba(46,224,138,.25)}
.rd-live .dot{width:7px;height:7px;border-radius:50%;background:var(--live);animation:ping 2s infinite}
@keyframes ping{0%{box-shadow:0 0 0 0 rgba(46,224,138,.5)}70%{box-shadow:0 0 0 7px rgba(46,224,138,0)}100%{box-shadow:0 0 0 0 rgba(46,224,138,0)}}
@keyframes spin{to{transform:rotate(360deg)}}
.rd-search{flex:1;max-width:420px;position:relative}
.rd-search input{width:100%;background:rgba(255,255,255,.04);border:1px solid var(--hair);border-radius:var(--r-pill);
  color:var(--fg);font-size:13px;padding:10px 14px 10px 38px;outline:none;transition:.18s}
.rd-search input::placeholder{color:var(--fg-3)}
.rd-search input:focus{border-color:rgba(199,155,255,.5);background:rgba(255,255,255,.06);box-shadow:0 0 0 4px rgba(168,85,247,.12)}
.rd-search .si{position:absolute;left:13px;top:50%;transform:translateY(-50%);color:var(--fg-3)}
.rd-header .spacer{flex:1}
.rd-iconbtn{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;
  background:rgba(255,255,255,.04);border:1px solid var(--hair);color:var(--fg-2);transition:.18s}
.rd-iconbtn:hover{color:var(--fg);background:rgba(255,255,255,.08)}
.rd-avatar{width:38px;height:38px;border-radius:50%;background:var(--grad);display:grid;place-items:center;
  font-weight:700;font-size:14px;color:#14021c;border:none;box-shadow:var(--glow)}
.rd-user-chip{display:flex;align-items:center;gap:10px;padding:4px 12px 4px 4px;border-radius:999px;
  background:rgba(255,255,255,.05);border:1px solid var(--hair)}
.rd-user-chip img{width:32px;height:32px;border-radius:50%;object-fit:cover}
.rd-user-chip .uc-init{width:32px;height:32px;border-radius:50%;background:var(--grad);display:grid;
  place-items:center;font-weight:700;font-size:13px;color:#14021c}
.rd-user-chip .uc-name{font-size:13px;font-weight:600;color:var(--fg-2)}
.rd-body{display:grid;grid-template-columns:322px 1fr;gap:18px;padding:18px 22px;overflow:hidden;min-height:0}
.rd-col{min-height:0;display:flex;flex-direction:column;gap:16px}
/* Clip Review has no side rail any more — adding streams moved to Live
   Streams — so the grid takes the full width instead of leaving a gap. */
.rd-body-full{grid-template-columns:1fr}
.rd-streampick{cursor:pointer;border-radius:15px;transition:.15s}
.rd-streampick.on{box-shadow:0 0 0 1px var(--acc-2)}
.rd-rail{border-radius:var(--r-lg);padding:16px;display:flex;flex-direction:column;gap:16px;overflow:hidden}
.rd-rail-head{display:flex;align-items:center;justify-content:space-between}
.rd-eyebrow{font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--fg-3)}
.rd-count{font-size:11px;font-weight:600;color:var(--fg-2);background:rgba(255,255,255,.05);padding:3px 9px;border-radius:var(--r-pill)}
.rd-addrow{display:flex;gap:8px}
.rd-suggwrap{position:relative;flex:1;min-width:0;display:flex}
.rd-suggwrap .rd-input{width:100%}
.rd-sugg{position:absolute;top:calc(100% + 6px);left:0;z-index:60;background:#101016;
  border:1px solid var(--hair-2);border-radius:12px;box-shadow:0 14px 36px rgba(0,0,0,.55);
  max-height:320px;overflow-y:auto;overflow-x:hidden;padding:6px;
  /* Wider than the input on purpose: names + LIVE + viewers/game must fit on
     one line with no horizontal scrolling. Caps to the viewport on phones. */
  width:340px;max-width:calc(100vw - 44px)}
.rd-sugglabel{font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--fg-3);padding:7px 9px 3px}
.rd-suggitem{display:flex;align-items:center;gap:8px;padding:7px 9px;border-radius:8px;cursor:pointer;font-size:13px;color:var(--fg)}
.rd-suggitem:hover{background:rgba(255,255,255,.06)}
.rd-suggitem .meta2{color:var(--fg-3);font-size:11px;margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rd-sugglive{font-size:9.5px;font-weight:800;letter-spacing:.05em;color:#fff;background:#e91916;border-radius:4px;padding:1px 5px;flex-shrink:0}
.rd-suggempty{padding:12px 9px;font-size:12.5px;color:var(--fg-3)}
/* The label row carries the "Clear all" action, so it stops being padding-only
   and becomes a flex row. Same padding as before so nothing shifts. */
.rd-sugglabelrow{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 9px 3px}
.rd-sugglabelrow .rd-sugglabel{padding:0}
.rd-suggclear{background:none;border:0;cursor:pointer;font:inherit;font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--fg-3);padding:2px 4px;border-radius:5px}
.rd-suggclear:hover{color:var(--fg);background:rgba(255,255,255,.07)}
/* Per-row dismiss. Hidden until the row is hovered so eight of these do not
   read as a column of buttons, but kept focusable for keyboard users. */
.rd-suggx{margin-left:auto;flex-shrink:0;background:none;border:0;cursor:pointer;color:var(--fg-3);opacity:0;padding:2px;border-radius:5px;display:flex;align-items:center}
.rd-suggitem:hover .rd-suggx{opacity:1}
.rd-suggx:focus{opacity:1}
.rd-suggx:hover{color:var(--fg);background:rgba(255,255,255,.1)}
.rd-input{flex:1;min-width:0;background:rgba(255,255,255,.04);border:1px solid var(--hair);border-radius:var(--r-md);
  color:var(--fg);font-size:13px;padding:11px 13px;outline:none;transition:.18s}
.rd-input::placeholder{color:var(--fg-3)}
.rd-input:focus{border-color:rgba(199,155,255,.5);box-shadow:0 0 0 4px rgba(168,85,247,.1)}
.rd-select{background:rgba(255,255,255,.04);border:1px solid var(--hair);border-radius:var(--r-md);
  color:var(--fg);font-size:13px;padding:0 10px;outline:none;cursor:pointer}
.rd-select option{background:#15151c}
.rd-btn{border:none;border-radius:var(--r-md);padding:11px 16px;font-size:13px;font-weight:600;
  display:inline-flex;align-items:center;justify-content:center;gap:7px;color:#fff;
  background:rgba(255,255,255,.06);border:1px solid var(--hair);transition:.18s;white-space:nowrap}
.rd-btn:hover{background:rgba(255,255,255,.1)}
.kick-theme{--acc:#53fc18;--acc-2:#39b515;--grad:linear-gradient(135deg,#53fc18 0%,#39b515 100%);--grad-soft:linear-gradient(135deg,rgba(83,252,24,.14),rgba(57,181,21,.10));--glow:0 0 0 1px rgba(83,252,24,.3),0 8px 30px -6px rgba(57,181,21,.4)}
.kick-theme .rd-btn.grad{box-shadow:0 6px 18px -6px rgba(83,252,24,.5)}
.kick-theme .rd-filter.active{box-shadow:0 4px 14px -4px rgba(83,252,24,.5)}
.kick-theme .rd-navitem.active::before{background:rgba(83,252,24,.1)}
.kick-theme .rd-navitem.active .ic{color:#53fc18}
.rd-btn.grad{background:var(--grad);border:none;color:#fff;box-shadow:0 6px 18px -6px rgba(168,85,247,.6)}
.rd-btn.grad:hover{filter:brightness(1.08);box-shadow:0 8px 24px -6px rgba(168,85,247,.75)}
.rd-btn.live{background:var(--live);color:#052012;border:none}
.rd-btn.live:hover{filter:brightness(1.08)}
.rd-btn.danger{background:var(--danger-soft);color:var(--danger);border:1px solid rgba(255,90,120,.3)}
.rd-btn.danger:hover{background:rgba(255,90,120,.22)}
.rd-btn.ghost-force{background:rgba(255,138,76,.14);color:#ff9a52;border:1px solid rgba(255,138,76,.3)}
.rd-btn.ghost-force:hover{background:rgba(255,138,76,.24)}
.rd-btn.sm{padding:7px 11px;font-size:12px;border-radius:10px}
.rd-streams{display:flex;flex-direction:column;gap:10px;overflow-y:auto;padding-right:2px;min-height:0}
.rd-stream{border-radius:var(--r-md);padding:13px;background:rgba(255,255,255,.025);border:1px solid var(--hair);transition:.18s}
.rd-stream:hover{border-color:var(--hair-2);background:rgba(255,255,255,.045)}
.rd-stream-top{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
.rd-stream-top>div:first-child{min-width:0;flex:1;overflow:hidden}
.rd-stream .nm{font-size:14px;font-weight:700;letter-spacing:-.01em;display:flex;align-items:center;gap:7px;overflow:hidden}
.rd-stream .nm .plat{width:6px;height:6px;border-radius:50%;flex-shrink:0;background:var(--acc);box-shadow:0 0 8px var(--acc)}
.rd-stream .mt{font-size:11px;color:var(--fg-2);margin-top:3px;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.rd-chip{font-size:10px;font-weight:600;padding:2px 7px;border-radius:var(--r-pill);background:rgba(255,255,255,.06);color:var(--fg-2);text-transform:capitalize}
.rd-stream-actions{display:flex;gap:6px;align-items:center;flex-shrink:0}
.rd-x{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;background:transparent;border:none;color:var(--fg-3);transition:.15s}
.rd-x:hover{color:var(--danger);background:var(--danger-soft)}
.rd-score{margin-top:12px}
.rd-score-top{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:7px}
.rd-score-top .lbl{font-size:11px;color:var(--fg-2);font-weight:500}
.rd-score-top .val{font-size:20px;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums;line-height:1}
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
  background:linear-gradient(90deg,transparent,rgba(199,155,255,.5),transparent);
  animation:rdScan 1.7s ease-in-out infinite;pointer-events:none}
@keyframes rdScan{0%{left:-36%}100%{left:100%}}
.rd-livedot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--acc);
  margin-right:7px;vertical-align:middle;animation:rdBreathe 1.4s ease-in-out infinite}
@keyframes rdBreathe{0%,100%{opacity:.35;transform:scale(.82)}50%{opacity:1;transform:scale(1)}}
@media(prefers-reduced-motion:reduce){
  /* Still legible without motion: the elapsed counter alone proves liveness. */
  .rd-track.working::after{animation:none;opacity:.25}
  .rd-livedot{animation:none;opacity:.9}
}
.rd-sigs{display:flex;gap:5px;margin-top:8px;flex-wrap:wrap}
.rd-sig{font-size:10px;padding:2px 7px;border-radius:6px;background:rgba(255,255,255,.05);color:var(--fg-2);font-variant-numeric:tabular-nums}
/* Training studio sliders */
.tr-dim-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:5px}
.tr-dim-label{font-size:13.5px;font-weight:700}
.tr-dim-hint{font-size:11.5px;font-weight:500;color:var(--fg-3);margin-left:9px}
.tr-dim-val{font-size:18px;font-weight:800;color:var(--acc);font-variant-numeric:tabular-nums;min-width:26px;text-align:right}
.tr-slider{width:100%;height:6px;-webkit-appearance:none;appearance:none;background:rgba(255,255,255,.09);border-radius:99px;outline:none;cursor:pointer}
.tr-slider::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:18px;height:18px;border-radius:50%;background:linear-gradient(135deg,#f943ff,#a855f7);box-shadow:0 0 10px rgba(168,85,247,.6);cursor:pointer}
.tr-slider::-moz-range-thumb{width:18px;height:18px;border:none;border-radius:50%;background:linear-gradient(135deg,#f943ff,#a855f7);box-shadow:0 0 10px rgba(168,85,247,.6);cursor:pointer}
.rd-profile{margin-top:12px;padding:11px;border-radius:var(--r-md);background:rgba(0,0,0,.25);border:1px solid var(--hair)}
.rd-pgrid{display:grid;grid-template-columns:1fr 1fr;gap:9px 12px}
.rd-pcell .k{font-size:10px;color:var(--fg-3);text-transform:uppercase;letter-spacing:.06em;font-weight:600}
.rd-pcell .v{font-size:14px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:2px}
.rd-learn{margin-top:10px;font-size:10px;font-weight:600;display:flex;align-items:center;gap:6px}
.rd-learnbar{flex:1;height:4px;border-radius:var(--r-pill);background:rgba(255,255,255,.08);overflow:hidden}
.rd-learnbar>div{height:100%;background:var(--grad);border-radius:var(--r-pill);transition:width .4s}
.rd-empty{text-align:center;color:var(--fg-3);font-size:13px;padding:32px 12px;line-height:1.6}
.rd-empty .ic{color:var(--fg-3);display:flex;justify-content:center;margin-bottom:10px}
.rd-main{min-height:0;display:flex;flex-direction:column;gap:16px;overflow:hidden}
.rd-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
.rd-stat{border-radius:var(--r-lg);padding:16px 18px;position:relative;overflow:hidden}
.rd-stat .k{font-size:11px;color:var(--fg-2);font-weight:600;letter-spacing:.02em;display:flex;align-items:center;gap:7px}
.rd-stat .k .si{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;background:var(--grad-soft);color:var(--acc)}
.rd-stat .v{font-size:30px;font-weight:800;letter-spacing:-.035em;margin-top:10px;font-variant-numeric:tabular-nums;line-height:1}
.rd-stat .sub{font-size:11px;color:var(--fg-3);margin-top:6px}
.rd-stat.accent{background:var(--grad-soft);border-color:rgba(199,155,255,.22)}
.rd-toolbar{display:flex;align-items:center;gap:12px}
.rd-toolbar h2{font-size:17px;font-weight:700;letter-spacing:-.02em}
.cull-panel{position:absolute;top:calc(100% + 8px);right:0;z-index:40;width:260px;padding:16px;border-radius:12px;display:flex;flex-direction:column;gap:10px}
.cull-row{display:flex;justify-content:space-between;align-items:baseline}
.cull-lbl{font-size:12px;color:var(--fg-2);font-weight:600}
.cull-val{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums}
.cull-slider{width:100%;accent-color:var(--acc);cursor:pointer}
.cull-preview{display:flex;justify-content:space-between;font-size:12px;font-weight:700}
.plat-switch{position:relative;display:flex;gap:0;background:rgba(255,255,255,.06);border:1px solid var(--hair);border-radius:99px;padding:3px;user-select:none}
.plat-sw-pill{position:absolute;top:3px;bottom:3px;left:3px;width:calc(50% - 3px);border-radius:99px;pointer-events:none;transition:transform .35s cubic-bezier(.34,1.4,.64,1),background .3s ease,box-shadow .3s ease}
.plat-sw-pill.kick{transform:translateX(100%);background:#53fc18;box-shadow:0 2px 14px -3px rgba(83,252,24,.7)}
.plat-sw-pill.twitch{transform:translateX(0);background:#9146ff;box-shadow:0 2px 14px -3px rgba(145,70,255,.7)}
.plat-sw-btn{position:relative;z-index:1;flex:1;border:none;border-radius:99px;padding:8px 18px;font-size:12px;font-weight:700;cursor:pointer;background:transparent;transition:color .25s ease,transform .12s ease;-webkit-tap-highlight-color:transparent}
.plat-sw-btn:active{transform:scale(.93)}
.plat-sw-btn.sw-on-twitch{color:#fff}.plat-sw-btn.sw-on-kick{color:#0a0a0e}.plat-sw-btn.sw-off{color:var(--fg-2)}
.rd-filters{display:flex;gap:6px;background:rgba(255,255,255,.04);padding:4px;border-radius:var(--r-pill);border:1px solid var(--hair)}
.rd-filter{border:none;background:transparent;color:var(--fg-2);font-size:12px;font-weight:600;padding:7px 15px;border-radius:var(--r-pill);transition:.18s}
.rd-filter:hover{color:var(--fg)}
.rd-filter.active{color:#fff;background:var(--grad);box-shadow:0 4px 14px -4px rgba(168,85,247,.6)}
.rd-grid{flex:1;overflow-y:auto;padding-right:4px;display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:18px;align-content:start;align-items:stretch;min-height:0}
/* min-height, not height. The thumbnail is 16:9 of the COLUMN width, so a card in
   a wide column is taller than one in a narrow column — a fixed 360px fits at the
   310px grid minimum and clips the action row (the "Open on Twitch" button) once
   columns get wider. The grid still stretches every card in a row to the tallest,
   so rows stay level. */
.rd-clip{border-radius:var(--r-lg);overflow:hidden;background:var(--panel);border:1px solid var(--hair);transition:transform .22s cubic-bezier(.4,0,.2,1),border-color .22s,box-shadow .22s;display:flex;flex-direction:column;min-height:360px}
.rd-clip:hover{transform:translateY(-4px);border-color:rgba(199,155,255,.35);box-shadow:var(--shadow-card)}
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
.rd-play .ring{width:56px;height:56px;border-radius:50%;display:grid;place-items:center;padding-left:3px;
  background:rgba(20,12,30,.4);border:1.5px solid rgba(255,255,255,.85);color:#fff;backdrop-filter:blur(4px);transition:transform .2s,background .2s}
.rd-clip:hover .rd-play .ring{transform:scale(1.08);background:var(--grad);border-color:transparent;box-shadow:var(--glow)}
/* Both badges overlay the clip player, so a backdrop-filter on them means the
   browser re-blurs that patch of video on every decoded frame. They also sit
   one-per-card in the review grid, each its own blur layer, which is what made
   scrolling a full queue heavy. An opaque background gives the same contrast
   over a bright thumbnail for none of the per-frame cost. */
.rd-scorebadge{position:absolute;top:10px;right:10px;z-index:2;display:inline-flex;align-items:center;gap:5px;
  font-size:12px;font-weight:700;padding:5px 10px;border-radius:var(--r-pill);color:#fff;
  background:rgba(10,8,14,.82);border:1px solid rgba(255,255,255,.16);font-variant-numeric:tabular-nums}
.rd-scorebadge .pip{width:6px;height:6px;border-radius:50%}
.rd-viralbadge{position:absolute;top:10px;left:10px;z-index:2;display:inline-flex;align-items:center;gap:5px;
  font-size:11.5px;font-weight:800;padding:5px 10px;border-radius:var(--r-pill);color:#fff;
  background:rgba(10,8,14,.82);border:1px solid rgba(255,255,255,.16);font-variant-numeric:tabular-nums}
.rd-viralbadge.hot{background:linear-gradient(135deg,#ff7700,#f943ff);border-color:transparent;box-shadow:0 3px 14px -3px rgba(255,119,0,.65)}
.rd-viralbadge.warm{color:#ffcc5c;border-color:rgba(255,204,92,.35)}
/* Crowd-validated: a real viewer already clipped this moment. Sits under
   the virality badge so both are readable. */
.rd-clippedbadge{position:absolute;top:38px;left:10px;z-index:2;display:inline-flex;align-items:center;gap:5px;
  font-size:10.5px;font-weight:800;letter-spacing:.02em;padding:3px 8px;border-radius:99px;color:#0b0b12;
  background:linear-gradient(135deg,#3ee08a,#2ee0c8);box-shadow:0 3px 12px -3px rgba(62,224,138,.6)}
.rd-dur{position:absolute;left:10px;bottom:10px;z-index:2;font-size:11px;font-weight:600;color:#fff;
  background:rgba(10,8,14,.6);padding:3px 8px;border-radius:7px;font-variant-numeric:tabular-nums}
.rd-clip-body{padding:14px;flex:1;display:flex;flex-direction:column}
.rd-clip-head{display:flex;align-items:center;justify-content:space-between;gap:8px}
.rd-clip-ch{font-size:14px;font-weight:700;letter-spacing:-.01em;display:flex;align-items:center;gap:7px}
.rd-clip-ch .av{width:22px;height:22px;border-radius:7px;background:var(--grad);display:grid;place-items:center;font-size:11px;font-weight:800;color:#1a0322}
.rd-status{font-size:11px;font-weight:600;padding:4px 10px;border-radius:var(--r-pill);display:inline-flex;align-items:center;gap:5px;text-transform:capitalize}
.rd-status.pending{background:var(--pending-soft);color:var(--pending)}
.rd-status.approved{background:var(--live-soft);color:var(--live)}
.rd-status.rejected{background:var(--danger-soft);color:var(--danger)}
.rd-clip-title{font-size:13px;color:var(--fg);margin-top:9px;font-weight:500;line-height:1.35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rd-clip-meta{display:flex;gap:7px;margin-top:10px;flex-wrap:wrap;overflow:hidden;max-height:48px}
.rd-tag{font-size:11px;color:var(--fg-2);background:rgba(255,255,255,.05);padding:3px 9px;border-radius:var(--r-pill)}
.rd-clip-actions{display:flex;gap:9px;margin-top:auto;padding-top:14px;flex-wrap:wrap}
.rd-clip-actions .rd-btn{flex:1}
.rd-resolved{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--fg-2);padding:4px 0;flex-wrap:wrap}
.rd-grid-empty{grid-column:1/-1;text-align:center;padding:70px 0;color:var(--fg-3)}
.rd-grid-empty .ic{display:flex;justify-content:center;margin-bottom:16px;color:var(--fg-3)}
.rd-grid-empty .big{font-size:18px;font-weight:700;color:var(--fg);margin-bottom:8px;letter-spacing:-.01em}
.rd-emptylink{display:inline-block;margin-top:16px;font-size:13px;font-weight:600;color:var(--acc);
  padding:8px 15px;border-radius:9px;border:1px solid var(--hair-2);transition:.16s}
.rd-emptylink:hover{background:rgba(255,255,255,.05);border-color:var(--acc);color:var(--fg)}
.rd-toast{position:fixed;bottom:26px;left:50%;transform:translate(-50%,90px);opacity:0;
  display:inline-flex;align-items:center;gap:10px;padding:13px 20px;border-radius:var(--r-pill);
  background:rgba(18,14,24,.85);border:1px solid rgba(199,155,255,.35);color:var(--fg);font-size:13px;font-weight:500;
  -webkit-backdrop-filter:blur(20px);backdrop-filter:blur(20px);box-shadow:0 16px 40px -12px rgba(0,0,0,.7);z-index:50;transition:all .35s cubic-bezier(.34,1.56,.64,1)}
.rd-toast.show{transform:translate(-50%,0);opacity:1}
.rd-undo{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:80;
  display:flex;align-items:center;gap:11px;padding:11px 14px;border-radius:12px;
  background:rgba(18,18,24,.96);border:1px solid var(--hair-2);
  box-shadow:0 10px 34px rgba(0,0,0,.5);font-size:13px;color:var(--fg);
  -webkit-backdrop-filter:blur(18px);backdrop-filter:blur(18px)}
.rd-undo .ico{display:flex;color:var(--fg-3)}
.rd-undo .msg{font-weight:600}
.rd-undo .act{background:rgba(168,85,247,.18);border:1px solid var(--acc);color:var(--acc);
  font-weight:700;font-size:12px;padding:5px 12px;border-radius:8px;cursor:pointer}
.rd-undo .act:hover{background:rgba(168,85,247,.3);color:var(--fg)}
.rd-undo .left{font-size:11px;color:var(--fg-3);font-variant-numeric:tabular-nums;min-width:26px}
.rd-undo .x{background:none;border:none;color:var(--fg-3);cursor:pointer;font-size:15px;line-height:1;padding:0 2px}
.rd-undo .x:hover{color:var(--fg)}
@media(max-width:600px){.rd-undo{left:12px;right:12px;transform:none;justify-content:center}}
.rd-toast .ico{width:24px;height:24px;border-radius:50%;background:var(--grad);display:grid;place-items:center;color:#fff}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:99px;border:2px solid transparent;background-clip:padding-box}
::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.18);background-clip:padding-box}
.rd-nav{display:flex;flex-direction:column;align-items:center;gap:6px;padding:16px 0;
  border-right:1px solid var(--hair);background:rgba(10,10,14,.5);
  -webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px);z-index:6}
.rd-nav .logo{margin-bottom:18px;display:flex}
/* The mark is a transparent PNG cropped to its own ink, so `height` is now the
   height of the GLYPH — under the old plated JPEG the same 44px was mostly
   empty background with a ~19px mark floating in it. Sizes here and everywhere
   else were re-picked against the visible mark, not carried over. */
.rd-nav .logo img{height:34px;filter:drop-shadow(0 0 12px rgba(199,155,255,.45))}
.rd-navitem{width:88px;height:64px;border-radius:16px;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:5px;background:transparent;border:none;
  color:var(--fg-3);font-size:11.5px;font-weight:600;letter-spacing:.01em;transition:.16s;position:relative}
.rd-navitem:hover{color:var(--fg-2);background:rgba(255,255,255,.05)}
/* Closed off on Kick. The button is really `disabled`; this only makes that
   legible — and the not-allowed cursor plus killed hover stops it reading as
   an unresponsive app. */
.rd-navitem.blocked{opacity:.32;cursor:not-allowed;filter:saturate(.4)}
.rd-navitem.blocked:hover{color:var(--fg-3);background:transparent}
.rd-navitem.active{color:#fff}
.rd-navitem.active::before{content:'';position:absolute;inset:0;border-radius:16px;
  background:var(--grad-soft);border:1px solid rgba(199,155,255,.3)}
.rd-navitem.active .ic{color:var(--acc)}
.rd-navitem .ic,.rd-navitem span{position:relative;z-index:1}
.rd-nav .sp{flex:1}
/* Drawer affordances exist only at the mobile breakpoint (see @media below). */
.rd-menubtn,.rd-navscrim{display:none}
.rd-nav .navbadge{position:absolute;top:7px;right:9px;min-width:16px;height:16px;padding:0 4px;
  border-radius:99px;background:var(--grad);color:#fff;font-size:9px;font-weight:800;display:grid;place-items:center;z-index:2}
.rd-header .htitle{font-size:18px;font-weight:700;letter-spacing:-.02em}
.rd-header .hsub{font-size:12px;color:var(--fg-3);margin-top:1px}
.rd-scroll{flex:1;overflow-y:auto;min-height:0;padding:20px 22px}
.rd-section-title{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.rd-section-title h2{font-size:19px;font-weight:800;letter-spacing:-.025em}
.rd-section-title .cnt{font-size:12px;color:var(--fg-3)}
/* 322px matches the old Clip Review rail: the add-stream box moved here and
   the search input needs the same room, otherwise the placeholder truncates
   next to the preset dropdown. */
.rd-streams-layout{display:grid;grid-template-columns:322px 1fr;gap:18px;flex:1;min-height:0;padding:20px 22px}
/* Search above preset — side by side leaves the input too narrow to read. */
.rd-addrow{flex-direction:column}
.rd-chanlist{display:flex;flex-direction:column;gap:9px;overflow-y:auto;min-height:0;padding-right:2px}
.rd-chanlist .rd-eyebrow{padding:4px 2px 2px}
.rd-chan{text-align:left;padding:12px;border-radius:15px;background:rgba(255,255,255,.025);
  border:1px solid var(--hair);transition:.16s;display:flex;align-items:center;gap:11px;width:100%}
.rd-chan:hover{background:rgba(255,255,255,.05)}
.rd-chan.active{background:var(--grad-soft);border-color:rgba(199,155,255,.32)}
.rd-chan .av{width:38px;height:38px;border-radius:12px;background:var(--grad);display:grid;place-items:center;
  font-weight:800;color:#1a0322;font-size:13px;flex-shrink:0}
.rd-chan .nm{font-weight:700;font-size:14px;letter-spacing:-.01em}
.rd-chan .mt{font-size:11px;color:var(--fg-2);margin-top:2px}
.rd-chan .mini{margin-left:auto;font-size:16px;font-weight:800;font-variant-numeric:tabular-nums}
.rd-detail{display:flex;flex-direction:column;gap:16px;overflow-y:auto;min-height:0;padding-right:4px}
.rd-detail-head{display:flex;align-items:center;gap:15px}
.rd-detail-head .av{width:54px;height:54px;border-radius:16px;background:var(--grad);display:grid;place-items:center;
  font-weight:800;color:#1a0322;font-size:19px;box-shadow:var(--glow)}
.rd-detail-head h2{font-size:23px;font-weight:800;letter-spacing:-.025em}
.rd-detail-head .mt{font-size:12px;color:var(--fg-2);margin-top:3px;display:flex;gap:7px;align-items:center}
.rd-detail-head .sp{flex:1}
.rd-card2{border-radius:18px;padding:18px}
.rd-chart-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px}
.rd-chart-head .lbl{font-size:13px;font-weight:600;color:var(--fg-2)}
.rd-chart-head .big{font-size:30px;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.rd-chart{width:100%;height:150px;display:block}
.rd-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.rd-metric{border-radius:15px;padding:15px}
.rd-metric .k{font-size:11px;color:var(--fg-2);font-weight:500}
.rd-metric .v{font-size:24px;font-weight:800;letter-spacing:-.03em;margin-top:7px;font-variant-numeric:tabular-nums}
.rd-weight{display:flex;align-items:center;gap:12px;margin-bottom:13px}
.rd-weight:last-child{margin-bottom:0}
.rd-weight .wl{width:130px;font-size:12px;color:var(--fg-2)}
.rd-weight .wt{flex:1;height:8px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
.rd-weight .wf{height:100%;border-radius:99px;background:var(--grad);transition:width .4s}
.rd-weight .wv{width:46px;text-align:right;font-size:12px;font-weight:700;font-variant-numeric:tabular-nums}
.rd-settings{max-width:900px;margin:0 auto;display:flex;flex-direction:column;gap:16px;width:100%}
.rd-card{border-radius:18px;padding:22px}
.rd-card h3{font-size:15px;font-weight:700;display:flex;align-items:center;gap:10px;letter-spacing:-.01em}
.rd-card h3 .si{width:30px;height:30px;border-radius:9px;background:var(--grad-soft);color:var(--acc);display:grid;place-items:center}
.rd-card .desc{font-size:12px;color:var(--fg-3);margin:6px 0 18px 40px}
/* ── Clip Editor ── */
.rd-drop{border:2px dashed var(--hair);border-radius:16px;padding:34px 20px;text-align:center;
  cursor:pointer;transition:border-color .18s,background .18s;background:rgba(255,255,255,.015)}
.rd-drop:hover{border-color:var(--acc-2);background:rgba(168,85,247,.05)}
.rd-drop.over{border-color:var(--acc);background:rgba(168,85,247,.11)}
.rd-drop .di{color:var(--acc);margin-bottom:10px}
.rd-drop .dt{font-size:14px;font-weight:700;margin-bottom:5px}
.rd-drop .ds{font-size:12px;color:var(--fg-3)}
.rd-quota{height:7px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden;margin:9px 0 6px}
.rd-quota i{display:block;height:100%;border-radius:99px;background:var(--grad);transition:width .3s}
.rd-quota-full i{background:linear-gradient(135deg,#ff5a78,#ff8a4c)}
.rd-up{border-radius:14px;border:1px solid var(--hair);background:rgba(255,255,255,.02);overflow:hidden;
  display:flex;flex-direction:column}
.rd-up video{width:100%;aspect-ratio:16/9;background:#000;display:block;object-fit:contain}
.rd-up .ub{padding:11px 13px;display:flex;align-items:center;gap:10px}
.rd-up .un{font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.rd-up .um{font-size:11px;color:var(--fg-3);margin-top:2px}
/* Twitch clip import cards */
.rd-tw{border-radius:14px;border:1px solid var(--hair);background:rgba(255,255,255,.02);
  overflow:hidden;display:flex;flex-direction:column}
.rd-tw iframe{width:100%;aspect-ratio:16/9;border:none;display:block;background:#000}
.rd-tw .tw-thumb{position:relative;display:block;width:100%;aspect-ratio:16/9;padding:0;border:none;
  background:#0b0b12;cursor:pointer;overflow:hidden}
.rd-tw .tw-thumb img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .25s}
.rd-tw .tw-thumb:hover img{transform:scale(1.04)}
.rd-tw .tw-noimg{width:100%;height:100%;display:grid;place-items:center;color:var(--fg-3)}
.rd-tw .tw-play{position:absolute;inset:0;margin:auto;width:40px;height:40px;border-radius:50%;
  display:grid;place-items:center;background:rgba(0,0,0,.55);color:#fff;
  -webkit-backdrop-filter:blur(4px);backdrop-filter:blur(4px);transition:.18s}
.rd-tw .tw-thumb:hover .tw-play{background:var(--acc-2);transform:scale(1.08)}
.rd-tw .tw-dur{position:absolute;right:7px;bottom:7px;font-size:10.5px;font-weight:700;color:#fff;
  background:rgba(0,0,0,.7);padding:2px 6px;border-radius:6px}
.rd-tw .tw-meta{padding:10px 12px;display:flex;flex-direction:column;gap:3px;min-width:0}
.rd-tw .tw-title{font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rd-tw .tw-sub{font-size:11px;color:var(--fg-3)}
.rd-tw .tw-link{font-size:11px;color:var(--acc);font-weight:600;margin-top:2px}
.rd-tw .tw-link:hover{text-decoration:underline}
/* ── Editor ── */
.tw-box{width:min(900px,100%);border-radius:18px;padding:18px}
.tw-frame{position:relative;width:100%;aspect-ratio:16/9;border-radius:12px;overflow:hidden;background:#000}
.tw-frame iframe{position:absolute;inset:0;width:100%;height:100%;border:none}
.ed-bg{position:fixed;inset:0;z-index:200;background:rgba(4,4,8,.86);display:flex;
  align-items:center;justify-content:center;padding:20px;-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px)}
.ed{width:min(1080px,100%);max-height:94vh;overflow-y:auto;border-radius:20px;padding:20px;
  background:var(--panel);border:1px solid var(--hair)}
.ed-head{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.ed-head h3{font-size:16px;font-weight:700;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ed-body{display:grid;grid-template-columns:1fr 260px;gap:18px}
@media(max-width:820px){.ed-body{grid-template-columns:1fr}}
.ed-stage{background:#000;border-radius:14px;overflow:hidden;display:grid;place-items:center;min-height:300px}
.ed-stage canvas{max-width:100%;max-height:56vh;display:block}
.ed-side{display:flex;flex-direction:column;gap:16px}
.ed-grp{display:flex;flex-direction:column;gap:7px}
.ed-grp label{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--fg-3)}
.ed-row{display:flex;align-items:center;gap:8px}
.ed-row input[type=range]{flex:1;accent-color:var(--acc);cursor:pointer}
.ed-num{font-size:11px;color:var(--fg-3);font-variant-numeric:tabular-nums;min-width:44px;text-align:right}
.ed-in{width:100%;background:rgba(255,255,255,.05);border:1px solid var(--hair);border-radius:10px;
  padding:8px 10px;color:var(--fg);font-size:13px;font-family:inherit}
.ed-in:focus{outline:none;border-color:var(--acc-2)}
.ed-seg{display:flex;gap:6px;flex-wrap:wrap}
.ed-seg button{flex:1;min-width:64px;padding:7px 9px;border-radius:9px;font-size:11.5px;font-weight:700;
  background:rgba(255,255,255,.05);border:1px solid var(--hair);color:var(--fg-3);cursor:pointer;transition:.15s}
.ed-seg button.on{background:var(--grad-soft);border-color:rgba(199,155,255,.4);color:#fff}
.ed-track{position:relative;height:36px;border-radius:10px;background:rgba(255,255,255,.06);
  border:1px solid var(--hair);overflow:hidden;cursor:pointer;margin-top:2px}
.ed-track .sel{position:absolute;top:0;bottom:0;background:var(--grad-soft);
  border-left:2px solid var(--acc);border-right:2px solid var(--acc)}
/* margin-left pulls the bar half its width so it stays visible at both ends
   instead of being clipped away by the track's overflow:hidden at 0%/100%. */
.ed-track .play{position:absolute;top:0;bottom:0;width:3px;margin-left:-1.5px;
  background:#fff;box-shadow:0 0 6px #fff;pointer-events:none}
.ed-prog{height:6px;border-radius:99px;background:rgba(255,255,255,.08);overflow:hidden}
.ed-prog i{display:block;height:100%;background:var(--grad);border-radius:99px;transition:width .15s}
.ed-note{font-size:11px;color:var(--fg-3);line-height:1.5}
.pub-row{display:flex;align-items:center;gap:9px;margin-top:7px}
.pub-row .rd-btn{flex-shrink:0;min-width:104px;justify-content:center}
.pub-ok{font-size:11px;color:var(--acc)}
.pub-warn{font-size:11px;color:#ff9a52;line-height:1.4}
.q-row{display:flex;align-items:center;gap:10px;padding:9px 12px;margin-bottom:6px;
  border-radius:12px;background:rgba(255,255,255,.03);border:1px solid var(--hair)}
.q-row.due{border-color:rgba(168,85,247,.5);background:var(--grad-soft)}
.q-row.missed{border-color:rgba(255,138,76,.35)}
.q-when{flex-shrink:0;min-width:74px;font-size:11.5px;font-weight:700;color:var(--acc)}
.q-row.missed .q-when{color:#ff9a52}
.q-mid{flex:1;min-width:0}
.q-name{font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.q-sub{font-size:11px;color:var(--fg-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-list{display:flex;flex-direction:column;gap:14px;margin-top:14px}
.sc-card{display:flex;gap:16px;padding:16px;border-radius:18px}
@media(max-width:760px){.sc-card{flex-direction:column}}
.sc-card.due{border-color:rgba(168,85,247,.55)}
.sc-card.missed{border-color:rgba(255,138,76,.4)}
.sc-media{flex-shrink:0;width:184px}
@media(max-width:760px){.sc-media{width:100%}}
.sc-media video{width:100%;border-radius:12px;background:#000;aspect-ratio:9/16;object-fit:contain}
.sc-body{flex:1;min-width:0;display:flex;flex-direction:column;gap:10px}
.sc-top{display:flex;align-items:center;gap:10px}
.sc-name{flex:1;min-width:0;font-size:14px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-when{font-size:11.5px;font-weight:800;color:var(--acc);flex-shrink:0}
.sc-when.missed{color:#ff9a52}
.sc-plats{display:flex;flex-direction:column;gap:6px}
.sc-plat{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.sc-plat .rd-btn{min-width:96px;justify-content:center}
.sc-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.sr-tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
@media(max-width:620px){.sr-tiles{grid-template-columns:repeat(2,1fr)}}
.sr-tile{background:rgba(255,255,255,.04);border:1px solid var(--hair);
  border-radius:12px;padding:12px;text-align:center}
.sr-tile .k{font-size:10.5px;color:var(--fg-3);text-transform:uppercase;letter-spacing:.05em}
.sr-tile .v{font-size:23px;font-weight:800;letter-spacing:-.02em;margin-top:3px}
.sr-list{margin-top:10px;display:flex;flex-direction:column;gap:7px}
.sr-row{display:flex;align-items:center;gap:11px}
.sr-when{flex-shrink:0;width:104px;font-size:11.5px;color:var(--fg-3)}
.sr-bar{flex:1;height:8px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
.sr-bar i{display:block;height:100%;background:var(--grad);border-radius:99px}
.sr-nums{flex-shrink:0;font-size:11.5px;color:var(--fg-2)}
.rd-lost{display:flex;align-items:center;gap:13px;padding:13px 16px;margin-bottom:14px;
  border-radius:14px;background:rgba(255,138,76,.09);border:1px solid rgba(255,138,76,.3)}
.rd-lost .ic{flex-shrink:0;color:#ff9a52;display:grid;place-items:center}
.rd-lost .tx{flex:1;min-width:0;font-size:12.8px;line-height:1.5;color:var(--fg-2)}
.rd-lost .tx b{color:#ff9a52}
.rd-lost-x{flex-shrink:0;background:none;border:0;color:var(--fg-3);font-size:22px;
  line-height:1;cursor:pointer;padding:0 2px;transition:color .12s}
.rd-lost-x:hover{color:var(--fg-1)}
@media(max-width:640px){.rd-lost{flex-direction:column;align-items:flex-start}}
.rv{max-width:460px;width:100%;padding:26px 28px;border-radius:20px;
  display:flex;flex-direction:column;gap:12px}
.rv h3{font-size:19px;font-weight:800;margin:0}
.rv-sub{font-size:12.5px;color:var(--fg-3);margin:0;line-height:1.5}
.rv-stars{display:flex;gap:4px;margin:2px 0}
.rv-star{background:none;border:0;cursor:pointer;font-size:32px;line-height:1;
  padding:0 2px;color:rgba(255,255,255,.2);transition:color .12s}
.rv-star.on{color:#ffc75a}
.rv-check{display:flex;align-items:flex-start;gap:8px;font-size:12.5px;
  color:var(--fg-2);cursor:pointer;line-height:1.45}
.rv-check input{margin-top:2px;flex-shrink:0}
.rv-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:4px}
.rv-actions .rd-btn.grad{flex:1 1 120px;justify-content:center}
.rv-done{display:flex;flex-direction:column;align-items:center;gap:10px;
  padding:22px 0;color:var(--acc);text-align:center}
.ed-warn{font-size:11.5px;color:#ff9a52;background:rgba(255,138,76,.1);
  border:1px solid rgba(255,138,76,.28);border-radius:10px;padding:8px 10px;line-height:1.45}
.rd-how{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
@media(max-width:760px){.rd-how{grid-template-columns:1fr}}
.rd-step{display:flex;gap:11px;align-items:flex-start;padding:13px 15px;border-radius:14px;
  background:rgba(255,255,255,.025);border:1px solid var(--hair)}
.rd-step .sn{flex-shrink:0;width:22px;height:22px;border-radius:7px;display:grid;place-items:center;
  background:var(--grad-soft);color:var(--acc);font-size:11.5px;font-weight:800}
.rd-step .st{font-size:13px;font-weight:700;display:flex;align-items:center;gap:6px;margin-bottom:3px}
.rd-step .sb{font-size:11.5px;color:var(--fg-3);line-height:1.5}
.rd-picks{display:flex;gap:8px;flex-wrap:wrap}
.rd-pick{display:inline-flex;align-items:center;gap:7px;max-width:220px;padding:7px 11px;
  border-radius:99px;background:rgba(255,255,255,.05);border:1px solid var(--hair);
  color:var(--fg-2);font-size:12px;font-weight:600;cursor:pointer;transition:.15s}
.rd-pick:hover{background:var(--grad-soft);border-color:rgba(199,155,255,.4);color:#fff}
.rd-pick span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rd-uprow{display:flex;align-items:center;gap:11px;padding:10px 0;border-bottom:1px solid var(--hair)}
.rd-uprow:last-child{border-bottom:none}
.rd-uprow .pb{flex:1;height:6px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
.rd-uprow .pb i{display:block;height:100%;background:var(--grad);border-radius:99px;transition:width .2s}
.rd-preset-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.rd-preset{border-radius:14px;padding:15px;border:1px solid var(--hair);background:rgba(255,255,255,.02)}
.rd-preset .pn{font-weight:700;font-size:14px;text-transform:capitalize;display:flex;align-items:center;justify-content:space-between}
.rd-preset .pn .badge2{font-size:10px;font-weight:700;color:var(--acc);background:var(--grad-soft);padding:3px 8px;border-radius:99px}
.rd-preset .pr{display:flex;justify-content:space-between;font-size:11px;color:var(--fg-2);margin-top:9px}
.rd-preset .pr b{color:var(--fg);font-weight:700;font-variant-numeric:tabular-nums}
.rd-field{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 0;border-bottom:1px solid var(--hair)}
.rd-field:last-child{border-bottom:none;padding-bottom:0}
.rd-field:first-of-type{padding-top:0}
.rd-field .fl{font-size:13px;font-weight:500}
.rd-field .fd{font-size:11px;color:var(--fg-3);margin-top:3px}
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
.rd-modal-close{position:absolute;top:14px;right:14px;width:36px;height:36px;border-radius:50%;border:none;
  /* Sits ON the player, so a blur here re-blurs that patch of video every
     frame it decodes. Opaque background instead — same look, no per-frame work. */
  background:rgba(10,8,14,.86);color:#fff;display:grid;place-items:center;z-index:2}
.rd-modal-close:hover{background:rgba(10,8,14,.85)}
.rd-modal-play{position:absolute;inset:0;display:grid;place-items:center}
.rd-modal-play .ring{width:76px;height:76px;border-radius:50%;display:grid;place-items:center;padding-left:4px;background:var(--grad);color:#fff;box-shadow:var(--glow)}
.rd-modal-body{padding:20px 22px;overflow-y:auto}
.rd-modal-head{display:flex;align-items:center;gap:12px}
.rd-modal-head .av{width:40px;height:40px;border-radius:12px;background:var(--grad);display:grid;place-items:center;font-weight:800;color:#1a0322}
.rd-modal-head h3{font-size:17px;font-weight:700;letter-spacing:-.02em}
.rd-modal-head .mt{font-size:12px;color:var(--fg-2);margin-top:2px}
.rd-modal-grid{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:20px}
.rd-sigbar{margin-bottom:12px}
.rd-sigbar .sh{display:flex;justify-content:space-between;font-size:12px;margin-bottom:5px}
.rd-sigbar .sh .sk{color:var(--fg-2)}
.rd-sigbar .sh .sv{font-weight:700;font-variant-numeric:tabular-nums}
.rd-sigbar .st{height:7px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
.rd-sigbar .sf{height:100%;border-radius:99px;background:var(--grad)}
.rd-modal-actions{display:flex;gap:10px;margin-top:8px}
.rd-modal-actions .rd-btn{flex:1}
.rd-meta-row{display:flex;justify-content:space-between;font-size:13px;padding:9px 0;border-bottom:1px solid var(--hair)}
.rd-meta-row:last-child{border-bottom:none}
.rd-meta-row .mk{color:var(--fg-2)}
.rd-meta-row .mv{font-weight:600}
@media(max-width:900px){
  .rd-body{grid-template-columns:1fr;grid-template-rows:auto 1fr}
  .rd-col{max-height:300px}
  .rd-stats{grid-template-columns:repeat(2,1fr)}
  .rd-streams-layout{grid-template-columns:1fr}
  .rd-metrics{grid-template-columns:repeat(2,1fr)}
  .rd-modal-grid{grid-template-columns:1fr}
}
@media(max-width:700px){
  /* Scrollable instead of fixed-height */
  body{overflow:auto}
  .rd-app{grid-template-columns:1fr;height:auto;min-height:100dvh}
  .rd-frame{min-height:0;overflow:visible}
  /* No bottom bar to clear anymore — content runs to the bottom of the screen. */
  .rd-screen{overflow:visible;padding-bottom:16px}
  .rd-navscrim{display:block}

  /* Vertical nav → slide-out drawer. The old bottom tab bar cost 58px of
     every screen and squeezed 9 tabs into it; the drawer gives the content
     the full viewport and each destination a full-width row. */
  .rd-nav{position:fixed;top:0;bottom:0;left:0;z-index:60;width:268px;max-width:82vw;
    flex-direction:column;align-items:stretch;gap:4px;padding:18px 12px calc(18px + env(safe-area-inset-bottom));
    border-right:1px solid var(--hair);border-top:none;overflow-y:auto;
    background:#0c0c12;
    transform:translateX(-102%);transition:transform .26s cubic-bezier(.4,0,.2,1);
    box-shadow:0 0 40px rgba(0,0,0,.6)}
  .rd-nav.open{transform:translateX(0)}
  .rd-nav .logo{display:flex;justify-content:center;margin-bottom:14px}
  .rd-nav .sp{flex:1;display:block;min-height:10px}
  .rd-navitem{width:auto;height:auto;min-height:48px;flex-direction:row;justify-content:flex-start;
    align-items:center;gap:12px;padding:0 14px;border-radius:12px;font-size:14px;font-weight:600;text-align:left}
  /* Badge is first in DOM (absolute on desktop); in the row layout it belongs
     at the end — order:3 keeps it there instead of shoving the icon/label right. */
  .rd-navitem .navbadge{position:static;order:3;margin-left:auto}
  /* Scrim: tap anywhere off the drawer to dismiss. */
  .rd-navscrim{position:fixed;inset:0;z-index:59;background:rgba(0,0,0,.55);
    opacity:0;pointer-events:none;transition:opacity .26s ease;-webkit-backdrop-filter:blur(2px);backdrop-filter:blur(2px)}
  .rd-navscrim.open{opacity:1;pointer-events:auto}
  .rd-menubtn{display:inline-flex;align-items:center;justify-content:center;width:38px;height:38px;
    flex-shrink:0;border-radius:11px;background:rgba(255,255,255,.06);border:1px solid var(--hair);
    color:var(--fg);cursor:pointer}

  /* Frame: let grid rows auto-size for page scroll */
  .rd-frame{grid-template-rows:56px auto}

  /* Header */
  .rd-header{padding:0 12px;gap:10px;height:56px}
  .rd-menubtn{display:inline-flex}
  .rd-header .htitle{font-size:15px}
  .rd-header .hsub{display:none}
  .rd-header .rd-live{display:none}

  /* Review screen */
  .rd-body{grid-template-columns:1fr;padding:12px;gap:12px;overflow:visible}
  .rd-col{max-height:none}
  .rd-main{gap:12px;overflow:visible}
  .rd-stats{grid-template-columns:repeat(2,1fr);gap:8px}
  .rd-stat{padding:12px 14px}
  .rd-stat .v{font-size:22px}
  .rd-toolbar{flex-wrap:wrap;gap:8px}
  .rd-filters{margin-left:0;width:100%;justify-content:space-between}
  .rd-filter{flex:1;text-align:center;padding:7px 6px}
  .rd-grid{grid-template-columns:1fr;padding-right:0;overflow:visible}
  .rd-clip{height:auto}

  /* Streams screen */
  .rd-streams-layout{grid-template-columns:1fr;padding:12px;gap:12px;overflow:visible}
  .rd-chanlist{overflow-y:visible;max-height:none}
  .rd-detail{overflow-y:visible}
  .rd-metrics{grid-template-columns:repeat(2,1fr);gap:8px}
  .rd-weight .wl{width:90px;font-size:11px}

  /* Settings */
  .rd-scroll{padding:12px}
  .rd-settings{gap:12px}
  .rd-preset-grid{grid-template-columns:1fr;gap:10px}
  .rd-card{padding:16px}

  /* Modal: full-screen sheet */
  .rd-modal-bg{padding:0;align-items:flex-end}
  .rd-modal{width:100%;max-height:92dvh;border-radius:22px 22px 0 0;overflow:hidden}
  .rd-modal-media{padding-bottom:56.25%}
  .rd-modal-body{flex:1;min-height:0;overflow-y:auto;padding:14px 16px}
  .rd-modal-grid{grid-template-columns:1fr;gap:16px}
  .rd-modal-actions{flex-wrap:wrap}

  /* Header: hide username text, just show avatar on narrow screens */
  .rd-user-chip .uc-name{display:none}
  .rd-user-chip{padding:2px;gap:0}

  /* Toast: no bottom bar to sit above now */
  .rd-toast{bottom:20px;font-size:12px;padding:10px 16px;max-width:90vw;text-align:center}
}
/* ═══ Aurora v2 — pure-CSS visual layer. Appended last so it wins at equal
   specificity; NO markup/logic depends on it. Theme-aware: every accent is
   derived from var(--acc)/var(--acc-2) via color-mix, so the (gated) kick
   theme keeps working. Wrapped fallbacks degrade to the original look. ═══ */
.rd-app::before{content:'';position:fixed;inset:0;z-index:-2;
  background:
    radial-gradient(1050px 560px at 15% -10%,rgba(168,85,247,.26),transparent 62%),
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
.rd-stat{isolation:isolate}
.rd-stat::before{content:'';position:absolute;top:-34px;right:-34px;width:120px;height:120px;
  border-radius:50%;z-index:-1;pointer-events:none;
  background:radial-gradient(circle,color-mix(in srgb,var(--acc-2) 20%,transparent),transparent 70%)}
.rd-btn.grad{position:relative;overflow:hidden}
.rd-btn.grad::after{content:'';position:absolute;top:0;left:-80%;width:50%;height:100%;
  background:linear-gradient(100deg,transparent,rgba(255,255,255,.34),transparent);
  transform:skewX(-20deg);transition:left .5s ease}
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
  @keyframes rdLogoGlow{from{filter:drop-shadow(0 0 9px rgba(199,155,255,.4))}
    to{filter:drop-shadow(0 0 17px rgba(199,155,255,.75))}}
  .rd-screen{animation:rdScreenIn .4s cubic-bezier(.16,1,.3,1)}
  @keyframes rdScreenIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
  .rd-grid .rd-clip{animation:rdCardIn .5s cubic-bezier(.16,1,.3,1) backwards}
  .rd-grid .rd-clip:nth-child(2){animation-delay:.05s}
  .rd-grid .rd-clip:nth-child(3){animation-delay:.1s}
  .rd-grid .rd-clip:nth-child(4){animation-delay:.15s}
  .rd-grid .rd-clip:nth-child(5){animation-delay:.2s}
  .rd-grid .rd-clip:nth-child(6){animation-delay:.25s}
  @keyframes rdCardIn{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
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
  .rd-frame{grid-template-rows:auto 1fr}
  .rd-header{flex-wrap:wrap;height:auto;min-height:0;padding:10px 12px;gap:8px 10px}
  .rd-header>*{min-width:0}
  .rd-header .htitle{font-size:16px}
  .rd-header .hsub{display:none}
  .plat-sw-btn{padding:7px 13px;font-size:11px}
  .rd-live{font-size:11px;padding:5px 9px}
  .rd-stats{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
  .rd-stat{padding:12px 13px}
  .rd-stat .k{white-space:normal;line-height:1.3}
  .rd-stat .v{font-size:24px}
  .rd-toolbar{flex-wrap:wrap;gap:8px}
  .rd-addrow{flex-wrap:wrap}
  .rd-addrow .rd-input{flex:1 1 100%}
  .rd-addrow .rd-suggwrap{flex:1 1 100%}
  .rd-addrow .rd-select{flex:1}
  .rd-grid{grid-template-columns:1fr}
  /* The bottom nav is gone (drawer now), so scrolling content keeps only a
     small breathing gap instead of reserving a whole tab bar's height. */
  .rd-body,.rd-scroll,.rd-streams-layout{padding-bottom:18px}
  .rd-detail,.rd-chanlist{padding-bottom:18px}
  /* Opaque drawer: the aurora layer above gives .rd-nav a translucent
     gradient, which would let page content read through a panel that now
     floats OVER the content instead of sitting beside it. */
  .rd-nav{background:#0c0c12}
  /* First-run welcome card: phone-comfortable padding */
  .wm-card{padding:26px 20px !important;border-radius:18px !important}
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
  .rd-detail-head h2{font-size:19px;word-break:break-word}
}
</style>
</head>
<body>
<div id="root"></div>
<script src="https://unpkg.com/react@18.3.1/umd/react.production.min.js" crossorigin="anonymous" integrity="sha384-DGyLxAyjq0f9SPpVevD6IgztCFlnMF6oW/XQGmfe+IsZ8TqEiDrcHkMLKI6fiB/Z"></script>
<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js" crossorigin="anonymous" integrity="sha384-gTGxhz21lVGYNMcdJOyq01Edg0jhn/c22nsx0kyqP0TxaV5WVdsSH1fSDUf5YJj1"></script>
<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" crossorigin="anonymous" integrity="sha384-m08KidiNqLdpJqLq95G/LEi8Qvjl/xUYll3QILypMoQ65QorJ9Lvtp2RXYGBFj1y"></script>
<script type="text/babel">
const { useState, useEffect, useRef, useCallback } = React;

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
        <stop offset="0" stopColor="rgba(168,85,247,.5)"/><stop offset="1" stopColor="rgba(168,85,247,0)"/>
      </linearGradient></defs>
      {[25,50,75].map(y=><line key={y} x1="0" x2={w} y1={h-(y/100)*(h-2*pad)-pad} y2={h-(y/100)*(h-2*pad)-pad} stroke="rgba(255,255,255,.05)" strokeWidth="1"/>)}
      <path d={area} fill="url(#cg)"/>
      <path d={line} fill="none" stroke="#c79bff" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke"/>
      <circle cx={last[0]} cy={last[1]} r="3.5" fill="#fff"/>
    </svg>
  );
}

function RdStat({ icon, k, v, sub, accent }) {
  return (
    <div className={'rd-stat glass'+(accent?' accent':'')}>
      <div className="k"><span className="si"><Icon name={icon} size={15}/></span>{k}</div>
      <div className="v">{v}</div>
      <div className="sub">{sub}</div>
    </div>
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
  const statusColor = s.status==='live' ? 'var(--live)' : s.status==='reconnecting' ? 'var(--pending)' : 'var(--fg-2)';
  const platColor = s.platform==='kick' ? '#53fc18' : 'var(--acc)';
  return (
    <div className="rd-stream">
      <div className="rd-stream-top">
        <div>
          <div className="nm"><span className="plat" style={{background:platColor,boxShadow:`0 0 8px ${platColor}`}}/>{s.channel}</div>
          <div className="mt">
            <span className="rd-chip" style={s.platform==='kick'?{background:'rgba(83,252,24,.12)',color:'#53fc18',border:'1px solid rgba(83,252,24,.3)'}:{}}>{s.platform}</span>
            <span className="rd-chip">{s.preset}</span>
            <span style={{color:statusColor,fontWeight:600}}>{s.status}</span>
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
        <div className="rd-sigs" style={{marginTop:6}}>{(()=>{
          const live = s.status==='live';
          const chips = [];
          if (staleSecs===null) chips.push(<span className="rd-sig" key="hb" style={{color:'var(--fg-3)'}}>&#9679; engine — waiting for first update…</span>);
          else if (staleSecs<=10) chips.push(<span className="rd-sig" key="hb" style={{color:'#86efac'}}>&#9679; engine live</span>);
          else if (!live) chips.push(<span className="rd-sig" key="hb" style={{color:'var(--fg-3)'}}>&#9679; engine idle — stream {s.status}</span>);
          else chips.push(<span className="rd-sig" key="hb" style={{background:'rgba(239,68,68,.14)',color:'#f87171'}}>&#9679; engine — no updates for {staleSecs}s</span>);
          if (breakdown._chat_vps!=null) {
            const last = breakdown._last_chat_s;
            const col = last<0 ? '#f87171' : last<30 ? '#86efac' : last<120 ? 'var(--pending)' : '#f87171';
            const fresh = last<0 ? 'no chat received yet' : 'last msg '+(last<=1?'just now':last+'s ago');
            chips.push(<span className="rd-sig" key="chat" style={{color:col}}>CHAT {breakdown._chat_vps}/s{breakdown._chat_base_vps>0?' (base '+breakdown._chat_base_vps+')':''} &middot; {fresh}</span>);
          }
          if (breakdown._threshold!=null) chips.push(<span className="rd-sig" key="thr" style={{color:'var(--fg-2)'}}>fires at {breakdown._threshold}</span>);
          return chips;
        })()}</div>
        <div className="rd-sigs">{Object.entries(breakdown).filter(([k])=>!k.startsWith('_')).map(([k,v])=>{
          const active=typeof v==='number'&&v>0.05;
          return <span className="rd-sig" key={k} style={active?{background:'rgba(168,85,247,.18)',color:'var(--fg-1)'}:{}}>{k}: {typeof v==='number'?v.toFixed(2):v}</span>;
        })}</div>
        <div className="rd-sigs" style={{marginTop:4}}>{[
          breakdown._audio_db!=null&&<span className="rd-sig" key="adb" style={{color:breakdown._audio_db>-50?'#86efac':'var(--fg-3)'}}>AUDIO {breakdown._audio_db}dB peak {breakdown._audio_peak_db}dB (base {breakdown._audio_base_db}dB)</span>,
          breakdown._viewers!=null&&<span className="rd-sig" key="vc" style={{color:'var(--fg-2)'}}>VIEWERS {breakdown._viewers} (base {breakdown._viewer_base})</span>,
        ].filter(Boolean)}</div>
      </div>
      <div className="rd-profile">
        <div className="rd-pgrid">
          <div className="rd-pcell"><div className="k">Threshold</div><div className="v">{p.trigger_threshold?p.trigger_threshold.toFixed(0):'—'}</div></div>
          <div className="rd-pcell"><div className="k">Velocity</div><div className="v">{p.avg_velocity>0?p.avg_velocity.toFixed(1):'—'}<span style={{fontSize:10,color:'var(--fg-3)',fontWeight:500}}> m/s</span></div></div>
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

function RdClip({ clip, onApprove, onReject, onDelete, onOpen, libraryMode }) {
  const score = Math.round(clip.score||clip.trigger_score||0);  // VOD clips carry 'score'; both are 0-100
  const dur = fmtDur(clip.duration_seconds);
  const time = fmtTime(clip.created_at);
  const title = clip.clip_title||clip.stream_title||'Live Stream';
  const thumb = clip.thumbnail_url || '';
  const twHref = clip.twitch_url || '';
  return (
    <div className="rd-clip">
      <div className="rd-media" style={{cursor:'pointer'}} onClick={()=>onOpen&&onOpen(clip)}>
        {thumb
          ? <img src={hiResThumb(thumb)} data-orig={hiResThumb(thumb)!==thumb?thumb:''} alt="" onError={e=>thumbFallback(e, clip.channel)} style={{position:'absolute',inset:0,width:'100%',height:'100%',objectFit:'cover'}}/>
          : <div className="rd-thumb" style={{background:thumbFor(clip.channel)}}/>}
        <div className="rd-play"><span className="ring"><Icon name="play" size={20}/></span></div>
        <span className="rd-scorebadge"><span className="pip" style={{background:scoreColor(score)}}/>{score}%</span>
        {clip.virality_score>0 && <span className={'rd-viralbadge'+(clip.virality_score>=65?' hot':clip.virality_score>=35?' warm':'')} title="Virality — how shareable this moment looks">
          <Icon name="trending" size={12}/>{Math.round(clip.virality_score)}% viral
        </span>}
        {clip.viewer_clipped && <span className="rd-clippedbadge"
          title={`Real viewers clipped this moment on Twitch — ${clip.viewer_clip_views||0} views`}>
          <Icon name="check" size={11}/>{(clip.viewer_clip_views||0).toLocaleString()} clipped it
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
            {twHref && <a href={twHref} target="_blank" rel="noopener" className="rd-btn sm" style={{textDecoration:'none',flex:'0 0 auto'}} title="Open on Twitch" onClick={e=>e.stopPropagation()}><Icon name="play" size={13}/></a>}
          </> : libraryMode && clip.status==='approved' ? <>
            {twHref && <a href={twHref} target="_blank" rel="noopener" className="rd-btn grad sm" style={{textDecoration:'none'}} onClick={e=>e.stopPropagation()}><Icon name="play" size={13}/>Open on Twitch</a>}
            {onDelete && <button className="rd-btn sm" style={{flex:'0 0 auto',background:'rgba(239,68,68,.1)',color:'var(--danger)',borderColor:'rgba(239,68,68,.2)'}} title="Remove from library" onClick={e=>{e.stopPropagation();onDelete(clip.id)}}><Icon name="trash" size={13}/></button>}
          </> : <span className="rd-resolved">
            <Icon name={clip.status==='approved'?'check':'x'} size={14} style={{color:clip.status==='approved'?'var(--live)':'var(--danger)'}}/>
            {clip.status==='approved'?'Approved':'Rejected'}
            {twHref && <a href={twHref} target="_blank" rel="noopener" className="rd-btn sm" style={{marginLeft:4,textDecoration:'none',flex:'0 0 auto'}} title="Open on Twitch" onClick={e=>e.stopPropagation()}><Icon name="play" size={13}/></a>}
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
  return <span style={{display:'inline-block',width:13,height:13,border:'2px solid rgba(255,255,255,.3)',borderTopColor:'#fff',borderRadius:'50%',animation:'spin 0.7s linear infinite',marginRight:6,verticalAlign:'middle'}}/>;
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

function ClipModal({ clip, onClose, onApprove, onReject, isAdmin, featured, onFeature }) {
  // Retry counter for the Twitch iframe. Declared BEFORE the null-clip early
  // return: hooks must run on every render or React errors when the modal
  // opens (same trap documented on the VOD plan gate).
  const [playerTry, setPlayerTry] = useState(0);
  useEffect(()=>{ setPlayerTry(0); },[clip&&clip.id]);
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
  const embedSrc = embed
    ? embed + (embed.indexOf('?')>=0?'&':'?') + 'parent=' + location.hostname + '&autoplay=false'
    : '';
  // With no inline embed (no embed_url stored) the clip can only play on
  // Twitch — make the whole media area a tap target so it opens even when the
  // thumbnail image is broken (the old absolutely-positioned play link was an
  // unreliable hit target on mobile once the broken <img> collapsed).
  const canLinkOut = !embedSrc && !!twHref;
  const openClip = () => { if (twHref) window.open(twHref, '_blank', 'noopener'); };
  const sigMap = {};
  for (const s of (clip.trigger_signals||[])) {
    const k = (s.type||'').replace('SignalType.','');
    sigMap[k] = (s.value||0)*100;
  }
  const sigKeys = [['CHAT_VELOCITY','Chat velocity'],['KEYWORD','Keyword hits'],['SENTIMENT','Sentiment'],['AUDIO_SPIKE','Audio spike']];

  return (
    <div className="rd-modal-bg" onClick={onClose}>
      <div className="rd-modal" onClick={e=>e.stopPropagation()}>
        <div className="rd-modal-media" style={canLinkOut?{cursor:'pointer'}:undefined} onClick={canLinkOut?openClip:undefined}>
          {embedSrc
            ? <iframe key={playerTry} src={embedSrc+'&_r='+playerTry} allowFullScreen frameBorder="0" scrolling="no" style={{position:'absolute',inset:0,width:'100%',height:'100%',background:'#000'}}/>
            : thumb
              ? <><img src={hiResThumb(thumb)} data-orig={hiResThumb(thumb)!==thumb?thumb:''} alt="" onError={e=>thumbFallback(e, clip.channel)} style={{position:'absolute',inset:0,width:'100%',height:'100%',objectFit:'cover'}}/>{twHref&&<div className="rd-modal-play"><span className="ring"><Icon name="play" size={26}/></span></div>}</>
              : <><div className="thumb" style={{background:thumbFor(clip.channel)}}/><div className="rd-modal-play"><span className="ring"><Icon name="play" size={26}/></span></div></>}
          <button className="rd-modal-close" onClick={e=>{e.stopPropagation();onClose();}}><Icon name="x" size={16}/></button>
          <span className="rd-scorebadge" style={{top:14,right:60}}><span className="pip" style={{background:scoreColor(score)}}/>{score}% trigger</span>
        </div>

        {embedSrc && <div style={{display:'flex',alignItems:'center',justifyContent:'center',gap:14,padding:'9px 12px',fontSize:12.5,background:'rgba(99,102,241,.10)',borderBottom:'1px solid rgba(255,255,255,.06)'}}>
          <span style={{color:'var(--fg-3)'}}>Player showing an error?</span>
          <button className="rd-btn sm" onClick={()=>setPlayerTry(t=>t+1)}>Reload player</button>
          {twHref && <a href={twHref} target="_blank" rel="noopener" style={{color:'#a5b4fc',textDecoration:'none',fontWeight:600}}>Watch on Twitch ↗</a>}
        </div>}

        <div className="rd-modal-body">
          <div className="rd-modal-head">
            <span className="av">{initials(clip.channel)}</span>
            <div style={{flex:1}}><h3>{title}</h3><div className="mt">{clip.channel} · {clip.game||'stream'} · {time}</div></div>
            <span className={'rd-status '+clip.status}>{clip.status}</span>
          </div>
          <div className="rd-modal-grid">
            <div>
              <div className="rd-eyebrow" style={{marginBottom:14}}>Why it fired</div>
              {sigKeys.map(([k,lbl])=>{
                const v=sigMap[k]||0;
                return <div className="rd-sigbar" key={k}>
                  <div className="sh"><span className="sk">{lbl}</span><span className="sv" style={{color:scoreColor(v)}}>{v.toFixed(0)}%</span></div>
                  <div className="st"><div className="sf" style={{width:v+'%'}}/></div>
                </div>;
              })}
            </div>
            <div>
              <div className="rd-eyebrow" style={{marginBottom:14}}>Details</div>
              <div className="rd-meta-row"><span className="mk">Duration</span><span className="mv">{dur||'—'}</span></div>
              <div className="rd-meta-row"><span className="mk">Platform</span><span className="mv" style={{textTransform:'capitalize'}}>{clip.platform}</span></div>
              <div className="rd-meta-row"><span className="mk">Game</span><span className="mv">{clip.game||'—'}</span></div>
              <div className="rd-meta-row"><span className="mk">Captured</span><span className="mv">{time}</span></div>
              {clip.virality_score>0 && <div className="rd-meta-row"><span className="mk">Virality</span><span className="mv">{Math.round(clip.virality_score)}%</span></div>}
              {twHref && <a href={twHref} target="_blank" rel="noopener" className="rd-btn grad sm" style={{textDecoration:'none',marginTop:12,width:'100%',justifyContent:'center'}}><Icon name="play" size={14}/>Open on Twitch</a>}
              {clip.status==='pending' && <div className="rd-modal-actions">
                <button className="rd-btn live sm" onClick={()=>{onApprove(clip.id);onClose()}}><Icon name="check" size={14}/>Approve</button>
                <button className="rd-btn danger sm" onClick={()=>{onReject(clip.id);onClose()}}><Icon name="x" size={14}/>Reject</button>
              </div>}
              {isAdmin && clip.status==='approved' && clip.platform==='twitch' && onFeature &&
                <button className="rd-btn sm" style={{marginTop:10,width:'100%',justifyContent:'center',
                    background:featured?'rgba(255,194,92,.14)':'rgba(168,85,247,.14)',
                    border:featured?'1px solid rgba(255,194,92,.35)':'1px solid rgba(168,85,247,.35)',
                    color:featured?'#ffc25c':'#c79bff'}}
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
  const clipsArr = Object.values(clips);
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
  // typing = Twitch partial-name search (debounced). Twitch-only — the data
  // source is Helix, so the dropdown stays away on other platforms.
  const [sugg, setSugg] = useState(null);
  const [suggOpen, setSuggOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const suggT = useRef(null);
  const inputRef = useRef(null);
  const canSugg = activePlatform === 'twitch';
  const fetchSugg = (q) => {
    fetch('/streams/suggest' + (q ? '?q=' + encodeURIComponent(q) : ''))
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
                              <div style={{display:'flex',alignItems:'center',gap:6}}>
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
                              <div style={{display:'flex',alignItems:'center',gap:6}}>
                                <span style={{fontWeight:600,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{p.name||p.login}</span>
                                <span className="rd-sugglive">LIVE</span>
                              </div>
                              <div className="meta2">{fmtViewers(p.viewers)} watching · {p.game||''}</div>
                            </div>
                          </div>))}
                      </>}
                      {!(sugg.recent||[]).length && !(sugg.popular||[]).length &&
                        <div className="rd-suggempty">Type a channel name to search Twitch</div>}
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
          <div style={{display:'flex',alignItems:'center',gap:6,marginTop:6,padding:'5px 10px',borderRadius:8,background:'rgba(255,255,255,.04)',border:'1px solid var(--hair)'}}>
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
    <span style={{display:'inline-flex',gap:6,alignItems:'center'}}>
      <span style={{fontSize:12,color:'var(--fg-2)',fontWeight:600}}>
        Clear {pending} clip{pending===1?'':'s'}?
      </span>
      <button className="rd-btn sm" disabled={busy} onClick={run}
        style={{background:'rgba(239,68,68,.16)',border:'1px solid rgba(239,68,68,.5)',color:'#fca5a5'}}>
        {busy ? 'Clearing…' : 'Yes, clear'}
      </button>
      <button className="rd-btn sm" disabled={busy} onClick={()=>setArmed(false)}
        style={{background:'rgba(255,255,255,.06)',border:'1px solid var(--hair)',color:'var(--fg-2)'}}>
        Cancel
      </button>
    </span>
  );
}

function ReviewScreen({ streams, scores, clips, filter, setFilter, onApprove, onReject, onOpen, lost, me, onDismissLost }) {
  const [showCull, setShowCull] = useState(false);
  const [sortBy, setSortBy] = useState('newest');
  const [chanFilter, setChanFilter] = useState('all');
  const clipsArr = Object.values(clips);
  const pending = clipsArr.filter(c=>c.status==='pending').length;
  const approved = clipsArr.filter(c=>c.status==='approved').length;
  const streamsArr = Object.values(streams);
  const avgScore = streamsArr.length ? Math.round(streamsArr.reduce((a,s)=>a+(scores[s.channel]?.score||0),0)/streamsArr.length) : 0;
  // Streamer filter options come from the clips themselves, so the moment a
  // new streamer gets clipped (clip_ready over the WS) they become filterable.
  // If the selected streamer's clips all disappear (culled/removed), fall back
  // to 'all' rather than pinning the grid to an empty, invisible filter.
  const channels = [...new Set(clipsArr.map(c=>c.channel).filter(Boolean))].sort();
  const effChan = channels.includes(chanFilter) ? chanFilter : 'all';
  const filtered = clipsArr.filter(c=>(filter==='all'||c.status===filter)&&(effChan==='all'||c.channel===effChan));
  const shown = [...filtered].sort((a,b)=>{
    if(sortBy==='virality') return (b.virality_score||0)-(a.virality_score||0);
    // default: pending first, then newest
    const sp={pending:0,approved:1,rejected:2};
    if(sp[a.status]!==sp[b.status]) return sp[a.status]-sp[b.status];
    return (b.created_at||0)-(a.created_at||0);
  });
  // The cap now REFUSES the new moment rather than deleting an old clip, so
  // "we did not clip this" is finally the accurate wording. The clip is never
  // created on Twitch either — the processor checks before spending the Helix
  // call — so there is no orphan for the user to find and contradict us with.
  const lostN = lost ? (lost.missed_24h || lost.lost_24h || 1) : 0;
  const nextPlan = lost && lost.next_plan;
  return (
    <div className="rd-body rd-body-full" style={{flex:1}}>
      <section className="rd-main">
        {lostN > 0 && <div className="rd-lost">
          <span className="ic"><Icon name="zap" size={16}/></span>
          <div className="tx">
            <b>Your review queue is full{lost.limit ? ' at ' + lost.limit + ' clips' : ''}.</b>{' '}
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
        <div className="rd-stats">
          <RdStat icon="sparkles" k="Pending review" v={pending} sub="awaiting your call" accent/>
          <RdStat icon="check" k="Approved" v={approved} sub="ready to use"/>
          <RdStat icon="radio" k="Active streams" v={streamsArr.length} sub="monitored live"/>
          <RdStat icon="trending" k="Avg trigger" v={avgScore} sub="across all channels"/>
        </div>
        <div className="rd-toolbar">
          <h2>Clip review</h2>
          <div style={{display:'flex',gap:8,alignItems:'center',marginLeft:'auto'}}>
            {clipsArr.length > 0 && (
              <div style={{position:'relative'}}>
                <button className={'rd-btn sm'+(showCull?' active':'')} onClick={()=>setShowCull(v=>!v)} style={{background:showCull?'rgba(168,85,247,.18)':'rgba(255,255,255,.06)',border:'1px solid',borderColor:showCull?'var(--acc)':'var(--hair)',color:showCull?'var(--acc)':'var(--fg-2)'}}>
                  <Icon name="sparkles" size={13}/>Cull clips
                </button>
                {showCull && <CullPanel clips={clips} onDone={()=>setShowCull(false)}/>}
              </div>
            )}
            {pending > 0 && <ClearQueueButton pending={pending}/>}
            {channels.length>1 && <select className="rd-select" value={effChan} onChange={e=>setChanFilter(e.target.value)} title="Filter by streamer" style={{padding:'6px 10px',fontSize:12,fontWeight:600}}>
              <option value="all">All streamers</option>
              {channels.map(c=><option key={c} value={c}>{c}</option>)}
            </select>}
            <div className="rd-filters">
              {['all','pending','approved'].map(f=><button key={f} className={'rd-filter'+(filter===f?' active':'')} onClick={()=>setFilter(f)}>{f[0].toUpperCase()+f.slice(1)}</button>)}
            </div>
            <div className="rd-filters">
              <button className={'rd-filter'+(sortBy==='newest'?' active':'')} onClick={()=>setSortBy('newest')} title="Sort by date added"><Icon name="clock" size={12}/> Newest</button>
              <button className={'rd-filter'+(sortBy==='virality'?' active':'')} onClick={()=>setSortBy('virality')} title="Sort by virality score"><Icon name="trending" size={12}/> Top Virality</button>
            </div>
          </div>
        </div>
        <div className="rd-grid">
          {shown.length===0
            ? <div className="rd-grid-empty"><div className="ic"><Icon name="film" size={42}/></div><div className="big">Waiting for clips</div><div>Add a channel on the Live Streams tab — clips appear here the moment a highlight fires.</div>
                {/* New here? This is the one screen a first-time user reliably
                    lands on with nothing to do, so it is where the walkthrough
                    belongs. New tab: the dashboard is a long-lived SPA holding
                    a live socket, and navigating away throws that state out. */}
                <a href="/tutorial" target="_blank" rel="noopener" className="rd-emptylink">Read the walkthrough →</a></div>
            : shown.map(c=><RdClip key={c.id} clip={c} onApprove={onApprove} onReject={onReject} onOpen={onOpen}/>)}
        </div>
      </section>
    </div>
  );
}

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
        <div className="rd-grid-empty" style={{padding:'70px 0'}}>
          <div className="ic"><Icon name="radio" size={42}/></div>
          <div className="big">No streams monitored yet</div>
          <div>Search a streamer on the left to start watching for highlights.</div>
        </div>
      </div>
    </div>
  );
  const p = profiles[active.channel]||{};
  const sd = scores[active.channel]||{score:0,breakdown:{}};
  const hist = histories[active.channel]||[sd.score];
  const recent = Object.values(clips).filter(c=>c.channel===active.channel).sort((a,b)=>(b.created_at||0)-(a.created_at||0)).slice(0,4);
  const WK=[['CHAT_VELOCITY','Chat velocity'],['KEYWORD','Keyword'],['SENTIMENT','Sentiment'],['AUDIO_SPIKE','Audio spike'],['VIEWER_SPIKE','Viewer spike'],['SILENCE_BURST','Silence burst']];
  const sw = p.signal_weights||{};
  const statusColor = active.status==='live'?'var(--live)':active.status==='reconnecting'?'var(--pending)':'var(--fg-2)';
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
              <span style={{color:statusColor,fontWeight:600}}>● {active.status}</span></div>
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
          <div className="rd-metric glass"><div className="k">Avg velocity</div><div className="v">{p.avg_velocity>0?p.avg_velocity.toFixed(1):'—'}<span style={{fontSize:11,color:'var(--fg-3)'}}> m/s</span></div></div>
          <div className="rd-metric glass"><div className="k">Approval rate</div>
            <div className="v" style={{color:p.approval_rate>=.7?'var(--live)':p.approval_rate>=.4?'var(--pending)':'var(--danger)'}}>
              {p.total_clips?Math.round(p.approval_rate*100)+'%':'—'}
            </div>
          </div>
          <div className="rd-metric glass"><div className="k">Total clips</div><div className="v">{p.total_clips||0}</div></div>
        </div>
        {Object.keys(sw).length>0 && <div className="rd-card2 glass">
          <h3 style={{fontSize:14,fontWeight:700,marginBottom:16,display:'flex',alignItems:'center',gap:9}}><Icon name="sliders" size={15} style={{color:'var(--acc)'}}/>Learned signal weights</h3>
          {WK.filter(([k])=>sw[k]!=null).map(([k,lbl])=>{const v=sw[k]||1;const pct=Math.min(100,(v/2.5)*100);return <div className="rd-weight" key={k}>
            <span className="wl">{lbl}</span>
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
function LibraryScreen({ clips, onOpen, onDelete, onGoReview }) {
  const [chanFilter, setChanFilter] = useState('all');
  const all = Object.values(clips);
  const approved = all.filter(c=>c.status==='approved');
  // Filterable streamers derive from the clips themselves — a newly-approved
  // streamer is selectable the moment their first clip lands over the WS.
  const channels = [...new Set(approved.map(c=>c.channel).filter(Boolean))].sort();
  const effChan = channels.includes(chanFilter) ? chanFilter : 'all';
  // Newest APPROVAL first, not newest capture. The library is the record of
  // what you decided to keep, so approving a clip puts it at the top — even if
  // it was captured days ago and has been sitting in the review queue since.
  // Sorting by created_at meant a clip you just kept could appear pages down,
  // which read as "my approval did nothing".
  //
  // approved_at is only set from the moment that field shipped; clips approved
  // before it fall back to created_at. That is deliberate rather than a
  // migration: their relative order is unchanged, and every new approval
  // carries a now-timestamp so it sorts above all of them.
  const at = c => c.approved_at || c.created_at || 0;
  const clipsArr = approved
    .filter(c=>effChan==='all'||c.channel===effChan)
    .sort((a,b)=>at(b)-at(a));
  // Pending clips are not listed here, but their existence is worth surfacing —
  // otherwise hiding them reads as "my clips vanished" rather than "they are one
  // tab over waiting on you".
  const pendingCount = all.filter(c=>c.status==='pending').length;
  return (
    <div className="rd-scroll">
      <div className="rd-section-title">
        <h2>Clip library</h2>
        <span className="cnt">{approved.length} approved</span>
        <div style={{marginLeft:'auto',display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
          {pendingCount>0 && <button className="rd-btn sm" onClick={()=>onGoReview&&onGoReview()}
            title="Undecided clips live in Clip Review"
            style={{background:'rgba(250,204,21,.12)',color:'var(--pending)',border:'1px solid rgba(250,204,21,.25)'}}>
            <Icon name="grid" size={13}/>{pendingCount} waiting in Clip Review
          </button>}
          {channels.length>1 && <select className="rd-select" value={effChan} onChange={e=>setChanFilter(e.target.value)} title="Filter by streamer" style={{padding:'6px 10px',fontSize:12,fontWeight:600}}>
            <option value="all">All streamers</option>
            {channels.map(c=><option key={c} value={c}>{c}</option>)}
          </select>}
        </div>
      </div>
      {clipsArr.length===0
        ? <div className="rd-grid-empty"><div className="ic"><Icon name="film" size={42}/></div><div className="big">Nothing here yet</div><div>{pendingCount>0?'Approve a clip in Clip Review and it is archived here.':'Clips you approve are archived in the library.'}</div></div>
        : <div className="rd-grid" style={{overflow:'visible',paddingRight:0}}>
            {clipsArr.map(c=><RdClip key={c.id} clip={c} onOpen={onOpen} onDelete={onDelete} libraryMode/>)}
          </div>}
    </div>
  );
}

function SettingsScreen({ streams }) {
  const [stats, setStats] = useState(null);
  useEffect(()=>{
    const loadStats = ()=> fetch('/stats').then(r=>r.json()).then(setStats).catch(()=>{});
    loadStats();
    // Usage stats are derived from clips, so refresh them whenever a clip is
    // created/approved/rejected (forwarded over the in-page hz_ws channel). Keeps
    // approval rate, totals and "clips this week" live without a page refresh.
    const onWs = e=>{ try{ const m=JSON.parse(e.detail);
      if(['clip_ready','clip_updated','clip_removed'].includes(m.event)) loadStats();
    }catch{} };
    window.addEventListener('hz_ws', onWs);
    // Re-pull stats on WS reconnect/deploy so they self-heal instead of staling.
    window.addEventListener('hz_refetch', loadStats);
    return ()=>{ window.removeEventListener('hz_ws', onWs); window.removeEventListener('hz_refetch', loadStats); };
  },[]);
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
        <div className="rd-section-title"><h2>Settings</h2></div>
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
        {stats&&stats.length>0 && <div className="rd-card glass">
          <h3><span className="si"><Icon name="trending" size={15}/></span>Usage stats</h3>
          <div className="desc">Clip performance per channel, all time.</div>
          {stats.map(r=><div key={r.channel} style={{borderBottom:'1px solid var(--hair)',paddingBottom:16,marginBottom:16}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
              <span style={{fontSize:15,fontWeight:700,color:'var(--acc)'}}>{r.channel}</span>
              <span style={{fontSize:12,color:'var(--fg-3)'}}>{r.clips_this_week} clips this week</span>
            </div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:10}}>
              {[['Total clips',r.total_clips,'var(--fg)'],['Approval rate',r.approval_rate+'%',r.approval_rate>=60?'var(--live)':r.approval_rate>=30?'var(--pending)':'var(--danger)'],
                ['Avg score',r.avg_score,'var(--fg)'],['Avg virality',r.avg_virality,'var(--acc)'],['Pending',r.pending,'var(--pending)'],['Top signal',r.top_signal,'var(--fg)']
              ].map(([k,v,c])=><div key={k} style={{background:'rgba(255,255,255,.03)',borderRadius:12,padding:'12px 14px'}}>
                <div style={{fontSize:11,color:'var(--fg-3)',marginBottom:4}}>{k}</div>
                <div style={{fontSize:18,fontWeight:700,color:c}}>{v}</div>
              </div>)}
            </div>
          </div>)}
        </div>}
      </div>
    </div>
  );
}

const NAV=[{id:'streams',label:'Live Streams',icon:'radio'},{id:'review',label:'Clip Review',icon:'grid'},{id:'library',label:'Clip Library',icon:'film'},{id:'vod',label:'VOD Scanner',icon:'video'},{id:'uploads',label:'Clip Editor',icon:'upload',adminOnly:true},{id:'schedule',label:'Scheduler',icon:'clock',adminOnly:true},{id:'training',label:'Training',icon:'sparkles',labelerOnly:true},{id:'landing',label:'Landing Page',icon:'trending',adminOnly:true},{id:'settings',label:'Settings',icon:'cog'},{id:'account',label:'Account',icon:'user'},{id:'feedback',label:'Feedback',icon:'chat'}];
// Tabs that are closed off while Kick clipping is under construction. Used by
// BOTH the route dispatch and the nav, so a blocked tab is greyed out and
// unclickable rather than looking live and then dead-ending. Account, Feedback
// and the admin/labeler tools are global and stay open; the platform switch
// and Sign out always stay live so Kick is never a trap.
const KICK_BLOCKED=['review','streams','library','vod','uploads','schedule','settings'];
const HEAD={streams:['Live Streams','Add channels and watch them score in real time'],review:['Clip Review','Approve or reject the highlights the bot caught'],library:['Clip Library','Every clip you have approved'],vod:['VOD Scanner','Find highlight moments in finished streams'],uploads:['Clip Editor','Bring clips in and cut them for vertical'],schedule:['Scheduler','Everything you have exported, ready to post'],training:['Training Studio','Blind-score clips to calibrate the formula'],landing:['Landing Page','Curate the example clips visitors see'],settings:['Settings','Tune triggers, storage & workflow'],account:['Account','Billing, profile & platforms'],feedback:['Feedback','Questions, bugs & suggestions']};

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
  const [idx, setIdx] = useState(0);
  const [vals, setVals] = useState({...FRESH});
  const [busy, setBusy] = useState(false);
  const [playerTry, setPlayerTry] = useState(0);
  const loadStats = ()=>fetch('/training/stats').then(r=>r.ok?r.json():null).then(setStats).catch(()=>{});
  const load = ()=>{
    fetch('/training/queue').then(r=>r.ok?r.json():[]).then(q=>{setQueue(q);setIdx(0);}).catch(()=>setQueue([]));
    loadStats();
  };
  useEffect(()=>{
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
      }
    } catch {} };
    window.addEventListener('hz_refetch', load);
    window.addEventListener('hz_ws', onWs);
    return ()=>{ window.removeEventListener('hz_refetch', load); window.removeEventListener('hz_ws', onWs); };
  },[]);
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
        if(verdict==='approve'||verdict==='reject'){
          await fetch(`/clips/${cur.id}/${verdict}`,{method:'POST'}).catch(()=>{});
        }
        setQueue(q=>q.filter(c=>c.id!==cur.id)); setIdx(0); setVals({...FRESH}); loadStats();
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
          <h2>Training Studio</h2>
          <span className="cnt">{queue===null?'Loading…':queue.length+' clip'+(queue.length===1?'':'s')+' awaiting your score'}{stats?` · ${stats.total} scored by the team`:''}</span>
        </div>
        <div className="rd-card glass" style={{marginBottom:14,padding:'12px 18px',fontSize:12.5,color:'var(--fg-2)',display:'flex',gap:10,alignItems:'center',flexWrap:'wrap'}}>
          <Icon name="sparkles" size={15}/>
          <span style={{flex:1,minWidth:220}}><b style={{color:'var(--fg)'}}>You're scoring blind.</b> The bot's numbers are hidden on purpose — rate what YOU saw, 1 (nothing) to 10 (insane). Your scores get paired with the bot's hidden read to recalibrate the formula.</span>
          {stats && <span style={{display:'flex',gap:6,alignItems:'center',flexWrap:'wrap'}}>
            <span className="rd-tag" style={{background:'rgba(168,85,247,.16)',color:'var(--acc)',fontWeight:800,fontSize:13}}>{stats.total} trained</span>
            {Object.entries(stats.by_labeler||{}).sort((a,b)=>b[1]-a[1]).map(([name,n])=>
              <span key={name} className="rd-tag">{name}: {n}</span>)}
          </span>}
        </div>
        {!cur
          ? <div className="rd-card glass" style={{textAlign:'center',padding:'42px 28px'}}>
              <div style={{marginBottom:12,color:'var(--acc)'}}><Icon name="check" size={36}/></div>
              <h3 style={{fontSize:17,justifyContent:'center'}}>Queue clear</h3>
              <div className="desc">New clips land here automatically as the bot captures them.</div>
            </div>
          : <div className="rd-card glass">
              <div style={{display:'flex',justifyContent:'space-between',alignItems:'baseline',flexWrap:'wrap',gap:8,marginBottom:12}}>
                <h3 style={{margin:0}}>{cur.channel}<span style={{fontSize:12,color:'var(--fg-3)',fontWeight:500,marginLeft:10}}>{cur.game||''}</span></h3>
                <span style={{fontSize:12,color:'var(--fg-3)'}}>{new Date((cur.created_at||0)*1000).toLocaleString()}</span>
              </div>
              {embedSrc
                ? <>
                    <div style={{position:'relative',paddingBottom:'56.25%',borderRadius:12,overflow:'hidden',background:'#000'}}>
                      <iframe key={playerTry} src={embedSrc+'&_r='+playerTry} style={{position:'absolute',inset:0,width:'100%',height:'100%',border:0}} allowFullScreen scrolling="no" title="Clip"/>
                    </div>
                    <div style={{display:'flex',alignItems:'center',gap:12,marginTop:8,fontSize:11.5,color:'var(--fg-3)'}}>
                      <span>Player showing an error?</span>
                      <button className="rd-btn sm" onClick={()=>setPlayerTry(t=>t+1)}>Reload player</button>
                      {cur.twitch_url && <a href={cur.twitch_url} target="_blank" rel="noopener" style={{color:'#a5b4fc',textDecoration:'none',fontWeight:600}}>Watch on Twitch ↗</a>}
                    </div>
                  </>
                : <a href={cur.twitch_url||'#'} target="_blank" rel="noopener" className="rd-btn sm" style={{textDecoration:'none'}}>Watch on Twitch ↗</a>}
              <div style={{marginTop:18,display:'flex',flexDirection:'column',gap:14}}>
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
              <div style={{display:'flex',gap:10,marginTop:20,flexWrap:'wrap',alignItems:'center'}}>
                <button className="rd-btn live" disabled={busy} onClick={()=>submit('approve')} style={{opacity:busy?0.6:1}}>
                  <Icon name="check" size={14}/>{busy?'Saving…':'Score & Approve'}
                </button>
                <button className="rd-btn danger" disabled={busy} onClick={()=>submit('reject')} style={{opacity:busy?0.6:1}}>
                  <Icon name="x" size={14}/>Score & Reject
                </button>
                <button className="rd-btn sm" disabled={busy} onClick={()=>submit(null)} title="Save the sliders and leave the clip pending for later review">Score only</button>
                {queue.length>1&&<button className="rd-btn sm" onClick={skip}>Skip</button>}
              </div>
            </div>}
      </div>
    </div>
  );
}

function LandingScreen({ clips, featured, onToggle, onMove, onGrab, myUrls }) {
  // Admin-only curation of the public landing page's example clips. Featured
  // entries come from /landing/showcase (the same payload visitors get), so
  // what's listed here is literally what the site is showing.
  const [q, setQ] = useState('');
  const max = 8;
  const featuredIds = featured.map(f=>f.id);
  const eligible = Object.values(clips)
    .filter(c=>c.status==='approved' && c.platform==='twitch' && c.twitch_url && !featuredIds.includes(c.id))
    .filter(c=>!q.trim() || (c.channel||'').toLowerCase().includes(q.trim().toLowerCase()))
    .sort((a,b)=>(b.virality_score||0)-(a.virality_score||0));
  const full = featured.length >= max;
  const thumb = (c)=> c.thumbnail_url
    ? <img src={c.thumbnail_url} alt="" style={{width:96,height:54,objectFit:'cover',borderRadius:8,flexShrink:0,background:'#15111f'}}/>
    : <div style={{width:96,height:54,borderRadius:8,flexShrink:0,background:'linear-gradient(135deg,#2a1840,#3a1a4d)'}}/>;
  const row = (c, right)=>(
    <div key={c.id} style={{display:'flex',alignItems:'center',gap:12,padding:'10px 12px',borderRadius:12,
      background:'rgba(255,255,255,.03)',border:'1px solid var(--hair)',minWidth:0}}>
      {thumb(c)}
      <div style={{minWidth:0,flex:1}}>
        <div style={{fontWeight:700,fontSize:13.5,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
          {c.clip_title||c.stream_title||'Clip'}</div>
        <div style={{fontSize:11.5,color:'var(--fg-3)',marginTop:2}}>
          {c.channel}{c.game?' · '+c.game:''} · {Math.round(c.score||c.trigger_score||0)}% trigger</div>
      </div>
      <div style={{display:'flex',gap:6,flexShrink:0}}>{right}</div>
    </div>
  );
  return (
    <div className="rd-scroll">
      <div className="rd-settings">
        <div className="rd-section-title">
          <h2>On the landing page</h2>
          <span className="cnt">{featured.length} of {max} slots used</span>
        </div>
        <div className="rd-card glass" style={{marginBottom:14,padding:'12px 18px',fontSize:12.5,color:'var(--fg-2)',
          display:'flex',gap:10,alignItems:'center',flexWrap:'wrap'}}>
          <Icon name="trending" size={15}/>
          <span style={{flex:1,minWidth:220}}>These are the example clips visitors see at
            highlightz.app. Order here is the order they appear. <b>Grab</b> copies
            one into your own Clip Library so the team can trade clips — it links
            to the same Twitch clip, nothing is re-hosted.</span>
          <a href="/" target="_blank" rel="noopener" className="rd-btn sm" style={{textDecoration:'none'}}>View live page ↗</a>
        </div>
        {featured.length===0
          ? <div className="rd-card glass" style={{textAlign:'center',padding:'34px 24px',marginBottom:22}}>
              <div className="desc">No clips featured yet — add a few from the list below and the
                examples section appears on the landing page.</div>
            </div>
          : <div style={{display:'flex',flexDirection:'column',gap:8,marginBottom:24}}>
              {featured.map((c,i)=>{
                // Already in your library? Say so rather than offering a
                // button that errors — the server refuses a duplicate, and a
                // dead button is worse than no button.
                const mine = myUrls.has(c.twitch_url);
                return row(c,<>
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
        <div style={{margin:'10px 0 12px'}}>
          <input className="rd-input" placeholder="filter by streamer" value={q} onChange={e=>setQ(e.target.value)}
            style={{maxWidth:280}}/>
        </div>
        {full && <div className="rd-card glass" style={{padding:'10px 16px',marginBottom:12,fontSize:12.5,color:'#ffc25c'}}>
          All {max} slots are full — remove one above to add another.</div>}
        {eligible.length===0
          ? <div className="rd-card glass" style={{textAlign:'center',padding:'30px 24px'}}>
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
  const hasKick    = !!(me.kick_slug);
  const signedInWith = hasKick && !hasTwitch ? 'Kick' : 'Twitch';

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
        <div className="rd-section-title"><h2>Account</h2></div>

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
              <div className="fd">{me.plan_limits ? `${me.plan_limits.max_streams} monitored stream${me.plan_limits.max_streams===1?'':'s'} · ${me.plan_limits.max_pending} pending clips · VOD scanner ${me.plan_limits.vod?'included':'not included'}` : ''}</div>
            </div>
            <span style={{fontWeight:700,color:'var(--acc)'}}>{me.plan_label}</span>
          </div>}
          {sub==='active' && me.plan==='starter' && <div className="rd-field">
            <div><div className="fl">Upgrade to Pro</div><div className="fd">10 streams, 200 pending clips, and the VOD scanner — $25/month</div></div>
            <a href="/billing/portal" className="rd-btn grad" style={{textDecoration:'none',display:'inline-flex',gap:7,alignItems:'center'}}>
              <Icon name="zap" size={14}/>Upgrade
            </a>
          </div>}
          {isTrial && <div className="rd-field">
            <div><div className="fl">Free trial active</div><div className="fd">Enjoy full access while it lasts — subscribe to keep clipping after it ends</div></div>
            <a href="/billing/checkout" className="rd-btn grad" style={{textDecoration:'none',display:'inline-flex',gap:7,alignItems:'center'}}>
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
                Pro is $25 for 10 streams, 200 pending, the VOD scanner and the
                Clip Editor.
              </div></div>
            <a href="/billing/paywall" className="rd-btn grad" style={{textDecoration:'none',display:'inline-flex',gap:7,alignItems:'center'}}>
              <Icon name="zap" size={14}/>See plans
            </a>
          </div>}
          {!isSubscribed && <div className="fd" style={{marginTop:12,fontSize:12,color:'#9c9caa'}}>Have a promo code? Enter it at checkout for 50% off your first month.</div>}
        </div>

        {/* Profile & Connected Platforms */}
        <div className="rd-card glass">
          <h3><span className="si"><Icon name="user" size={15}/></span>Profile &amp; Platforms</h3>
          <div className="desc">Your account and connected streaming platforms.</div>

          {/* Avatar + display name + sign out */}
          <div style={{display:'flex',alignItems:'center',gap:14,padding:'12px 0 16px',borderBottom:'1px solid rgba(255,255,255,.07)'}}>
            {me.avatar_url
              ? <img src={me.avatar_url} alt={me.username} style={{width:48,height:48,borderRadius:'50%',objectFit:'cover',flexShrink:0}}/>
              : <span style={{width:48,height:48,borderRadius:'50%',background:'var(--grad)',display:'grid',placeItems:'center',fontWeight:700,color:'#14021c',fontSize:18,flexShrink:0}}>{(me.username||'?')[0].toUpperCase()}</span>}
            <div style={{flex:1,minWidth:0}}>
              <div style={{fontWeight:700,fontSize:15,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{me.username||'—'}</div>
              <div style={{fontSize:11,color:'var(--fg-3)',marginTop:2}}>Signed in with {signedInWith}</div>
            </div>
            <button className="rd-btn sm danger" style={{flexShrink:0}}
              onClick={()=>fetch('/logout',{method:'POST'}).then(()=>{location.href='/login';})}>
              Sign out
            </button>
          </div>

          {/* Twitch row */}
          <div style={{display:'flex',alignItems:'center',gap:12,padding:'14px 0',borderBottom:'1px solid rgba(255,255,255,.07)'}}>
            <span style={{width:32,height:32,borderRadius:8,background:'rgba(145,71,255,.18)',display:'grid',placeItems:'center',flexShrink:0}}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="#9147ff"><path d="M11.571 4.714h1.715v5.143H11.57zm4.715 0H18v5.143h-1.714zM6 0L1.714 4.286v15.428h5.143V24l4.286-4.286h3.428L22.286 12V0zm14.571 11.143l-3.428 3.428h-3.429l-3 3v-3H6.857V1.714h13.714z"/></svg>
            </span>
            <div style={{flex:1,minWidth:0}}>
              <div style={{fontWeight:600,fontSize:13}}>Twitch</div>
              {hasTwitch
                ? <div style={{fontSize:11,color:'var(--fg-3)',marginTop:1}}>@{me.twitch_login}</div>
                : <div style={{fontSize:11,color:'var(--fg-3)',marginTop:1}}>Not connected</div>}
            </div>
            {hasTwitch
              ? <span style={{fontSize:12,color:'#9147ff',fontWeight:600,flexShrink:0}}>✓ Connected</span>
              : <a href="/auth/twitch" className="rd-btn sm" style={{textDecoration:'none',flexShrink:0}}>Connect</a>}
          </div>

          {/* Kick row */}
          <div style={{display:'flex',alignItems:'center',gap:12,padding:'14px 0 4px'}}>
            <span style={{width:32,height:32,borderRadius:8,background:'rgba(83,252,24,.12)',display:'grid',placeItems:'center',flexShrink:0}}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="#53fc18"><path d="M2 2h4v8l6-8h5l-7 9 7 9h-5l-6-8v8H2z"/></svg>
            </span>
            <div style={{flex:1,minWidth:0}}>
              <div style={{fontWeight:600,fontSize:13}}>Kick</div>
              {hasKick
                ? <div style={{fontSize:11,color:'var(--fg-3)',marginTop:1}}>@{me.kick_slug}</div>
                : <div style={{fontSize:11,color:'var(--fg-3)',marginTop:1}}>Not connected</div>}
            </div>
            {hasKick
              ? <span style={{fontSize:12,color:'#53fc18',fontWeight:600,flexShrink:0}}>✓ Connected</span>
              : <a href="/auth/kick" className="rd-btn sm" style={{textDecoration:'none',background:'#53fc18',color:'#0a0a0a',fontWeight:700,border:'none',flexShrink:0}}>Connect</a>}
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
          {delErr && <div style={{fontSize:12,color:'var(--danger)',marginBottom:10}}>{delErr}</div>}
          {!confirmDel
            ? <button className="rd-btn danger" onClick={()=>setConfirmDel(true)}>Delete my account</button>
            : <div style={{display:'flex',flexDirection:'column',gap:10}}>
                <div style={{fontSize:13,color:'var(--danger)',fontWeight:600}}>This will delete all your clips, streams, and account data. Continue?</div>
                <div style={{display:'flex',gap:10,flexWrap:'wrap'}}>
                  <button className="rd-btn danger" onClick={deleteAccount} disabled={deleting} style={{flex:'1 1 auto'}}>
                    {deleting ? 'Deleting…' : 'Yes, delete everything'}
                  </button>
                  <button className="rd-btn" onClick={()=>{setConfirmDel(false);setDelErr('');}} style={{flex:'0 0 auto'}}>Cancel</button>
                </div>
              </div>}
        </div>

        <div style={{textAlign:'center',fontSize:11,color:'var(--fg-3)',paddingBottom:24}}>
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
    return ()=>window.removeEventListener('hz_fb_reply', onReply);
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
        <div className="rd-section-title"><h2>Feedback</h2></div>
        <div className="rd-card glass">
          <h3><span className="si"><Icon name="chat" size={15}/></span>Send feedback</h3>
          <div className="desc">Questions, suggestions, bug reports — we read everything.</div>
          {sent ? (
            <div style={{padding:'24px 0',textAlign:'center'}}>
              <div style={{fontSize:32,marginBottom:12}}>✓</div>
              <div style={{fontWeight:700,marginBottom:8}}>Thanks for your feedback!</div>
              <div style={{fontSize:13,color:'var(--fg-3)',marginBottom:20}}>We'll review it shortly.</div>
              <button className="rd-btn" onClick={()=>{setSent(false);loadThreads();}}>Send another</button>
            </div>
          ) : (
            <>
              <div style={{marginBottom:14}}>
                <div style={{fontSize:11,fontWeight:600,color:'var(--fg-3)',textTransform:'uppercase',letterSpacing:'.04em',marginBottom:8}}>Category</div>
                <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
                  {CATEGORIES.map(c=>(
                    <button key={c} onClick={()=>setCategory(c)} style={{
                      padding:'6px 14px',borderRadius:99,fontSize:12,fontWeight:600,cursor:'pointer',border:'1px solid',transition:'.15s',
                      background: category===c ? 'rgba(168,85,247,.2)' : 'rgba(255,255,255,.05)',
                      borderColor: category===c ? 'rgba(168,85,247,.5)' : 'rgba(255,255,255,.09)',
                      color: category===c ? '#c79bff' : 'var(--fg-3)',
                    }}>{c}</button>
                  ))}
                </div>
              </div>
              <div style={{marginBottom:14}}>
                <div style={{fontSize:11,fontWeight:600,color:'var(--fg-3)',textTransform:'uppercase',letterSpacing:'.04em',marginBottom:8}}>Message</div>
                <textarea
                  value={message}
                  onChange={e=>setMessage(e.target.value)}
                  placeholder="Tell us what's on your mind…"
                  maxLength={2000}
                  rows={6}
                  style={{width:'100%',background:'rgba(255,255,255,.04)',border:'1px solid rgba(255,255,255,.09)',borderRadius:12,color:'var(--fg)',padding:'12px 14px',fontSize:14,resize:'vertical',outline:'none',fontFamily:'inherit',lineHeight:1.6}}
                />
                <div style={{textAlign:'right',fontSize:11,color:'var(--fg-3)',marginTop:4}}>{message.length}/2000</div>
              </div>
              {err && <div style={{color:'var(--danger)',fontSize:13,marginBottom:12,padding:'8px 12px',background:'rgba(255,90,120,.08)',borderRadius:9,border:'1px solid rgba(255,90,120,.2)'}}>{err}</div>}
              <button className="rd-btn grad" onClick={submit} disabled={sending} style={{opacity:sending?.6:1}}>
                <Icon name="chat" size={14}/>{sending ? 'Sending…' : 'Send feedback'}
              </button>
            </>
          )}
        </div>

        {threads.length > 0 &&
          <div className="rd-card glass" style={{marginTop:16}}>
            <h3><span className="si"><Icon name="chat" size={15}/></span>Your messages</h3>
            <div className="desc">Anything you have sent us, and our replies.</div>
            <div style={{display:'flex',flexDirection:'column',gap:14,marginTop:14}}>
              {threads.map(t=>(
                <div key={t.id} style={{border:'1px solid var(--hair)',borderRadius:10,padding:'12px 14px',
                    background:t.reply_unread?'rgba(168,85,247,.07)':'transparent'}}>
                  <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:6}}>
                    <span style={{fontFamily:'ui-monospace,monospace',fontSize:10.5,letterSpacing:'.12em',
                      textTransform:'uppercase',color:'var(--fg-3)'}}>{t.category}</span>
                    <span style={{fontSize:11,color:'var(--fg-3)'}}>{fmtTime(t.created_at)}</span>
                    {t.reply_unread && <span className="navbadge" style={{position:'static'}}>new</span>}
                  </div>
                  <div style={{fontSize:13.5,lineHeight:1.55,whiteSpace:'pre-wrap'}}>{t.message}</div>
                  {(t.replies||[]).map((r,ri)=>(
                    <div key={ri} style={{marginTop:10,paddingLeft:12,
                        borderLeft:'2px solid '+(r.from_admin===false?'var(--hair-2)':'var(--acc)')}}>
                      <div style={{fontSize:11,fontWeight:700,marginBottom:3,
                          color:r.from_admin===false?'var(--fg-3)':'var(--acc)'}}>
                        {r.from_admin===false?'You':'Highlightz'}</div>
                      <div style={{fontSize:13.5,lineHeight:1.55,whiteSpace:'pre-wrap'}}>{r.message}</div>
                      <div style={{fontSize:11,color:'var(--fg-3)',marginTop:3}}>{fmtTime(r.at)}</div>
                    </div>
                  ))}
                  {replyTo===t.id ? (
                    <div style={{marginTop:12}}>
                      <textarea className="rd-input" rows={3} value={replyMsg} autoFocus
                        onChange={e=>setReplyMsg(e.target.value)}
                        placeholder="Write your reply…"
                        style={{width:'100%',resize:'vertical',fontFamily:'inherit',fontSize:13.5}}/>
                      {replyErr && <div style={{fontSize:12,color:'var(--bad)',marginTop:6}}>{replyErr}</div>}
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
            <div style={{display:'flex',alignItems:'center',gap:10,flexWrap:'wrap',marginBottom:14}}>
              <span style={{fontSize:12.5,color:'var(--fg-3)'}}>
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
              <div className="rd-grid-empty" style={{padding:'34px 0'}}>
                <div className="ic"><Icon name="film" size={38}/></div>
                <div className="big">No clips on your channel yet</div>
                <div>Clips you or your viewers create on Twitch will show up here.</div>
              </div>}

            {clips.length>0 &&
              <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(240px,1fr))',gap:14}}>
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
              <button className="rd-btn" style={{marginTop:14}} disabled={loading}
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
              allowFullScreen title={play.title||'Clip'}/>
          </div>
          <div className="ed-note" style={{marginTop:10}}>
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
const REC_TYPES = [
  'video/mp4;codecs=avc1.42E01E,mp4a.40.2',
  'video/mp4;codecs=avc1.42E01E',
  'video/mp4',
  'video/webm;codecs=h264',
  'video/webm;codecs=vp9',
  'video/webm',
];

function pickRecorderType() {
  if (typeof MediaRecorder === 'undefined') return '';
  return REC_TYPES.find(t => { try { return MediaRecorder.isTypeSupported(t); } catch { return false; } }) || '';
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

function activeCaption(segs, t) {
  if (!segs || !segs.length) return '';
  // Linear scan: a clip is seconds long and has a handful of segments, so this
  // is cheaper than the bookkeeping a binary search would need per frame.
  let held = '';
  for (const s of segs) {
    if (t >= s.start && t <= s.end) return s.text || '';
    // Segments are in order, so the last one that qualifies here is the most
    // recent cue — that is the one worth holding through a short gap.
    if (s.end < t && t - s.end <= CAP_HOLD_S) held = s.text || '';
  }
  return held;
}

/* Canvas filter support, probed once. Blur fill silently becomes a plain crop
   without it — drawing the background unblurred would put a huge duplicate of
   the video behind itself, which looks broken rather than degraded. */
let _CTX_FILTER = null;
function ctxCanFilter(ctx) {
  if (_CTX_FILTER === null) {
    try { ctx.filter = 'blur(2px)'; _CTX_FILTER = ctx.filter !== 'none'; ctx.filter = 'none'; }
    catch { _CTX_FILTER = false; }
  }
  return _CTX_FILTER;
}

function paintFrame(ctx, video, o) {
  const { w, h, zoom, offX, offY, text, textSize, textPos, caption } = o;
  const fill = o.fill || 'crop';
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, w, h);

  const vw = video.videoWidth || 16, vh = video.videoHeight || 9;

  if (fill === 'blur' && ctxCanFilter(ctx)) {
    // Contain the video and put a blurred, over-scaled copy behind it. Nothing
    // is cropped off the sides, which is the point — a 16:9 clip forced into
    // 9:16 by cover loses most of the frame.
    ctx.save();
    ctx.filter = 'blur(28px)';
    const bs = Math.max(w / vw, h / vh) * 1.25;   // overscan so blurred edges
    ctx.drawImage(video, (w - vw * bs) / 2, (h - vh * bs) / 2, vw * bs, vh * bs);
    ctx.restore();
    ctx.fillStyle = 'rgba(0,0,0,.3)';             // hold the foreground forward
    ctx.fillRect(0, 0, w, h);
    const cs = Math.min(w / vw, h / vh) * zoom;
    const cw = vw * cs, ch = vh * cs;
    ctx.drawImage(video, (w - cw) / 2 + offX * w, (h - ch) / 2 + offY * h, cw, ch);
  } else {
    // Cover: fill the frame, crop the overflow. Letterboxing a vertical export
    // would defeat the point of reframing for a phone screen.
    const scale = Math.max(w / vw, h / vh) * zoom;
    const dw = vw * scale, dh = vh * scale;
    ctx.drawImage(video, (w - dw) / 2 + offX * w, (h - dh) / 2 + offY * h, dw, dh);
  }

  // Auto-caption first, so a manual title drawn at the same spot sits on top
  // rather than being hidden behind it.
  if (caption) {
    const fs = Math.round(h * (o.capSize || 0.055));
    ctx.font = `800 ${fs}px Sora, Inter, system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    // Wrap to the frame instead of running off the edge — a long line on a
    // 9:16 export would otherwise lose both ends.
    const words = String(caption).split(' ');
    const lines = [];
    let cur = '';
    for (const wd of words) {
      const test = cur ? cur + ' ' + wd : wd;
      if (ctx.measureText(test).width > w * 0.86 && cur) { lines.push(cur); cur = wd; }
      else cur = test;
    }
    if (cur) lines.push(cur);
    const shown = lines.slice(-3);
    const capPos = o.capPos || 'bottom';
    // 0.78 keeps captions clear of the platform's own bottom chrome; 'low'
    // (0.88) is for people who want them under the action, and 'top' for when
    // the interesting part of the frame is at the bottom.
    const baseY = h * (capPos === 'top' ? 0.16 : capPos === 'middle' ? 0.5
                     : capPos === 'low' ? 0.88 : 0.78);
    shown.forEach((ln, i) => {
      const ly = baseY + (i - (shown.length - 1) / 2) * fs * 1.2;
      if (o.capHighlight) {
        // A solid plate behind the words. Reads on any background, where a
        // stroke alone can still disappear into busy gameplay.
        const tw = ctx.measureText(ln).width;
        const padX = fs * 0.34, padY = fs * 0.24;
        ctx.fillStyle = 'rgba(0,0,0,.72)';
        const rx = (w - tw) / 2 - padX, ry = ly - fs * 0.62 - padY;
        const rw = tw + padX * 2, rh = fs * 1.24 + padY * 2;
        if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(rx, ry, rw, rh, fs * 0.22); ctx.fill(); }
        else ctx.fillRect(rx, ry, rw, rh);
      } else {
        ctx.lineWidth = Math.max(2, fs * 0.2);
        ctx.strokeStyle = 'rgba(0,0,0,.9)';
        ctx.lineJoin = 'round';
        ctx.strokeText(ln, w / 2, ly);
      }
      ctx.fillStyle = '#fff';
      ctx.fillText(ln, w / 2, ly);
    });
  }

  if (text) {
    const fs = Math.round(h * textSize);
    ctx.font = `900 ${fs}px Sora, Inter, system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const y = textPos === 'top' ? h * 0.12 : textPos === 'middle' ? h * 0.5 : h * 0.88;
    // NOTE: this whole script lives inside a Python triple-quoted string, so a
    // bare backslash-n here would be parsed by PYTHON into a real newline and
    // break the JS string literal. Split on a character code instead — no
    // escape, nothing for Python to eat.
    const lines = String(text).split(String.fromCharCode(10)).slice(0, 3);
    lines.forEach((ln, i) => {
      const ly = y + (i - (lines.length - 1) / 2) * fs * 1.15;
      ctx.lineWidth = Math.max(2, fs * 0.16);
      ctx.strokeStyle = 'rgba(0,0,0,.85)';
      ctx.lineJoin = 'round';
      ctx.strokeText(ln, w / 2, ly);      // outline first, so text reads on any background
      ctx.fillStyle = '#fff';
      ctx.fillText(ln, w / 2, ly);
    });
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

function ClipEditor({ clip, onClose, onExported, captionsOn = false, platforms = [] }) {
  // captionsOn is the RELEASE flag, not a plan gate. With it false the panel is
  // hidden entirely rather than rendered as a button that 503s on every click —
  // a visible control that always fails is the Kick-tab mistake again, and this
  // one shipped to paying users while CAPTIONS_ENABLED was unset on prod.
  const [dur, setDur]       = useState(0);
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
  const [capSize, setCapSize] = useState(0.055);
  const [capPos, setCapPos]   = useState('bottom');
  const [capHi, setCapHi]     = useState(false);
  const [playing, setPlay]  = useState(false);
  const [busy, setBusy]     = useState(false);
  const [pct, setPct]       = useState(0);
  const [err, setErr]       = useState('');
  const [done, setDone]     = useState('');

  // The exported file is KEPT, not just downloaded. Handing it to the native
  // share sheet is the whole "post to TikTok/IG/YouTube" story: one tap on a
  // phone, into the real app, with no OAuth and no platform app-review. Dropping
  // the blob after download would force a re-export to share.
  const [outFile, setOutFile] = useState(null);  // {blob, ext, name}
  const [cap, setCap]         = useState('');    // caption text to carry across
  const [copied, setCopied]   = useState('');

  const [caps, setCaps]     = useState(null);   // [{start,end,text}]
  const [capOn, setCapOn]   = useState(true);
  const [capJob, setCapJob] = useState(null);   // {status,pct} while running
  const [capErr, setCapErr] = useState('');

  const videoRef = useRef(null);
  const canvRef  = useRef(null);
  const rafRef   = useRef(0);
  const cancelRef = useRef(false);
  // The playhead and the clock are driven from the animation loop, NOT from
  // render. The loop paints the canvas imperatively and changes no state, so
  // React does not re-render while the video plays — anything positioned from
  // `videoRef.current.currentTime` during render is frozen at wherever it was
  // when the last state change happened. (It was, and the bar never moved.)
  // Calling setState 60x/second instead would re-render the whole editor every
  // frame, which is the wrong trade for two numbers.
  const headRef  = useRef(null);
  const clockRef = useRef(null);

  const OUT_H = 1280;
  const aspect = (RATIOS.find(r => r[0] === ratio) || RATIOS[0])[1];
  const outW = Math.round(OUT_H * aspect / 2) * 2;   // even dims: H.264 requires it
  const outH = OUT_H;

  const opts = () => ({ w: outW, h: outH, zoom, offX, offY, text, textSize, textPos,
    fill, capSize, capPos, capHighlight: capHi,
    caption: capOn ? activeCaption(caps, videoRef.current ? videoRef.current.currentTime : 0) : '' });

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

  // Live preview loop.
  useEffect(() => {
    const draw = () => {
      const v = videoRef.current, c = canvRef.current;
      if (v && c) {
        // Never resize mid-export: assigning canvas.width resets the surface
        // and invalidates the MediaRecorder capture track, so a shape change
        // landing during a render would truncate the file.
        if (c.width !== outW && !busy) { c.width = outW; c.height = outH; }
        paintFrame(c.getContext('2d'), v, opts());
        const t = v.currentTime;
        if (headRef.current)
          headRef.current.style.left = (dur ? Math.min(t, dur) / dur * 100 : 0) + '%';
        if (clockRef.current) {
          const s = edTime(t);
          if (clockRef.current.textContent !== s) clockRef.current.textContent = s;
        }
        if (t >= outPt && playing) { v.pause(); setPlay(false); v.currentTime = inPt; }
      }
      rafRef.current = requestAnimationFrame(draw);
    };
    rafRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(rafRef.current);
  });

  const onMeta = () => {
    const v = videoRef.current;
    if (!v) return;
    const settle = (d) => { setDur(d); setIn(0); setOut(d); v.currentTime = 0; };
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

  const seek = (t) => {
    const v = videoRef.current;
    if (v) v.currentTime = Math.max(inPt, Math.min(outPt, t));
  };

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (playing) { v.pause(); setPlay(false); }
    else { if (v.currentTime < inPt || v.currentTime >= outPt) v.currentTime = inPt; v.play(); setPlay(true); }
  };

  const trackClick = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    seek(((e.clientX - r.left) / r.width) * dur);
  };

  const download = (blob, ext) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = (clip.filename || 'clip').replace(RE_EXT, '') + `-${ratio.replace(':', 'x')}.${ext}`;
    document.body.appendChild(a); a.click(); a.remove();
    // Revoke late: revoking immediately can cancel the download in some browsers.
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  };

  // ── Export: MediaRecorder path ──
  // Records the canvas while the video plays, so it runs in real time. Used
  // when WebCodecs is missing.
  const exportRecorder = async () => {
    const v = videoRef.current, c = canvRef.current;
    const type = pickRecorderType();
    if (!type) throw new Error('This browser cannot export video. Try Chrome.');
    const stream = c.captureStream(30);
    const chunks = [];
    const rec = new MediaRecorder(stream, { mimeType: type, videoBitsPerSecond: 6e6 });
    rec.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
    const finished = new Promise(res => { rec.onstop = res; });

    // Seek to the in-point. Assigning the CURRENT time fires no 'seeked' event
    // in most browsers, so waiting unconditionally would hang forever on a
    // clip whose playhead is already parked there.
    if (Math.abs(v.currentTime - inPt) > 0.01) {
      v.currentTime = inPt;
      await new Promise(res => {
        const h = () => { v.removeEventListener('seeked', h); res(); };
        v.addEventListener('seeked', h);
        setTimeout(h, 3000);           // never block export on a missing event
      });
    }

    rec.start(200);
    // Give the recorder a beat to latch onto the track before playback starts.
    // Without it a short trim can finish before the first timeslice is emitted
    // and the export lands empty — which is exactly how this failed the first
    // time it was driven.
    await new Promise(r => setTimeout(r, 150));

    await v.play().catch(() => {});
    const span = Math.max(0.1, outPt - inPt);
    const started = Date.now();
    await new Promise(res => {
      const tick = () => {
        if (cancelRef.current) return res();
        // A minimum wall-clock floor guarantees at least one timeslice lands
        // even for a very short trim.
        const enough = Date.now() - started >= 400;
        if (enough && (v.currentTime >= outPt || v.ended)) return res();
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
    await finished;
    return { blob: new Blob(chunks, { type }), ext: type.includes('mp4') ? 'mp4' : 'webm' };
  };

  // ── Export: WebCodecs path ──
  // Seeks frame by frame and encodes directly — no real-time playback, so a
  // 30s clip finishes in seconds. Falls back to the recorder if anything in
  // the pipeline is unavailable.
  const exportWebCodecs = async () => {
    const v = videoRef.current, c = canvRef.current;
    const FPS = 30;
    const total = Math.max(1, Math.round((outPt - inPt) * FPS));
    const chunks = [];
    let cfg = null;

    const enc = new VideoEncoder({
      output: (chunk, meta) => {
        if (meta && meta.decoderConfig) cfg = meta.decoderConfig;
        const buf = new Uint8Array(chunk.byteLength);
        chunk.copyTo(buf);
        chunks.push({ data: buf, key: chunk.type === 'key', ts: chunk.timestamp, dur: chunk.duration || (1e6 / FPS) });
      },
      error: e => { throw e; },
    });

    const support = await VideoEncoder.isConfigSupported({
      codec: 'avc1.42001f', width: outW, height: outH, bitrate: 6e6, framerate: FPS,
      avc: { format: 'annexb' },
    });
    if (!support || !support.supported) throw new Error('no-h264');
    enc.configure(support.config);

    const ctx = c.getContext('2d');
    for (let i = 0; i < total; i++) {
      if (cancelRef.current) break;
      const t = inPt + i / FPS;
      v.currentTime = t;
      await new Promise(res => { const h = () => { v.removeEventListener('seeked', h); res(); }; v.addEventListener('seeked', h); });
      paintFrame(ctx, v, opts());
      const frame = new VideoFrame(c, { timestamp: Math.round((i / FPS) * 1e6), duration: Math.round(1e6 / FPS) });
      enc.encode(frame, { keyFrame: i % (FPS * 2) === 0 });
      frame.close();
      if (i % 3 === 0) setPct((i / total) * 100);
      // Let the encoder drain so memory doesn't balloon on a long clip.
      if (enc.encodeQueueSize > 20) await new Promise(r => setTimeout(r, 8));
    }
    await enc.flush();
    enc.close();
    if (!chunks.length) throw new Error('no-frames');
    return { blob: muxAnnexB(chunks, FPS), ext: 'h264', raw: true };
  };

  // Annex-B elementary stream. Playable and re-muxable, but not an MP4 — so
  // this path only ships once the muxer below is proven; see runExport.
  const muxAnnexB = (chunks) => new Blob(chunks.map(c => c.data), { type: 'video/h264' });

  const runExport = async () => {
    setErr(''); setDone(''); setBusy(true); setPct(0); cancelRef.current = false;
    const v = videoRef.current;
    const wasMuted = v.muted;
    v.muted = true;                       // exporting should not blast audio
    try {
      // MediaRecorder produces a real, playable container on every browser.
      // The WebCodecs fast path is deliberately NOT wired in yet: it yields a
      // raw H.264 elementary stream, and shipping a file the user cannot open
      // would be worse than a slower export that works.
      const { blob, ext } = await exportRecorder();
      if (cancelRef.current) { setDone(''); return; }
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
      setDone('Exported. Saving to your Scheduler…');
      try {
        const fd = new FormData();
        fd.append('file', new File([blob], name, { type: blob.type || 'video/mp4' }));
        const ur = await fetch('/uploads?source=render', { method: 'POST', body: fd });
        if (!ur.ok) throw new Error((await ur.json().catch(()=>({}))).detail || 'save failed');
        const saved = await ur.json();
        await fetch('/publish/schedule', { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ upload_id: saved.id, caption: '', platforms: [],
                                 due_at: 0, duration_s: clipSecs, ratio, fmt: ext }) });
        setDone('Exported and added to your Scheduler.');
      } catch (e) {
        // The user still HAS the file — it downloaded. Say what did and did
        // not happen rather than reporting a failed export.
        setDone('');
        setErr('Exported to your downloads, but saving it to the Scheduler failed'
               + (e && e.message ? ' (' + e.message + ')' : '') + '.');
      }
      if (onExported) onExported(blob, ext);
    } catch (e) {
      setErr(e && e.message ? e.message : 'Export failed.');
    } finally {
      v.muted = wasMuted;
      setBusy(false); setPlay(false);
    }
  };

  // Web Share API level 2 (files). On a phone this opens the OS share sheet
  // with TikTok / Instagram / YouTube in it, which is the entire feature — no
  // OAuth, no platform app review, no upload quota. It is genuinely absent on
  // most desktop browsers, so this is a capability check and NOT a browser
  // sniff, and the desktop path below is a real path rather than an apology.
  const canShareFiles = (f) => {
    try { return !!(navigator.canShare && navigator.share && navigator.canShare({ files: [f] })); }
    catch { return false; }
  };

  const shareFile = async () => {
    if (!outFile) return;
    const f = new File([outFile.blob], outFile.name,
                       { type: outFile.blob.type || 'video/mp4' });
    if (!canShareFiles(f)) return;
    try { await navigator.share({ files: [f], text: cap || '' }); }
    catch (e) {
      // AbortError just means the user backed out of the sheet. Reporting that
      // as a failure would be wrong and alarming.
      if (e && e.name !== 'AbortError') setErr('Could not open the share sheet.');
    }
  };

  const copyCap = async () => {
    try { await navigator.clipboard.writeText(cap || ''); setCopied('caption'); }
    catch { setErr('Could not copy — select the text and copy it manually.'); }
    setTimeout(()=>setCopied(''), 1800);
  };

  const clipSecs = Math.max(0, outPt - inPt);
  const shareReady = !!outFile;
  const canNativeShare = shareReady && canShareFiles(
    new File([outFile.blob], outFile.name, { type: outFile.blob.type || 'video/mp4' }));

  const recType = pickRecorderType();
  const canExport = !!recType;
  const eta = Math.max(1, Math.round(outPt - inPt));

  return (
    <div className="ed-bg" onMouseDown={e => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="ed glass">
        <div className="ed-head">
          <span style={{color:'var(--acc)'}}><Icon name="film" size={18}/></span>
          <h3>{clip.filename || 'Edit clip'}</h3>
          <button className="rd-btn sm" onClick={onClose} disabled={busy}>Close</button>
        </div>

        <div className="ed-body">
          <div>
            <div className="ed-stage"><canvas ref={canvRef}/></div>

            <video ref={videoRef} src={clip.url} onLoadedMetadata={onMeta} playsInline
              crossOrigin="anonymous" style={{display:'none'}}/>

            <div style={{display:'flex',alignItems:'center',gap:10,marginTop:12}}>
              <button className="rd-btn sm" onClick={togglePlay} disabled={busy}>
                {playing ? 'Pause' : 'Play'}
              </button>
              <span className="ed-num" style={{minWidth:0}}>
                <b ref={clockRef}>{edTime(0)}</b> · trim {edTime(inPt)} – {edTime(outPt)} ({(outPt - inPt).toFixed(1)}s)
              </span>
            </div>

            <div className="ed-track" onClick={trackClick}>
              <div className="sel" style={{left:(dur?inPt/dur*100:0)+'%',
                                           width:(dur?(outPt-inPt)/dur*100:0)+'%'}}/>
              <div className="play" ref={headRef} style={{left:0}}/>
            </div>

            <div className="ed-grp" style={{marginTop:10}}>
              <div className="ed-row">
                <span className="ed-num" style={{textAlign:'left',minWidth:34}}>Start</span>
                <input type="range" min="0" max={dur||0} step="0.05" value={inPt} disabled={busy}
                  onChange={e=>{const x=Math.min(+e.target.value,outPt-0.3);setIn(x);seek(x);}}/>
                <span className="ed-num">{edTime(inPt)}</span>
              </div>
              <div className="ed-row">
                <span className="ed-num" style={{textAlign:'left',minWidth:34}}>End</span>
                <input type="range" min="0" max={dur||0} step="0.05" value={outPt} disabled={busy}
                  onChange={e=>{const x=Math.max(+e.target.value,inPt+0.3);setOut(x);seek(x);}}/>
                <span className="ed-num">{edTime(outPt)}</span>
              </div>
            </div>
          </div>

          <div className="ed-side">
            <div className="ed-grp">
              <label>Shape</label>
              <div className="ed-seg">
                {RATIOS.map(([k,,name])=>(
                  <button key={k} className={ratio===k?'on':''} disabled={busy}
                    onClick={()=>setRatio(k)}>{name}<br/>{k}</button>
                ))}
              </div>
            </div>

            <div className="ed-grp">
              <label>Fill</label>
              <div className="ed-seg">
                <button className={fill==='crop'?'on':''} disabled={busy}
                  onClick={()=>setFill('crop')}>Crop<br/>fills the frame</button>
                <button className={fill==='blur'?'on':''} disabled={busy}
                  onClick={()=>setFill('blur')}>Blur<br/>keeps it all</button>
              </div>
              <div className="ed-note">
                Crop cuts the sides off to fill a vertical frame. Blur keeps the
                whole picture and fills the gaps with a blurred copy.
              </div>
            </div>

            <div className="ed-grp">
              <label>Zoom</label>
              <div className="ed-row">
                <input type="range" min="1" max="2.5" step="0.01" value={zoom} disabled={busy}
                  onChange={e=>setZoom(+e.target.value)}/>
                <span className="ed-num">{zoom.toFixed(2)}x</span>
              </div>
            </div>

            <div className="ed-grp">
              <label>Position</label>
              <div className="ed-row">
                <span className="ed-num" style={{textAlign:'left',minWidth:14}}>X</span>
                <input type="range" min="-0.5" max="0.5" step="0.01" value={offX} disabled={busy}
                  onChange={e=>setOffX(+e.target.value)}/>
              </div>
              <div className="ed-row">
                <span className="ed-num" style={{textAlign:'left',minWidth:14}}>Y</span>
                <input type="range" min="-0.5" max="0.5" step="0.01" value={offY} disabled={busy}
                  onChange={e=>setOffY(+e.target.value)}/>
              </div>
              <button className="rd-btn sm" disabled={busy}
                onClick={()=>{setZoom(1);setOffX(0);setOffY(0);}}>Reset framing</button>
            </div>

            {captionsOn && <div className="ed-grp">
              <label>Auto-captions</label>
              {!caps && !capJob &&
                <button className="rd-btn sm" onClick={makeCaptions} disabled={busy}>
                  <Icon name="sparkles" size={13}/>&nbsp;Generate captions
                </button>}
              {capJob &&
                <>
                  <div className="ed-prog"><i style={{width:(capJob.pct||0)+'%'}}/></div>
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
              </>}
              {caps && !capJob && <>
                <div className="ed-seg">
                  {[['top','Top'],['middle','Middle'],['bottom','Bottom'],['low','Low']].map(([k,l])=>(
                    <button key={k} className={capPos===k?'on':''} disabled={busy}
                      onClick={()=>setCapPos(k)}>{l}</button>
                  ))}
                </div>
                <div className="ed-row">
                  <span className="ed-num" style={{textAlign:'left',minWidth:30}}>Size</span>
                  <input type="range" min="0.035" max="0.09" step="0.005" value={capSize}
                    disabled={busy} onChange={e=>setCapSize(+e.target.value)}/>
                </div>
                <button className={'rd-btn sm'+(capHi?' grad':'')} disabled={busy}
                  onClick={()=>setCapHi(!capHi)}>
                  {capHi ? 'Highlight box on' : 'Highlight box off'}
                </button>
                <div className="ed-note">
                  "Low" sits under the action — on TikTok and Reels the platform
                  puts its own captions and buttons there, so it can end up
                  covered. The highlight box reads on busy gameplay where an
                  outline alone can disappear.
                </div>
              </>}
              {capErr && <div className="ed-warn">{capErr}</div>}
            </div>}

            <div className="ed-grp">
              <label>Title text</label>
              <textarea className="ed-in" rows="2" value={text} disabled={busy}
                placeholder="Optional text on the clip" maxLength={120}
                onChange={e=>setText(e.target.value)}/>
              <div className="ed-seg">
                {['top','middle','bottom'].map(p=>(
                  <button key={p} className={textPos===p?'on':''} disabled={busy}
                    onClick={()=>setTP(p)}>{p}</button>
                ))}
              </div>
              <div className="ed-row">
                <span className="ed-num" style={{textAlign:'left',minWidth:30}}>Size</span>
                <input type="range" min="0.04" max="0.14" step="0.005" value={textSize} disabled={busy}
                  onChange={e=>setTS(+e.target.value)}/>
              </div>
            </div>

            {busy && <div className="ed-grp">
              <div className="ed-prog"><i style={{width:pct+'%'}}/></div>
              <div className="ed-note">Exporting… {Math.round(pct)}%</div>
              <button className="rd-btn sm danger" onClick={()=>{cancelRef.current=true;}}>Cancel</button>
            </div>}

            {!busy && <button className="rd-btn grad" onClick={runExport} disabled={!canExport}>
              <Icon name="download" size={14}/>&nbsp;Export {ratio}
            </button>}

            {!canExport &&
              <div className="ed-warn">This browser can't export video. Use Chrome, Edge or Safari.</div>}
            {canExport && !busy && !done &&
              <div className="ed-note">Renders on your machine, about {eta}s. Keep this tab open.</div>}
            {done && <div className="ed-note" style={{color:'var(--acc)'}}>{done}</div>}
            {err && <div className="ed-warn">{err}</div>}

            {/* The render is in the Scheduler now — that is where posting
                lives, so the editor stays about editing. */}
            {shareReady && <div className="ed-note" style={{color:'var(--acc)'}}>
              Open the <b>Scheduler</b> tab to caption it, pick platforms and post.
            </div>}
          </div>
        </div>
      </div>
    </div>
  );
}


/* ── Scheduler ────────────────────────────────────────────────────────────────
   Everything exported lands here. This is where posting lives, so it gets the
   room: a card per clip with the video, the caption, the platform buttons and
   the fit check, rather than a strip squeezed into the editor's sidebar.

   It REMINDS. It cannot post — the app holds no TikTok/Instagram/YouTube
   credentials by design. Every string here has to say so. */
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

function ScheduleCard({ item, platforms, onChange, onDrop }) {
  const [cap, setCap]     = useState(item.caption || '');
  const [when, setWhen]   = useState(item.due_at ? toLocalInput(item.due_at) : '');
  const [picked, setPicked] = useState(new Set(item.platforms || []));
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);
  const [busyErr, setBusyErr] = useState('');
  const [shareable, setShareable] = useState(false);

  // Only offer the share sheet if this browser can actually take a file. It is
  // absent on most desktops, so the download + upload-page path below is the
  // real path there, not a fallback apology.
  useEffect(()=>{
    try {
      const probe = new File([new Blob([1])], 'x.mp4', {type:'video/mp4'});
      setShareable(!!(navigator.canShare && navigator.share && navigator.canShare({files:[probe]})));
    } catch { setShareable(false); }
  },[]);

  const save = async (patch) => {
    setSaving(true); setBusyErr('');
    try {
      const r = await fetch('/publish/schedule/'+item.id, {method:'PATCH',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify(patch)});
      if(!r.ok){ let d='Could not save'; try{ d=(await r.json()).detail||d; }catch{} setBusyErr(d); }
    } catch { setBusyErr('Could not reach the server'); }
    setSaving(false);
    if (onChange) onChange();
  };

  const toggle = (id) => {
    const next = new Set(picked);
    next.has(id) ? next.delete(id) : next.add(id);
    setPicked(next);
    save({platforms:[...next]});
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

  const when_ = item.missed ? 'Missed' : item.due ? 'Post now'
              : item.scheduled ? qWhen(item.due_at) : 'No time set';

  return (
    <div className={'sc-card glass'+(item.due?' due':'')+(item.missed?' missed':'')}>
      <div className="sc-media">
        <video src={'/uploads/'+item.upload_id+'/file'} controls preload="metadata"/>
      </div>
      <div className="sc-body">
        <div className="sc-top">
          <div className="sc-name">{item.filename}</div>
          <div className={'sc-when'+(item.missed?' missed':'')}>{when_}</div>
        </div>

        <textarea className="ed-in" rows="3" value={cap}
          placeholder="Caption + hashtags — written once, used everywhere"
          onChange={e=>setCap(e.target.value)} onBlur={()=>save({caption:cap})}/>

        <div className="sc-plats">
          {(platforms||[]).map(pf=>{
            const issues = fitIssues(pf, item.duration_s||0, item.ratio||'', cap, item.fmt||'');
            const on = picked.has(pf.id);
            return (
              <div key={pf.id} className="sc-plat">
                <button className={'rd-btn sm'+(on?' grad':'')} onClick={()=>toggle(pf.id)}>
                  {on ? '✓ ' : ''}{pf.label}
                </button>
                <a className="rd-btn sm" href={pf.upload_url} target="_blank"
                   rel="noopener noreferrer">Open</a>
                {issues.length
                  ? <span className="pub-warn">{issues[0]}</span>
                  : <span className="pub-ok">Fits · {Math.round(item.duration_s||0)}s</span>}
              </div>
            );
          })}
        </div>

        <div className="sc-actions">
          {shareable && <button className="rd-btn sm grad" onClick={share}>
            <Icon name="upload" size={13}/>&nbsp;Share to an app
          </button>}
          <a className="rd-btn sm" href={'/uploads/'+item.upload_id+'/file'}
             download={item.filename}>Download</a>
          <button className="rd-btn sm" onClick={copy} disabled={!cap}>
            {copied ? 'Copied' : 'Copy caption'}
          </button>
          <input className="ed-in" type="datetime-local" value={when}
            style={{flex:'1 1 176px',minWidth:150}}
            onChange={e=>{ setWhen(e.target.value);
              const t = e.target.value ? Math.floor(new Date(e.target.value).getTime()/1000) : 0;
              save({due_at: t}); }}/>
          <button className="rd-btn sm" onClick={()=>save({status:'posted'})}
            disabled={saving}>Mark posted</button>
          <button className="rd-btn sm danger" onClick={()=>onDrop(item.id)}>Remove</button>
        </div>
        {busyErr && <div className="ed-warn">{busyErr}</div>}
      </div>
    </div>
  );
}

function ScheduleScreen({ me, queue = [], platforms = [], uploadsOn = true }) {
  const [tab, setTab] = useState('todo');
  const drop = (id) => fetch('/publish/schedule/'+id, {method:'DELETE'}).catch(()=>{});

  const pending = (queue||[]).filter(i=>i.status==='pending');
  const done    = (queue||[]).filter(i=>i.status!=='pending');
  const shown   = tab==='todo' ? pending : done;

  return (
    <div className="rd-wrap">
      {/* Same three-step shape as the other tabs: nothing on screen otherwise
          says where these clips came from or what the user is meant to do. */}
      <div className="rd-how">
        {[['download','1','Export a clip','Anything you export in the Clip Editor lands here automatically.'],
          ['chat','2','Write it once','One caption, reused for every platform. We check it fits before you post.'],
          ['clock','3','Post it','Share straight to the apps from your phone, or set a time and we will nudge you.']
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

      <div className="ed-warn" style={{marginTop:12,color:'var(--fg-3)',
        background:'rgba(255,255,255,.03)',borderColor:'var(--hair)'}}>
        Highlightz never posts for you and never asks for your TikTok, Instagram
        or YouTube login. Your clip goes to your device and you post it from
        your own account — a reminder here is a nudge, not an upload.
      </div>

      <div className="ed-seg" style={{maxWidth:280,marginTop:14}}>
        <button className={tab==='todo'?'on':''} onClick={()=>setTab('todo')}>
          To post{pending.length?' ('+pending.length+')':''}
        </button>
        <button className={tab==='done'?'on':''} onClick={()=>setTab('done')}>
          Done{done.length?' ('+done.length+')':''}
        </button>
      </div>

      {!uploadsOn &&
        <div className="ed-warn" style={{marginTop:14}}>
          The Clip Editor is switched off, so nothing can reach the Scheduler yet.
        </div>}

      {shown.length === 0
        ? <div className="rd-card glass" style={{marginTop:14}}>
            <h3><span className="si"><Icon name="clock" size={15}/></span>
              {tab==='todo' ? 'Nothing waiting to post' : 'Nothing posted yet'}</h3>
            <div className="desc">
              {tab==='todo'
                ? 'Export a clip in the Clip Editor and it shows up here, ready to caption and post.'
                : 'Clips you mark as posted move here, so the list above stays what is left to do.'}
            </div>
          </div>
        : <div className="sc-list">
            {shown.map(i=>(
              <ScheduleCard key={i.id} item={i} platforms={platforms} onDrop={drop}/>
            ))}
          </div>}
    </div>
  );
}

function UploadScreen({ me, uploadsOn = true, importOn = false, captionsOn = false, platforms = [] }) {
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
          <div className="rd-card glass" style={{textAlign:'center',padding:'42px 28px'}}>
            <div style={{marginBottom:12,color:'var(--acc)'}}><Icon name="upload" size={40}/></div>
            <h3 style={{fontSize:18,marginBottom:8,justifyContent:'center'}}>Clip Editor is a Pro feature</h3>
            <div className="desc" style={{maxWidth:460,margin:'0 auto 20px'}}>
              Bring your own clips into Highlightz to edit and publish. Included with
              Pro, along with the VOD scanner, 10 monitored streams and a 200-clip queue.
            </div>
            <a href="/billing/portal" className="rd-btn grad" style={{textDecoration:'none',display:'inline-flex',gap:7,alignItems:'center'}}>
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
          <div style={{display:'flex',alignItems:'center',gap:10,padding:'10px 14px',borderRadius:12,
                       background:'rgba(255,138,76,.12)',border:'1px solid rgba(255,138,76,.32)',
                       fontSize:12.5,color:'#ff9a52',fontWeight:600}}>
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
          {uploads.length>0 && <div style={{marginTop:14}}>
            <div className="ed-note" style={{marginBottom:7}}>
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

          {running.length>0 && <div style={{marginTop:14}}>
            {running.map(([id,p])=>(
              <div className="rd-uprow" key={id}>
                <div style={{fontSize:12,fontWeight:600,maxWidth:180,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{p.name}</div>
                <div className="pb"><i style={{width:(p.err?100:p.pct)+'%',background:p.err?'var(--danger)':undefined}}/></div>
                <div style={{fontSize:11,color:p.err?'var(--danger)':'var(--fg-3)',minWidth:76,textAlign:'right'}}>
                  {p.err ? p.err : (p.pct<100?p.pct+'%':'Processing...')}
                </div>
              </div>
            ))}
          </div>}

          {err && <div style={{marginTop:12,fontSize:12,color:'var(--danger)'}}>{err}</div>}

          {quota && <div style={{marginTop:16}}>
            <div className={'rd-quota'+(pct>=90?' rd-quota-full':'')}><i style={{width:pct+'%'}}/></div>
            <div style={{fontSize:11,color:'var(--fg-3)'}}>
              {fmtBytes(quota.used)} of {fmtBytes(quota.limit)} used · {fmtBytes(quota.remaining)} free
            </div>
          </div>}
        </div>

        <div className="rd-card glass">
          <h3><span className="si"><Icon name="film" size={15}/></span>Your clips</h3>
          <div className="desc">Everything you've uploaded. Hit Edit on any of them to trim,
            reframe and export — publishing straight to TikTok lands here next.</div>
          {uploads.length===0
            ? <div className="rd-grid-empty" style={{padding:'40px 0'}}>
                <div className="ic"><Icon name="film" size={38}/></div>
                <div className="big">No clips uploaded yet</div>
                <div>Drop a clip above and the editor opens automatically.</div>
              </div>
            : <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(260px,1fr))',gap:14}}>
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
      {editing && <ClipEditor clip={editing} onClose={()=>setEditing(null)} captionsOn={captionsOn} platforms={platforms}/>}
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
    <div style={{marginBottom:14}}>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'baseline',
                   fontSize:12,color:'var(--fg-2)',marginBottom:6,gap:10}}>
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
                                         transition:'width .5s ease'}}/>
      </div>
      {job.phase === 'audio' && (
        <div style={{fontSize:11,color:'var(--fg-3)',marginTop:6,lineHeight:1.5}}>
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
          <div className="rd-card glass" style={{textAlign:'center',padding:'42px 28px'}}>
            <div style={{marginBottom:12,color:'var(--acc)'}}><Icon name="film" size={40}/></div>
            <h3 style={{fontSize:18,marginBottom:8,justifyContent:'center'}}>VOD scanning is a Pro feature</h3>
            <div className="desc" style={{maxWidth:440,margin:'0 auto 20px'}}>
              Scan past broadcasts for highlights you missed — the formula replays the
              whole VOD's chat and surfaces the best moments. Included with Pro, along
              with 10 monitored streams and a 200-clip review queue.
            </div>
            <a href="/billing/portal" className="rd-btn grad" style={{textDecoration:'none',display:'inline-flex',gap:7,alignItems:'center'}}>
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
            <div style={{display:'flex',gap:10,alignItems:'center',flexWrap:'wrap'}}>
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
          {err && <div style={{marginTop:10,padding:'9px 13px',borderRadius:10,background:'rgba(255,90,120,.08)',border:'1px solid rgba(255,90,120,.2)',color:'var(--danger)',fontSize:13}}>{err}</div>}
          <div style={{marginTop:14,padding:'10px 13px',borderRadius:10,background:'rgba(255,255,255,.03)',border:'1px solid var(--hair)',fontSize:12,color:'var(--fg-3)',lineHeight:1.6}}>
            <strong style={{color:'var(--fg-2)'}}>How it works:</strong> The bot pulls the VOD{audioOn?' chat replay and its audio track':' chat replay'}, then scans second-by-second with the same scoring engine as live monitoring — chat velocity, keywords, sentiment{audioOn?', and audio spikes':''}. When the score crosses the threshold, a moment is found. Each moment links to that exact timestamp in the VOD, and lands in your review queue automatically.{audioOn?' Audio scans take a few minutes; nothing is recorded or stored — only loudness is measured.':''}
          </div>
        </div>

        {shown.length>0 && shown.map(job=>(
          <div key={job.id} className="rd-card glass">
            <div style={{display:'flex',alignItems:'flex-start',gap:12,marginBottom:14}}>
              {job.thumbnail_url
                ? <img src={job.thumbnail_url} alt="" onError={e=>{e.target.style.display='none'}} style={{width:80,height:45,borderRadius:8,objectFit:'cover',flexShrink:0}}/>
                : <div style={{width:80,height:45,borderRadius:8,background:'var(--grad-soft)',flexShrink:0,display:'grid',placeItems:'center'}}><Icon name="video" size={18} style={{color:'var(--acc)'}}/></div>}
              <div style={{flex:1,minWidth:0}}>
                <div style={{fontWeight:700,fontSize:14,marginBottom:3,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>
                  {job.vod_title||`VOD ${job.vod_id}`}
                </div>
                <div style={{fontSize:12,color:'var(--fg-2)',display:'flex',gap:10,flexWrap:'wrap'}}>
                  {job.channel && <span>{job.channel}</span>}
                  {job.game && <span>{job.game}</span>}
                  {job.duration>0 && <span><Icon name="clock" size={11} style={{display:'inline',verticalAlign:'middle',marginRight:3}}/>{fmtDuration(job.duration)}</span>}
                </div>
              </div>
              <div style={{display:'flex',gap:6,alignItems:'center',flexShrink:0}}>
                {job.status==='running' && <button className="rd-btn sm danger" onClick={()=>cancelJob(job.id)}>Cancel</button>}
                {job.status==='done' && <button className="rd-btn sm" onClick={()=>cancelJob(job.id)} title="Remove"><Icon name="trash" size={13}/></button>}
              </div>
            </div>

            {job.status==='running' && <ScanActivity job={job}/>}

            {job.status==='failed' && (
              <div style={{padding:'9px 13px',borderRadius:10,background:'rgba(255,90,120,.08)',border:'1px solid rgba(255,90,120,.2)',color:'var(--danger)',fontSize:13,marginBottom:12}}>
                {job.error||'Analysis failed'}
              </div>
            )}

            {job.status==='done' && (
              <div style={{display:'flex',alignItems:'center',gap:8,fontSize:13,color:'var(--live)',fontWeight:600,marginBottom:14}}>
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
                      display:'flex',alignItems:'center',gap:12,padding:'10px 13px',
                      borderRadius:12,background:'rgba(255,255,255,.03)',border:'1px solid var(--hair)',
                    }}>
                      <span style={{
                        minWidth:36,height:36,borderRadius:10,
                        background: sc>=75?'var(--live-soft)':sc>=50?'var(--pending-soft)':'var(--grad-soft)',
                        color: sc>=75?'var(--live)':sc>=50?'var(--pending)':'var(--acc)',
                        display:'grid',placeItems:'center',fontWeight:800,fontSize:13,flexShrink:0,
                        fontVariantNumeric:'tabular-nums',
                      }}>{sc}</span>
                      <div style={{flex:1,minWidth:0}}>
                        <div style={{fontWeight:600,fontSize:13}}>{m.timestamp}</div>
                        <div style={{fontSize:11,color:'var(--fg-3)',marginTop:2}}>
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
          <div className="rd-grid-empty" style={{paddingTop:40}}>
            <div className="ic"><Icon name="video" size={42}/></div>
            <div className="big">No VOD scans yet</div>
            <div>Paste a Twitch VOD URL above to find highlight moments from any past stream.</div>
          </div>
        )}
      </div>
    </div>
  );
}

function WelcomeOverlay({ onClose }) {
  const Step = ({n, title, body}) => (
    <div style={{display:'flex',gap:14,alignItems:'flex-start'}}>
      <span style={{width:28,height:28,borderRadius:9,background:'var(--grad-soft)',color:'var(--acc)',display:'grid',placeItems:'center',fontSize:13,fontWeight:800,flexShrink:0}}>{n}</span>
      <div>
        <div style={{fontWeight:700,fontSize:14,marginBottom:3}}>{title}</div>
        <div style={{fontSize:13,color:'var(--fg-3)',lineHeight:1.6}}>{body}</div>
      </div>
    </div>
  );
  return (
    <div style={{position:'fixed',inset:0,zIndex:60,background:'rgba(5,4,8,.78)',backdropFilter:'blur(10px)',WebkitBackdropFilter:'blur(10px)',display:'flex',alignItems:'center',justifyContent:'center',padding:20,overflowY:'auto'}}>
      <div className="glass wm-card" style={{borderRadius:24,maxWidth:640,width:'100%',padding:'40px 42px',maxHeight:'92vh',overflowY:'auto'}}>
        <div style={{display:'flex',justifyContent:'center',marginBottom:18}}>
          <img src="/static/logo-mark.png" alt="Highlightz" style={{height:40,filter:'drop-shadow(0 0 14px rgba(199,155,255,.4))'}}/>
        </div>
        <h1 style={{fontSize:26,fontWeight:800,letterSpacing:'-.025em',textAlign:'center',marginBottom:8}}>Welcome to Highlightz</h1>
        <p style={{fontSize:14,color:'var(--fg-3)',textAlign:'center',lineHeight:1.65,marginBottom:26}}>
          A tool that makes clipping easier. Highlightz watches your streams live and
          captures the best moments automatically — so you never miss a highlight again.
        </p>

        <div style={{display:'flex',flexDirection:'column',gap:18,marginBottom:26}}>
          <Step n="1" title="Add any live Twitch channel"
            body="Monitor multiple streams at the same time — your own channel, streamers you clip for, or anyone live right now."/>
          <Step n="2" title="A formula scores every second — not AI"
            body="Highlightz uses a transparent mathematical formula that combines chat speed, audio spikes, keywords, viewer surges, and hype moments into one live score. No AI, no black box — you can watch the score move in real time."/>
          <Step n="3" title="It adapts to every streamer"
            body="The formula learns each channel's normal — a quiet chess stream and a loud FPS stream trigger at the same fairness. The more it watches, the sharper it gets."/>
          <Step n="4" title="Clips are created right on Twitch"
            body="Fully connected to your Twitch account. When the score crosses the threshold, a real Twitch clip is created instantly under your account — hosted by Twitch, ready to share."/>
          <Step n="5" title="You stay in control"
            body="Every clip lands in your review queue. Approve the keepers, reject the misses — and the formula tunes itself to your taste."/>
        </div>

        <div style={{display:'flex',gap:10,flexWrap:'wrap',justifyContent:'center',marginBottom:26}}>
          {['Formula-based — not AI','Adapts to each streamer','Multiple streams at once','Fully connected to Twitch'].map(t=>(
            <span key={t} style={{fontSize:12,fontWeight:600,padding:'6px 13px',borderRadius:99,background:'rgba(168,85,247,.12)',border:'1px solid rgba(168,85,247,.3)',color:'#c79bff'}}>{t}</span>
          ))}
        </div>

        <button className="rd-btn grad" style={{width:'100%',justifyContent:'center',padding:'13px',fontSize:15}} onClick={onClose}>
          <Icon name="zap" size={15}/>Start clipping
        </button>
      </div>
    </div>
  );
}

// Shared "not ready yet" screen. Two callers with different palettes: Kick
// (green) and held-back features like Clip Editor (the app's purple), so the
// screen reads as part of whatever the user was looking at.
const UC_THEME = {
  kick:   { a:'#53fc18', b:'#39b515' },
  violet: { a:'#c79bff', b:'#a855f7' },
};

function UnderConstruction({ theme='kick', title='Kick is coming soon', children, note }) {
  const { a, b } = UC_THEME[theme] || UC_THEME.kick;
  const tint = (o)=>theme==='kick'?`rgba(83,252,24,${o})`:`rgba(168,85,247,${o})`;
  return (
    <div style={{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',
                 textAlign:'center',minHeight:'70vh',padding:'40px 24px',gap:22}}>
      <div style={{width:96,height:96,borderRadius:26,display:'grid',placeItems:'center',color:a,
                   background:tint(.1),border:'1px solid '+tint(.32),
                   boxShadow:'0 12px 40px -14px '+tint(.45)}}><Icon name="cog" size={44}/></div>
      <div style={{display:'inline-flex',alignItems:'center',gap:9,padding:'7px 16px',borderRadius:999,
                   background:tint(.12),border:'1px solid '+tint(.35),
                   color:a,fontWeight:800,fontSize:12.5,letterSpacing:'.14em',textTransform:'uppercase'}}>
        <span style={{width:8,height:8,borderRadius:'50%',background:a,boxShadow:'0 0 10px '+a}}/>
        Under Construction
      </div>
      <h1 style={{margin:0,fontSize:38,fontWeight:900,letterSpacing:'-.02em',
                  background:`linear-gradient(135deg,${a},${b})`,
                  WebkitBackgroundClip:'text',WebkitTextFillColor:'transparent',backgroundClip:'text'}}>
        {title}
      </h1>
      <p style={{margin:0,maxWidth:560,fontSize:15.5,lineHeight:1.6,color:'var(--fg-2)'}}>
        {children}
      </p>
      <p style={{margin:0,fontSize:13,color:'var(--fg-3)'}}>
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
  const [welcome, setWelcome] = useState(()=>{ try { return !localStorage.getItem('hz_welcome_seen'); } catch { return false; } });
  const dismissWelcome = () => { try { localStorage.setItem('hz_welcome_seen','1'); } catch {} setWelcome(false); };
  const [streams, setStreams] = useState({});
  const [scores, setScores] = useState({});
  const [profiles, setProfiles] = useState({});
  const [histories, setHistories] = useState({});
  const [clips, setClips] = useState({});
  const [filter, setFilter] = useState('all');
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
  // The posting queue. Reminders only — we hold no platform credentials,
  // so nothing here posts by itself and every string must say so.
  const [queue, setQueue] = useState([]);
  // {clips} while the review prompt is open, null otherwise.
  const [reviewAsk, setReviewAsk] = useState(null);
  // Clips DELETED by the pending cap. Not 'missed' — the new clip is kept
  // and the oldest unreviewed one is dropped, which is what the notice says.
  const [lostClips, setLostClips] = useState(null);
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
    fetch('/publish/platforms').then(r=>r.json()).then(d=>setPlatforms(d.platforms||[])).catch(()=>{});
    fetch('/publish/schedule').then(r=>r.json()).then(d=>setQueue(d.items||[])).catch(()=>{});
    // Which clips are featured on the landing page (admin curation state).
    fetch('/landing/showcase').then(r=>r.json()).then(d=>setFeatured(d.clips||[])).catch(()=>{});
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
        // The other direction — a user answered on their thread. Only admins
        // receive this (it is fanned out by id, never broadcast to everyone).
        if(msg.event==='feedback_new'){
          loadFbUnread();
          window.dispatchEvent(new CustomEvent('hz_fb_reply'));
          flash((msg.username||'Someone') + ' replied to their feedback');
        }
        if(msg.event==='clip_ready'){setClips(p=>({...p,[msg.clip.id]:msg.clip}));flash('New clip from '+msg.clip.channel);}
        else if(msg.event==='clip_updated'){
          setClips(p=>({...p,[msg.clip.id]:msg.clip}));
          setModalClip(prev=>prev&&prev.id===msg.clip.id?msg.clip:prev);
        }
        else if(msg.event==='clip_removed'){setClips(p=>{const n={...p};delete n[msg.clip_id];return n;});}
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
      setStreams(p=>({...p,[s.channel]:s}));
      flash('Monitoring '+s.channel);
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
    if(r.ok){const u=await r.json();setClips(p=>({...p,[id]:u}));}
    else{flash('Could not approve clip — it may have been removed. Refreshing...');refetchAll();}
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
  // Clip Editor and Scheduler are ADMIN ONLY, full stop. Not gated on the
  // release flags: UPLOADS_ENABLED was set true in production, which handed
  // every Pro subscriber a working Editor and Scheduler. Tying visibility to a
  // flag means one env edit silently ships an unreleased feature again, so the
  // owner is the only one who sees these until that is a deliberate decision.
  const adminOnlyTabs = ['uploads', 'schedule'];
  // The screen actually rendered. The nav is the only way in today (`route`
  // lives in React state alone), but that is a property of the current code,
  // not a guarantee — normalise so a future deep link or restored route cannot
  // walk into one of these. Falls back to the review queue.
  const view = (adminOnlyTabs.includes(route) && !(me && me.is_admin)) ? 'review' : route;

  let screen;
  // Kick is temporarily closed off while automated clipping is built — show a
  // big "under construction" prompt for every platform-specific feature screen.
  // Account and Feedback are global and stay open; Settings shows Twitch-only
  // stats so it's also gated while Kick is under construction.
  // KICK_BLOCKED is the single source of truth, shared with the nav below so
  // a tab can never be clickable-but-dead (or greyed-out-but-working).
  if(activePlatform==='kick' && KICK_BLOCKED.includes(view)) screen=<KickUnderConstruction/>;
  else if(view==='uploads' && !clipTabOn) screen=<UploadsUnderConstruction/>;
  else if(view==='review') screen=<ReviewScreen {...{streams:platformStreams,scores,clips:platformClips,filter,setFilter,onApprove:approveClip,onReject:rejectClip,onOpen:setModalClip,lost:lostClips,me,onDismissLost:dismissMissNotice}}/>;
  else if(view==='streams') screen=<StreamsScreen {...{streams:platformStreams,scores,profiles,histories,clips:platformClips,activePlatform,onAdd:addStream,onRemove:removeStream,onForce:forceClip}}/>;
  else if(view==='library') screen=<LibraryScreen {...{clips:platformClips,onOpen:setModalClip,onDelete:deleteClip,onGoReview:()=>setRoute('review')}}/>;
  else if(view==='vod') screen=<VodScreen clips={platformClips} me={me}/>;
  else if(view==='schedule') screen=<ScheduleScreen me={me} queue={queue} platforms={platforms} uploadsOn={uploadsOn}/>;
  else if(view==='uploads') screen=<UploadScreen me={me} uploadsOn={uploadsOn} importOn={importOn} captionsOn={captionsOn} platforms={platforms}/>;
  else if(view==='training') screen=<TrainingScreen/>;
  else if(view==='landing') screen=<LandingScreen clips={clips} featured={featured} onToggle={toggleFeature} onMove={moveFeature} onGrab={grabFeature} myUrls={myClipUrls}/>;
  else if(view==='account') screen=<AccountScreen me={me}/>;
  else if(view==='feedback') screen=<FeedbackScreen onSeen={loadFbUnread}/>;
  else screen=<SettingsScreen {...{streams}}/>;

  return (
    <div className={'rd-app'+(activePlatform==='kick'?' kick-theme':'')} id="rd-app" data-grad="violet" data-density="comfortable" data-glow="on">

      <div className={'rd-navscrim'+(navOpen?' open':'')} onClick={()=>setNavOpen(false)}/>
      <nav className={'rd-nav'+(navOpen?' open':'')}>
        <span className="logo"><img src="/static/logo-mark.png" alt="Highlightz"/></span>
        {NAV.filter(n=>(!n.labelerOnly||(me&&(me.is_labeler||me.is_admin)))&&(!n.adminOnly||(me&&me.is_admin))).map(n=>{
          // On Kick every platform-specific tab is closed off, so the button is
          // genuinely disabled — not just visually dimmed. `disabled` is what
          // actually stops the click; the class only makes that visible.
          const blocked = activePlatform==='kick' && KICK_BLOCKED.includes(n.id);
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
        {me.subscription_status==='trialing' && <div style={{display:'flex',alignItems:'center',gap:10,padding:'9px 22px',background:'rgba(145,70,255,.1)',borderBottom:'1px solid rgba(145,70,255,.22)',fontSize:12.5,color:'#c79bff',fontWeight:600}}>
          <span style={{width:7,height:7,borderRadius:'50%',background:'#22c55e',boxShadow:'0 0 8px #22c55e',flexShrink:0}}/>
          <span>Free trial — {me.trial_days_left||0} day{(me.trial_days_left||0)===1?'':'s'} left. <span style={{color:'#9c9caa',fontWeight:500}}>Subscribe to keep access when it ends — promo codes get 50% off your first month.</span></span>
          <a href="/billing/checkout" style={{marginLeft:'auto',color:'#fff',background:'#9146ff',textDecoration:'none',padding:'5px 12px',borderRadius:8,fontWeight:700,whiteSpace:'nowrap'}}>Subscribe</a>
        </div>}
        <main className="rd-screen">{screen}</main>
      </div>
      {reviewAsk && <ReviewPrompt clips={reviewAsk.clips}
        onClose={()=>setReviewAsk(null)}/>}
      <UndoToast entry={undoable} onUndo={doUndo} onDismiss={()=>setUndoable(null)}/>
      <RdToast msg={toast}/>
      <ClipModal clip={modalClip} onClose={()=>setModalClip(null)} onApprove={approveClip} onReject={rejectClip}
        isAdmin={!!me.is_admin} featured={!!modalClip&&featuredIds.includes(modalClip.id)} onFeature={toggleFeature}/>
      {welcome && <WelcomeOverlay onClose={dismissWelcome}/>}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<RdApp/>);
</script>
</body>
</html>"""
