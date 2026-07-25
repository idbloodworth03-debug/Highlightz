"""Aurora dashboard HTML — single-file React SPA served at GET /."""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Highlightz</title>
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
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
.rd-rail{border-radius:var(--r-lg);padding:16px;display:flex;flex-direction:column;gap:16px;overflow:hidden}
.rd-rail-head{display:flex;align-items:center;justify-content:space-between}
.rd-eyebrow{font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--fg-3)}
.rd-count{font-size:11px;font-weight:600;color:var(--fg-2);background:rgba(255,255,255,.05);padding:3px 9px;border-radius:var(--r-pill)}
.rd-addrow{display:flex;gap:8px}
.rd-suggwrap{position:relative;flex:1;min-width:0;display:flex}
.rd-suggwrap .rd-input{width:100%}
.rd-sugg{position:absolute;top:calc(100% + 6px);left:0;right:0;z-index:60;background:#101016;
  border:1px solid var(--hair-2);border-radius:12px;box-shadow:0 14px 36px rgba(0,0,0,.55);
  max-height:320px;overflow-y:auto;padding:6px}
.rd-sugglabel{font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--fg-3);padding:7px 9px 3px}
.rd-suggitem{display:flex;align-items:center;gap:8px;padding:7px 9px;border-radius:8px;cursor:pointer;font-size:13px;color:var(--fg)}
.rd-suggitem:hover{background:rgba(255,255,255,.06)}
.rd-suggitem .meta{margin-left:auto;color:var(--fg-3);font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:120px;flex-shrink:0}
.rd-sugglive{font-size:9.5px;font-weight:800;letter-spacing:.05em;color:#fff;background:#e91916;border-radius:4px;padding:1px 5px;flex-shrink:0}
.rd-suggempty{padding:12px 9px;font-size:12.5px;color:var(--fg-3)}
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
.rd-clip{border-radius:var(--r-lg);overflow:hidden;background:var(--panel);border:1px solid var(--hair);transition:transform .22s cubic-bezier(.4,0,.2,1),border-color .22s,box-shadow .22s;display:flex;flex-direction:column;height:360px}
.rd-clip:hover{transform:translateY(-4px);border-color:rgba(199,155,255,.35);box-shadow:var(--shadow-card)}
.rd-media{position:relative;width:100%;height:0;padding-bottom:56.25%;overflow:hidden}
.rd-thumb{position:absolute;inset:0}
.rd-thumb::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 40%,rgba(0,0,0,.55))}
.rd-media::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 40%,rgba(0,0,0,.55));pointer-events:none;z-index:1}
.rd-play{position:absolute;inset:0;display:grid;place-items:center;z-index:2}
.rd-play .ring{width:56px;height:56px;border-radius:50%;display:grid;place-items:center;padding-left:3px;
  background:rgba(20,12,30,.4);border:1.5px solid rgba(255,255,255,.85);color:#fff;backdrop-filter:blur(4px);transition:transform .2s,background .2s}
.rd-clip:hover .rd-play .ring{transform:scale(1.08);background:var(--grad);border-color:transparent;box-shadow:var(--glow)}
.rd-scorebadge{position:absolute;top:10px;right:10px;z-index:2;display:inline-flex;align-items:center;gap:5px;
  font-size:12px;font-weight:700;padding:5px 10px;border-radius:var(--r-pill);color:#fff;
  background:rgba(10,8,14,.55);border:1px solid rgba(255,255,255,.16);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);font-variant-numeric:tabular-nums}
.rd-scorebadge .pip{width:6px;height:6px;border-radius:50%}
.rd-viralbadge{position:absolute;top:10px;left:10px;z-index:2;display:inline-flex;align-items:center;gap:5px;
  font-size:11.5px;font-weight:800;padding:5px 10px;border-radius:var(--r-pill);color:#fff;
  background:rgba(10,8,14,.55);border:1px solid rgba(255,255,255,.16);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);font-variant-numeric:tabular-nums}
.rd-viralbadge.hot{background:linear-gradient(135deg,#ff7700,#f943ff);border-color:transparent;box-shadow:0 3px 14px -3px rgba(255,119,0,.65)}
.rd-viralbadge.warm{color:#ffcc5c;border-color:rgba(255,204,92,.35)}
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
.rd-toast{position:fixed;bottom:26px;left:50%;transform:translate(-50%,90px);opacity:0;
  display:inline-flex;align-items:center;gap:10px;padding:13px 20px;border-radius:var(--r-pill);
  background:rgba(18,14,24,.85);border:1px solid rgba(199,155,255,.35);color:var(--fg);font-size:13px;font-weight:500;
  -webkit-backdrop-filter:blur(20px);backdrop-filter:blur(20px);box-shadow:0 16px 40px -12px rgba(0,0,0,.7);z-index:50;transition:all .35s cubic-bezier(.34,1.56,.64,1)}
.rd-toast.show{transform:translate(-50%,0);opacity:1}
.rd-toast .ico{width:24px;height:24px;border-radius:50%;background:var(--grad);display:grid;place-items:center;color:#fff}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:99px;border:2px solid transparent;background-clip:padding-box}
::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.18);background-clip:padding-box}
.rd-nav{display:flex;flex-direction:column;align-items:center;gap:6px;padding:16px 0;
  border-right:1px solid var(--hair);background:rgba(10,10,14,.5);
  -webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px);z-index:6}
.rd-nav .logo{margin-bottom:18px;display:flex}
.rd-nav .logo img{height:44px;filter:drop-shadow(0 0 12px rgba(199,155,255,.55))}
.rd-navitem{width:88px;height:64px;border-radius:16px;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:5px;background:transparent;border:none;
  color:var(--fg-3);font-size:11.5px;font-weight:600;letter-spacing:.01em;transition:.16s;position:relative}
.rd-navitem:hover{color:var(--fg-2);background:rgba(255,255,255,.05)}
.rd-navitem.active{color:#fff}
.rd-navitem.active::before{content:'';position:absolute;inset:0;border-radius:16px;
  background:var(--grad-soft);border:1px solid rgba(199,155,255,.3)}
.rd-navitem.active .ic{color:var(--acc)}
.rd-navitem .ic,.rd-navitem span{position:relative;z-index:1}
.rd-nav .sp{flex:1}
.rd-nav .navbadge{position:absolute;top:7px;right:9px;min-width:16px;height:16px;padding:0 4px;
  border-radius:99px;background:var(--grad);color:#fff;font-size:9px;font-weight:800;display:grid;place-items:center;z-index:2}
.rd-header .htitle{font-size:18px;font-weight:700;letter-spacing:-.02em}
.rd-header .hsub{font-size:12px;color:var(--fg-3);margin-top:1px}
.rd-scroll{flex:1;overflow-y:auto;min-height:0;padding:20px 22px}
.rd-section-title{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.rd-section-title h2{font-size:19px;font-weight:800;letter-spacing:-.025em}
.rd-section-title .cnt{font-size:12px;color:var(--fg-3)}
.rd-streams-layout{display:grid;grid-template-columns:290px 1fr;gap:18px;flex:1;min-height:0;padding:20px 22px}
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
.rd-modal-bg{position:fixed;inset:0;background:rgba(5,4,8,.72);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);
  z-index:60;display:grid;place-items:center;padding:32px}
.rd-modal{width:min(900px,100%);max-height:90vh;border-radius:22px;overflow:hidden;display:flex;flex-direction:column;
  box-shadow:var(--shadow-card);background:rgba(16,14,22,.9);border:1px solid var(--hair-2)}
.rd-modal-media{position:relative;width:100%;padding-bottom:46%;flex-shrink:0}
.rd-modal-media .thumb{position:absolute;inset:0}
.rd-modal-media .thumb::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 50%,rgba(0,0,0,.6))}
.rd-modal-close{position:absolute;top:14px;right:14px;width:36px;height:36px;border-radius:50%;border:none;
  background:rgba(10,8,14,.6);color:#fff;display:grid;place-items:center;-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);z-index:2}
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
  .rd-screen{overflow:visible;padding-bottom:70px}

  /* Vertical nav → bottom tab bar */
  .rd-nav{position:fixed;bottom:0;left:0;right:0;z-index:20;
    flex-direction:row;height:58px;padding:0 4px;gap:0;
    border-right:none;border-top:1px solid var(--hair);
    justify-content:space-around;align-items:center}
  .rd-nav .logo{display:none}
  .rd-nav .sp{display:none}
  .rd-navitem{flex:1;width:auto;height:50px;border-radius:12px;font-size:9px;gap:2px}

  /* Frame: let grid rows auto-size for page scroll */
  .rd-frame{grid-template-rows:56px auto}

  /* Header */
  .rd-header{padding:0 12px;gap:10px;height:56px}
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

  /* Toast: above bottom nav */
  .rd-toast{bottom:70px;font-size:12px;padding:10px 16px;max-width:90vw;text-align:center}
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
  /* Scrolling content must never hide under the fixed bottom nav — including
     the internally-scrolling stream-detail and channel-list columns */
  .rd-body,.rd-scroll,.rd-streams-layout{padding-bottom:78px}
  .rd-detail,.rd-chanlist{padding-bottom:84px}
  .rd-nav{z-index:30}
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
const hiResThumb = url => (url||'').replace(/-preview-\d+x\d+\./, '-preview-1280x720.');

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
            <button className="rd-btn live sm" onClick={e=>{e.stopPropagation();onApprove(clip.id)}}><Icon name="check" size={14}/>Approve</button>
            <button className="rd-btn danger sm" onClick={e=>{e.stopPropagation();onReject(clip.id)}}><Icon name="x" size={14}/>Reject</button>
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
  if (!clip) return null;
  const score = Math.round(clip.score||clip.trigger_score||0);  // VOD clips carry 'score'; both are 0-100
  const dur = fmtDur(clip.duration_seconds);
  const time = fmtTime(clip.created_at);
  const title = clip.clip_title||clip.stream_title||'Live Stream';
  const embed = clip.embed_url || '';
  const twHref = clip.twitch_url || '';
  const thumb = clip.thumbnail_url || '';
  // Twitch embeds require JS and don't work in many mobile browsers (Error #4000).
  // On narrow screens fall back to thumbnail + direct link — same as when no embed_url exists.
  const isMobile = window.innerWidth <= 700;
  const embedSrc = embed && !isMobile
    ? embed + (embed.indexOf('?')>=0?'&':'?') + 'parent=' + location.hostname + '&autoplay=false'
    : '';
  // With no inline embed (mobile, or no embed_url) the clip can only play on
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
            ? <iframe src={embedSrc} allowFullScreen frameBorder="0" scrolling="no" style={{position:'absolute',inset:0,width:'100%',height:'100%',background:'#000'}}/>
            : thumb
              ? <><img src={hiResThumb(thumb)} data-orig={hiResThumb(thumb)!==thumb?thumb:''} alt="" onError={e=>thumbFallback(e, clip.channel)} style={{position:'absolute',inset:0,width:'100%',height:'100%',objectFit:'cover'}}/>{twHref&&<div className="rd-modal-play"><span className="ring"><Icon name="play" size={26}/></span></div>}</>
              : <><div className="thumb" style={{background:thumbFor(clip.channel)}}/><div className="rd-modal-play"><span className="ring"><Icon name="play" size={26}/></span></div></>}
          <button className="rd-modal-close" onClick={e=>{e.stopPropagation();onClose();}}><Icon name="x" size={16}/></button>
          <span className="rd-scorebadge" style={{top:14,right:60}}><span className="pip" style={{background:scoreColor(score)}}/>{score}% trigger</span>
        </div>

        {embedSrc && twHref && <a href={twHref} target="_blank" rel="noopener" style={{display:'flex',alignItems:'center',justifyContent:'center',gap:6,padding:'9px 12px',fontSize:12.5,color:'#a5b4fc',textDecoration:'none',background:'rgba(99,102,241,.10)',borderBottom:'1px solid rgba(255,255,255,.06)'}}>Player not loading? Watch on Twitch ↗</a>}

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

function ReviewScreen({ streams, scores, profiles, clips, filter, setFilter, activePlatform, onAdd, onRemove, onForce, onApprove, onReject, onOpen }) {
  const [ch, setCh] = useState('');
  const [preset, setPreset] = useState('default');
  const [showCull, setShowCull] = useState(false);
  const [sortBy, setSortBy] = useState('newest');
  const [chanFilter, setChanFilter] = useState('all');
  const add = () => { if(ch.trim()){onAdd(ch.trim(),preset,activePlatform);setCh('');setSuggOpen(false);} };
  // Streamer suggestions: zero state = recently monitored + popular-now;
  // typing = Twitch partial-name search (debounced). Twitch-only — the data
  // source is Helix, so the dropdown stays away on other platforms.
  const [sugg, setSugg] = useState(null);
  const [suggOpen, setSuggOpen] = useState(false);
  const suggT = useRef(null);
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
  const pick = (login) => { onAdd(login, preset, activePlatform); setCh(''); setSuggOpen(false); };
  const fmtViewers = (v) => v >= 1000 ? (v/1000).toFixed(v >= 10000 ? 0 : 1) + 'k' : '' + v;
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
  return (
    <div className="rd-body" style={{flex:1}}>
      <aside className="rd-col" style={{minHeight:0}}>
        <div className="rd-rail glass" style={{flex:'0 0 auto'}}>
          <div className="rd-eyebrow">Add a stream</div>
          <div className="rd-addrow">
            <div className="rd-suggwrap">
              <input className="rd-input" placeholder="search a streamer" value={ch}
                onChange={e=>onChInput(e.target.value)}
                onFocus={()=>{ if(canSugg){ setSuggOpen(true); fetchSugg(ch.trim()); } }}
                onBlur={()=>setTimeout(()=>setSuggOpen(false),150)}
                onKeyDown={e=>{ if(e.key==='Enter') add(); if(e.key==='Escape') setSuggOpen(false); }}/>
              {suggOpen && sugg && (
                <div className="rd-sugg">
                  {ch.trim() ? (
                    (sugg.results||[]).length
                      ? (sugg.results||[]).map(r=>(
                          <div key={r.login} className="rd-suggitem" onMouseDown={e=>{e.preventDefault();pick(r.login);}}>
                            {r.avatar ? <img src={r.avatar} alt="" style={{width:20,height:20,borderRadius:'50%',flexShrink:0}}/> : <span style={{width:20,flexShrink:0}}/>}
                            <span style={{fontWeight:600,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{r.name||r.login}</span>
                            {r.is_live && <span className="rd-sugglive">LIVE</span>}
                            <span className="meta">{r.game||''}</span>
                          </div>))
                      : <div className="rd-suggempty">No channels found</div>
                  ) : (
                    <>
                      {(sugg.recent||[]).length > 0 && <>
                        <div className="rd-sugglabel">Recently monitored</div>
                        {(sugg.recent||[]).map(c=>(
                          <div key={'r'+c} className="rd-suggitem" onMouseDown={e=>{e.preventDefault();pick(c);}}>
                            <Icon name="clock" size={13}/><span style={{fontWeight:600}}>{c}</span>
                          </div>))}
                      </>}
                      {(sugg.popular||[]).length > 0 && <>
                        <div className="rd-sugglabel">Popular right now</div>
                        {(sugg.popular||[]).map(p=>(
                          <div key={'p'+p.login} className="rd-suggitem" onMouseDown={e=>{e.preventDefault();pick(p.login);}}>
                            <span className="rd-sugglive">LIVE</span>
                            <span style={{fontWeight:600,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{p.name||p.login}</span>
                            <span className="meta">{fmtViewers(p.viewers)} · {p.game||''}</span>
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
              : streamsArr.map(s=><RdStream key={s.channel} s={s} scoreData={scores[s.channel]} profile={profiles[s.channel]} onRemove={onRemove} onForce={onForce}/>)}
          </div>
        </div>
      </aside>
      <section className="rd-main">
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
            ? <div className="rd-grid-empty"><div className="ic"><Icon name="film" size={42}/></div><div className="big">Waiting for clips</div><div>Add a live stream — clips appear here the moment a highlight fires.</div></div>
            : shown.map(c=><RdClip key={c.id} clip={c} onApprove={onApprove} onReject={onReject} onOpen={onOpen}/>)}
        </div>
      </section>
    </div>
  );
}

function StreamsScreen({ streams, scores, profiles, histories, clips, onForce }) {
  const streamsArr = Object.values(streams);
  const [sel, setSel] = useState(null);
  useEffect(()=>{ if(!sel&&streamsArr.length>0) setSel(streamsArr[0].channel); },[streamsArr.length]);
  const active = streams[sel]||streamsArr[0];
  if(!active) return <div className="rd-scroll"><div className="rd-empty"><span className="ic"><Icon name="radio" size={28}/></span>No streams monitored.</div></div>;
  const p = profiles[active.channel]||{};
  const sd = scores[active.channel]||{score:0,breakdown:{}};
  const hist = histories[active.channel]||[sd.score];
  const recent = Object.values(clips).filter(c=>c.channel===active.channel).sort((a,b)=>(b.created_at||0)-(a.created_at||0)).slice(0,4);
  const WK=[['CHAT_VELOCITY','Chat velocity'],['KEYWORD','Keyword'],['SENTIMENT','Sentiment'],['AUDIO_SPIKE','Audio spike'],['VIEWER_SPIKE','Viewer spike'],['SILENCE_BURST','Silence burst']];
  const sw = p.signal_weights||{};
  const statusColor = active.status==='live'?'var(--live)':active.status==='reconnecting'?'var(--pending)':'var(--fg-2)';
  return (
    <div className="rd-streams-layout">
      <div className="rd-chanlist">
        <div className="rd-eyebrow">Channels · {streamsArr.length}</div>
        {streamsArr.map(s=>{
          const sc=scores[s.channel]||{score:0};
          return <button key={s.channel} className={'rd-chan'+(s.channel===active.channel?' active':'')} onClick={()=>setSel(s.channel)}>
            <span className="av">{initials(s.channel)}</span>
            <div><div className="nm">{s.channel}</div><div className="mt">{s.platform} · {s.preset}</div></div>
            <span className="mini" style={{color:scoreColor(sc.score)}}>{sc.score.toFixed(0)}</span>
          </button>;
        })}
      </div>
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

function LibraryScreen({ clips, onOpen, onApprove, onReject, onDelete }) {
  const [f, setF] = useState('all');
  const [chanFilter, setChanFilter] = useState('all');
  const allClips = Object.values(clips);
  // Filterable streamers derive from the clips themselves — a newly-clipped
  // streamer is selectable the moment their first clip arrives over the WS.
  const channels = [...new Set(allClips.map(c=>c.channel).filter(Boolean))].sort();
  const effChan = channels.includes(chanFilter) ? chanFilter : 'all';
  const clipsArr = allClips
    .filter(c=>(f==='all'||c.status===f)&&(effChan==='all'||c.channel===effChan))
    .sort((a,b)=>(b.created_at||0)-(a.created_at||0));
  const total = allClips.length;
  const approvedCount = allClips.filter(c=>c.status==='approved').length;
  return (
    <div className="rd-scroll">
      <div className="rd-section-title">
        <h2>Clip library</h2>
        <span className="cnt">{total} captured · {approvedCount} approved</span>
        <div style={{marginLeft:'auto',display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
          {channels.length>1 && <select className="rd-select" value={effChan} onChange={e=>setChanFilter(e.target.value)} title="Filter by streamer" style={{padding:'6px 10px',fontSize:12,fontWeight:600}}>
            <option value="all">All streamers</option>
            {channels.map(c=><option key={c} value={c}>{c}</option>)}
          </select>}
          <div className="rd-filters">
            {['all','pending','approved','rejected'].map(x=><button key={x} className={'rd-filter'+(f===x?' active':'')} onClick={()=>setF(x)}>{x[0].toUpperCase()+x.slice(1)}</button>)}
          </div>
        </div>
      </div>
      {clipsArr.length===0
        ? <div className="rd-grid-empty"><div className="ic"><Icon name="film" size={42}/></div><div className="big">Nothing here yet</div><div>Clips you capture will be archived in the library.</div></div>
        : <div className="rd-grid" style={{overflow:'visible',paddingRight:0}}>
            {clipsArr.map(c=><RdClip key={c.id} clip={c} onOpen={onOpen} onApprove={onApprove} onReject={onReject} onDelete={onDelete} libraryMode/>)}
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

const NAV=[{id:'streams',label:'Live Streams',icon:'radio'},{id:'review',label:'Clip Review',icon:'grid'},{id:'library',label:'Clip Library',icon:'film'},{id:'vod',label:'VOD Scanner',icon:'video'},{id:'training',label:'Training',icon:'sparkles',labelerOnly:true},{id:'settings',label:'Settings',icon:'cog'},{id:'account',label:'Account',icon:'user'},{id:'feedback',label:'Feedback',icon:'chat'}];
const HEAD={streams:['Live Streams','Monitor active streams and per-channel analytics'],review:['Clip Review','Approve highlights as they fire'],library:['Clip Library','Every clip you have captured'],vod:['VOD Scanner','Find highlight moments in finished streams'],training:['Training Studio','Blind-score clips to calibrate the formula'],settings:['Settings','Tune triggers, storage & workflow'],account:['Account','Billing, profile & platforms'],feedback:['Feedback','Questions, bugs & suggestions']};

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
  const skip = ()=>{ if(queue&&queue.length) { setIdx(i=>(i+1)%queue.length); setVals({...FRESH}); } };
  const isMobile = window.innerWidth <= 700;
  const embedSrc = cur && cur.embed_url && !isMobile
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
                ? <div style={{position:'relative',paddingBottom:'56.25%',borderRadius:12,overflow:'hidden',background:'#000'}}>
                    <iframe src={embedSrc} style={{position:'absolute',inset:0,width:'100%',height:'100%',border:0}} allowFullScreen scrolling="no" title="Clip"/>
                  </div>
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

function AccountScreen({ me }) {
  const [deleting, setDeleting]   = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [delErr, setDelErr]       = useState('');
  const sub        = me.subscription_status || 'none';
  const trialDays  = me.trial_days_left || 0;
  const isTrial    = sub === 'trialing';
  const subLabel   = isTrial ? `Free trial — ${trialDays} day${trialDays===1?'':'s'} left`
    : ({active:'Active',canceled:'Canceled',past_due:'Past due',expired:'Trial ended',none:'No subscription'}[sub] || sub);
  const subColor   = (sub==='active'||sub==='trialing') ? 'var(--live)' : sub==='past_due' ? 'var(--pending)' : 'var(--fg-3)';
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
          <div className="desc">Manage your Highlightz Pro plan.</div>
          <div className="rd-field">
            <div><div className="fl">Plan status</div></div>
            <span style={{fontWeight:700,color:subColor,textTransform:'capitalize'}}>{subLabel}</span>
          </div>
          {isSubscribed && me.plan_label && <div className="rd-field">
            <div><div className="fl">Membership</div>
              <div className="fd">{me.plan_limits ? `Up to ${me.plan_limits.max_streams} streams · ${me.plan_limits.max_pending} pending clips · VOD scanner ${me.plan_limits.vod?'included':'not included'}` : ''}</div>
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
          {!isSubscribed && sub!=='trialing' && <div style={{marginTop:16}}>
            <a href="/billing/checkout" className="rd-btn grad" style={{textDecoration:'none',display:'inline-flex',gap:7,alignItems:'center'}}>
              <Icon name="zap" size={14}/>Subscribe now
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

function FeedbackScreen() {
  const CATEGORIES = ['General','Bug report','Feature request','Question'];
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
              <button className="rd-btn" onClick={()=>setSent(false)}>Send another</button>
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
      </div>
    </div>
  );
}

function VodScreen({ clips, me }) {
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
          setJobs(prev=>prev.map(j=>j.id===msg.job_id?{...j,progress:msg.progress,...(msg.vod_title?{vod_title:msg.vod_title,channel:msg.channel,duration:msg.duration,game:msg.game}:{})}:j));
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
          <div className="desc">Paste a Twitch VOD URL and the bot will scan the full chat replay to find highlight moments — no video download needed.</div>
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
            <strong style={{color:'var(--fg-2)'}}>How it works:</strong> The bot downloads the full chat replay for the VOD, then scans it second-by-second using the same scoring engine as live monitoring — chat velocity, keywords, sentiment. When the score crosses the threshold, a moment is found. Each moment links directly to that exact timestamp in the VOD. Clips show up in your review queue automatically.
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

            {job.status==='running' && (
              <div style={{marginBottom:14}}>
                <div style={{display:'flex',justifyContent:'space-between',fontSize:12,color:'var(--fg-2)',marginBottom:6}}>
                  <span>Scanning chat…</span>
                  <span style={{fontVariantNumeric:'tabular-nums'}}>{Math.round(job.progress||0)}%</span>
                </div>
                <div className="rd-track" style={{height:6}}>
                  <div className="rd-fill" style={{width:(job.progress||0)+'%',background:'var(--grad)'}}/>
                </div>
              </div>
            )}

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
          <img src="/static/logo.jpg" alt="Highlightz" style={{height:56,filter:'drop-shadow(0 0 14px rgba(199,155,255,.5))'}}/>
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

function UnderConstruction() {
  return (
    <div style={{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',
                 textAlign:'center',minHeight:'70vh',padding:'40px 24px',gap:22}}>
      <div style={{width:96,height:96,borderRadius:26,display:'grid',placeItems:'center',color:'#53fc18',
                   background:'rgba(83,252,24,.1)',border:'1px solid rgba(83,252,24,.32)',
                   boxShadow:'0 12px 40px -14px rgba(83,252,24,.45)'}}><Icon name="cog" size={44}/></div>
      <div style={{display:'inline-flex',alignItems:'center',gap:9,padding:'7px 16px',borderRadius:999,
                   background:'rgba(83,252,24,.12)',border:'1px solid rgba(83,252,24,.35)',
                   color:'#53fc18',fontWeight:800,fontSize:12.5,letterSpacing:'.14em',textTransform:'uppercase'}}>
        <span style={{width:8,height:8,borderRadius:'50%',background:'#53fc18',boxShadow:'0 0 10px #53fc18'}}/>
        Under Construction
      </div>
      <h1 style={{margin:0,fontSize:38,fontWeight:900,letterSpacing:'-.02em',
                  background:'linear-gradient(135deg,#53fc18,#39b515)',
                  WebkitBackgroundClip:'text',WebkitTextFillColor:'transparent',backgroundClip:'text'}}>
        Kick is coming soon
      </h1>
      <p style={{margin:0,maxWidth:560,fontSize:15.5,lineHeight:1.6,color:'var(--fg-2)'}}>
        We're building fully-automated Kick clipping to the same standard as our Twitch detection.
        It isn't ready yet, so this section is temporarily closed off. In the meantime, switch back to
        <b style={{color:'var(--fg)'}}> Twitch</b> to keep capturing highlights.
      </p>
      <p style={{margin:0,fontSize:13,color:'var(--fg-3)'}}>
        Thanks for your patience — we'll flip this on the moment it's solid.
      </p>
    </div>
  );
}

function RdApp() {
  const [route, setRoute] = useState('review');
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
  const [featuredIds, setFeaturedIds] = useState([]);
  const toastTimer = useRef(null);

  const flash = useCallback(msg => {
    setToast(msg);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(()=>setToast(''), 3000);
  }, []);

  // Single source of truth for loading all live state. Called once on mount and
  // again on every WebSocket (re)connect so the UI fully self-heals after any
  // disconnect (laptop sleep, network blip, server restart on deploy) without a
  // manual page refresh — anything that changed during the gap is pulled fresh.
  const refetchAll = useCallback(()=>{
    Promise.all([fetch('/clips').then(r=>r.json()), fetch('/streams').then(r=>r.json())])
      .then(([ca,sa])=>{
        setClips(Object.fromEntries(ca.map(c=>[c.id,c])));
        setStreams(Object.fromEntries(sa.map(s=>[s.channel,s])));
      }).catch(()=>{});
    fetch('/profiles').then(r=>r.json()).then(arr=>{
      setProfiles(Object.fromEntries(arr.map(p=>[p.channel,p])));
    }).catch(()=>{});
    fetch('/me').then(r=>r.json()).then(data=>setMe(data)).catch(()=>{});
    // Which clips are featured on the landing page (admin curation state).
    fetch('/landing/showcase').then(r=>r.json()).then(d=>setFeaturedIds((d.clips||[]).map(c=>c.id))).catch(()=>{});
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
          // A stream session hit an error and is reconnecting.
          flash(msg.message||'A stream hit an error — reconnecting.');
        }
        // Forward VOD events to VodScreen via custom event
        else if(['vod_progress','vod_moment','vod_done','vod_error'].includes(msg.event)){
          window.dispatchEvent(new CustomEvent('hz_ws',{detail:e.data}));
        }
        // Forward team scoring ticks to the Training screen's live counter
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
  const toggleFeature = async(id)=>{
    const r=await fetch(`/admin/showcase/${id}`,{method:'POST'});
    if(!r.ok){flash('Could not update landing page examples');return;}
    const d=await r.json();
    setFeaturedIds(p=> d.featured ? [...p.filter(x=>x!==id),id] : p.filter(x=>x!==id));
    flash(d.featured ? 'Added to the landing page examples' : 'Removed from the landing page examples');
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

  let screen;
  // Kick is temporarily closed off while automated clipping is built — show a
  // big "under construction" prompt for every platform-specific feature screen.
  // Account and Feedback are global and stay open; Settings shows Twitch-only
  // stats so it's also gated while Kick is under construction.
  if(activePlatform==='kick' && ['review','streams','library','vod','settings'].includes(route)) screen=<UnderConstruction/>;
  else if(route==='review') screen=<ReviewScreen {...{streams:platformStreams,scores,profiles,clips:platformClips,filter,setFilter,activePlatform,onAdd:addStream,onRemove:removeStream,onForce:forceClip,onApprove:approveClip,onReject:rejectClip,onOpen:setModalClip}}/>;
  else if(route==='streams') screen=<StreamsScreen {...{streams:platformStreams,scores,profiles,histories,clips:platformClips,onForce:forceClip}}/>;
  else if(route==='library') screen=<LibraryScreen {...{clips:platformClips,onOpen:setModalClip,onApprove:approveClip,onReject:rejectClip,onDelete:deleteClip}}/>;
  else if(route==='vod') screen=<VodScreen clips={platformClips} me={me}/>;
  else if(route==='training') screen=<TrainingScreen/>;
  else if(route==='account') screen=<AccountScreen me={me}/>;
  else if(route==='feedback') screen=<FeedbackScreen/>;
  else screen=<SettingsScreen {...{streams}}/>;

  return (
    <div className={'rd-app'+(activePlatform==='kick'?' kick-theme':'')} id="rd-app" data-grad="violet" data-density="comfortable" data-glow="on">

      <nav className="rd-nav">
        <span className="logo"><img src="/static/logo.jpg" alt="Highlightz"/></span>
        {NAV.filter(n=>!n.labelerOnly||(me&&(me.is_labeler||me.is_admin))).map(n=>(
          <button key={n.id} className={'rd-navitem'+(route===n.id?' active':'')} onClick={()=>setRoute(n.id)}>
            {n.id==='review'&&pending>0&&<span className="navbadge">{pending}</span>}
            <span className="ic"><Icon name={n.icon} size={22}/></span>
            <span>{n.label}</span>
          </button>
        ))}
        <span className="sp"/>
        <button className="rd-navitem" style={{background:'none',border:'none',cursor:'pointer',color:'inherit'}} title="Sign out" onClick={()=>fetch('/logout',{method:'POST'}).then(()=>{location.href='/login';})}>
          <span className="ic"><Icon name="logout" size={18}/></span>
          <span>Out</span>
        </button>
      </nav>
      <div className="rd-frame">
        <header className="rd-header">
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
