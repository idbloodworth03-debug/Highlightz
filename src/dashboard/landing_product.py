"""Captured surfaces of the real product, for the landing page.

GENERATED FILE. Do not edit. Run scripts/capture_product_ui.mjs to rebuild.

Every string below is DOM that the real dashboard components rendered in a real
browser, plus the stylesheet that painted them. Nothing here was drawn by hand,
which is the whole point: the landing page shows the product because it IS the
product's markup, down to the class names.

The capture is fed seeded data shaped like the real API responses (see the
script for which source file each shape comes from) and is then sanitised:
no images, iframes, videos or scripts, every control turned into a span so it
cannot take focus, and every id namespaced. The CSS is scoped under .pcap so
the dashboard's rules cannot reach the rest of the page, and pruned to the
classes these captures actually use.
"""

# The dashboard's stylesheet, every selector prefixed with .pcap.
PRODUCT_CSS = """.pcap{
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
.pcap *{box-sizing:border-box;margin:0;padding:0}
.pcap,.pcap{height:100%}
.pcap{font-family:var(--font);color:var(--fg);background:var(--rd-bg);-webkit-font-smoothing:antialiased;overflow:hidden}
.pcap button{font-family:inherit;cursor:pointer}
.pcap ::selection{background:rgba(199,155,255,.3)}
.pcap .rd-app{position:relative;height:100vh;display:grid;grid-template-columns:104px 1fr;isolation:isolate}
.pcap .rd-frame{display:grid;grid-template-rows:68px 1fr;min-height:0;overflow:hidden}
.pcap .rd-screen{min-height:0;overflow:hidden;display:flex;flex-direction:column}
.pcap .rd-app::before{content:'';position:fixed;inset:0;z-index:-2;
  background:radial-gradient(900px 480px at 18% -8%,rgba(168,85,247,.20),transparent 60%),
    radial-gradient(760px 420px at 92% 6%,rgba(249,67,255,.13),transparent 55%),
    radial-gradient(700px 600px at 60% 110%,rgba(124,107,255,.12),transparent 60%),var(--rd-bg)}
.pcap .rd-app::after{content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:radial-gradient(120% 120% at 50% 0%,transparent 60%,rgba(0,0,0,.55))}
.pcap .glass{background:var(--panel);border:1px solid var(--hair);-webkit-backdrop-filter:blur(22px) saturate(140%);backdrop-filter:blur(22px) saturate(140%)}
.pcap .rd-header{display:flex;align-items:center;gap:18px;padding:0 22px;border-bottom:1px solid var(--hair);
  background:rgba(10,10,14,.55);-webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px);z-index:5}
.pcap .rd-live{display:inline-flex;align-items:center;gap:7px;font-size:12px;font-weight:600;color:var(--live);
  background:var(--live-soft);padding:6px 12px;border-radius:var(--r-pill);border:1px solid rgba(46,224,138,.25)}
.pcap .rd-live .dot{width:7px;height:7px;border-radius:50%;background:var(--live);animation:ping 2s infinite}
@keyframes ping{0%{box-shadow:0 0 0 0 rgba(46,224,138,.5)}70%{box-shadow:0 0 0 7px rgba(46,224,138,0)}100%{box-shadow:0 0 0 0 rgba(46,224,138,0)}}
@keyframes spin{to{transform:rotate(360deg)}}
.pcap .rd-search{flex:1;max-width:420px;position:relative}
.pcap .rd-search input{width:100%;background:rgba(255,255,255,.04);border:1px solid var(--hair);border-radius:var(--r-pill);
  color:var(--fg);font-size:13px;padding:10px 14px 10px 38px;outline:none;transition:.18s}
.pcap .rd-search input::placeholder{color:var(--fg-3)}
.pcap .rd-search input:focus{border-color:rgba(199,155,255,.5);background:rgba(255,255,255,.06);box-shadow:0 0 0 4px rgba(168,85,247,.12)}
.pcap .rd-search .si{position:absolute;left:13px;top:50%;transform:translateY(-50%);color:var(--fg-3)}
.pcap .rd-header .spacer{flex:1}
.pcap .rd-iconbtn{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;
  background:rgba(255,255,255,.04);border:1px solid var(--hair);color:var(--fg-2);transition:.18s}
.pcap .rd-iconbtn:hover{color:var(--fg);background:rgba(255,255,255,.08)}
.pcap .rd-avatar{width:38px;height:38px;border-radius:50%;background:var(--grad);display:grid;place-items:center;
  font-weight:700;font-size:14px;color:#14021c;border:none;box-shadow:var(--glow)}
.pcap .rd-user-chip{display:flex;align-items:center;gap:10px;padding:4px 12px 4px 4px;border-radius:999px;
  background:rgba(255,255,255,.05);border:1px solid var(--hair)}
.pcap .rd-user-chip img{width:32px;height:32px;border-radius:50%;object-fit:cover}
.pcap .rd-user-chip .uc-init{width:32px;height:32px;border-radius:50%;background:var(--grad);display:grid;
  place-items:center;font-weight:700;font-size:13px;color:#14021c}
.pcap .rd-user-chip .uc-name{font-size:13px;font-weight:600;color:var(--fg-2)}
.pcap .rd-body{display:grid;grid-template-columns:322px 1fr;gap:18px;padding:18px 22px;overflow:hidden;min-height:0}
.pcap .rd-col{min-height:0;display:flex;flex-direction:column;gap:16px}
.pcap .rd-body-full{grid-template-columns:1fr}
.pcap .rd-streampick{cursor:pointer;border-radius:15px;transition:.15s}
.pcap .rd-streampick.on{box-shadow:0 0 0 1px var(--acc-2)}
.pcap .rd-rail{border-radius:var(--r-lg);padding:16px;display:flex;flex-direction:column;gap:16px;overflow:hidden}
.pcap .rd-rail-head{display:flex;align-items:center;justify-content:space-between}
.pcap .rd-eyebrow{font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--fg-3)}
.pcap .rd-count{font-size:11px;font-weight:600;color:var(--fg-2);background:rgba(255,255,255,.05);padding:3px 9px;border-radius:var(--r-pill)}
.pcap .rd-addrow{display:flex;gap:8px}
.pcap .rd-suggwrap{position:relative;flex:1;min-width:0;display:flex}
.pcap .rd-suggwrap .rd-input{width:100%}
.pcap .rd-sugg{position:absolute;top:calc(100% + 6px);left:0;z-index:60;background:#101016;
  border:1px solid var(--hair-2);border-radius:12px;box-shadow:0 14px 36px rgba(0,0,0,.55);
  max-height:320px;overflow-y:auto;overflow-x:hidden;padding:6px;
  
  width:340px;max-width:calc(100vw - 44px)}
.pcap .rd-sugglabel{font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--fg-3);padding:7px 9px 3px}
.pcap .rd-suggitem{display:flex;align-items:center;gap:8px;padding:7px 9px;border-radius:8px;cursor:pointer;font-size:13px;color:var(--fg)}
.pcap .rd-suggitem:hover{background:rgba(255,255,255,.06)}
.pcap .rd-suggitem .meta2{color:var(--fg-3);font-size:11px;margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pcap .rd-sugglive{font-size:9.5px;font-weight:800;letter-spacing:.05em;color:#fff;background:#e91916;border-radius:4px;padding:1px 5px;flex-shrink:0}
.pcap .rd-suggempty{padding:12px 9px;font-size:12.5px;color:var(--fg-3)}
.pcap .rd-sugglabelrow{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 9px 3px}
.pcap .rd-sugglabelrow .rd-sugglabel{padding:0}
.pcap .rd-suggclear{background:none;border:0;cursor:pointer;font:inherit;font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--fg-3);padding:2px 4px;border-radius:5px}
.pcap .rd-suggclear:hover{color:var(--fg);background:rgba(255,255,255,.07)}
.pcap .rd-suggx{margin-left:auto;flex-shrink:0;background:none;border:0;cursor:pointer;color:var(--fg-3);opacity:0;padding:2px;border-radius:5px;display:flex;align-items:center}
.pcap .rd-suggitem:hover .rd-suggx{opacity:1}
.pcap .rd-suggx:focus{opacity:1}
.pcap .rd-suggx:hover{color:var(--fg);background:rgba(255,255,255,.1)}
.pcap .rd-input{flex:1;min-width:0;background:rgba(255,255,255,.04);border:1px solid var(--hair);border-radius:var(--r-md);
  color:var(--fg);font-size:13px;padding:11px 13px;outline:none;transition:.18s}
.pcap .rd-input::placeholder{color:var(--fg-3)}
.pcap .rd-input:focus{border-color:rgba(199,155,255,.5);box-shadow:0 0 0 4px rgba(168,85,247,.1)}
.pcap .rd-select{background:rgba(255,255,255,.04);border:1px solid var(--hair);border-radius:var(--r-md);
  color:var(--fg);font-size:13px;padding:0 10px;outline:none;cursor:pointer}
.pcap .rd-select option{background:#15151c}
.pcap .rd-btn{border:none;border-radius:var(--r-md);padding:11px 16px;font-size:13px;font-weight:600;
  display:inline-flex;align-items:center;justify-content:center;gap:7px;color:#fff;
  background:rgba(255,255,255,.06);border:1px solid var(--hair);transition:.18s;white-space:nowrap}
.pcap .rd-btn:hover{background:rgba(255,255,255,.1)}
.pcap .kick-theme{--acc:#53fc18;--acc-2:#39b515;--grad:linear-gradient(135deg,#53fc18 0%,#39b515 100%);--grad-soft:linear-gradient(135deg,rgba(83,252,24,.14),rgba(57,181,21,.10));--glow:0 0 0 1px rgba(83,252,24,.3),0 8px 30px -6px rgba(57,181,21,.4)}
.pcap .kick-theme .rd-btn.grad{box-shadow:0 6px 18px -6px rgba(83,252,24,.5)}
.pcap .kick-theme .rd-filter.active{box-shadow:0 4px 14px -4px rgba(83,252,24,.5)}
.pcap .kick-theme .rd-navitem.active::before{background:rgba(83,252,24,.1)}
.pcap .kick-theme .rd-navitem.active .ic{color:#53fc18}
.pcap .rd-btn.grad{background:var(--grad);border:none;color:#fff;box-shadow:0 6px 18px -6px rgba(168,85,247,.6)}
.pcap .rd-btn.grad:hover{filter:brightness(1.08);box-shadow:0 8px 24px -6px rgba(168,85,247,.75)}
.pcap .rd-btn.live{background:var(--live);color:#052012;border:none}
.pcap .rd-btn.live:hover{filter:brightness(1.08)}
.pcap .rd-btn.danger{background:var(--danger-soft);color:var(--danger);border:1px solid rgba(255,90,120,.3)}
.pcap .rd-btn.danger:hover{background:rgba(255,90,120,.22)}
.pcap .rd-btn.ghost-force{background:rgba(255,138,76,.14);color:#ff9a52;border:1px solid rgba(255,138,76,.3)}
.pcap .rd-btn.ghost-force:hover{background:rgba(255,138,76,.24)}
.pcap .rd-btn.sm{padding:7px 11px;font-size:12px;border-radius:10px}
.pcap .rd-streams{display:flex;flex-direction:column;gap:10px;overflow-y:auto;padding-right:2px;min-height:0}
.pcap .rd-stream{border-radius:var(--r-md);padding:13px;background:rgba(255,255,255,.025);border:1px solid var(--hair);transition:.18s}
.pcap .rd-stream:hover{border-color:var(--hair-2);background:rgba(255,255,255,.045)}
.pcap .rd-stream-top{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
.pcap .rd-stream-top>div:first-child{min-width:0;flex:1;overflow:hidden}
.pcap .rd-stream .nm{font-size:14px;font-weight:700;letter-spacing:-.01em;display:flex;align-items:center;gap:7px;overflow:hidden}
.pcap .rd-stream .nm .plat{width:6px;height:6px;border-radius:50%;flex-shrink:0;background:var(--acc);box-shadow:0 0 8px var(--acc)}
.pcap .rd-stream .mt{font-size:11px;color:var(--fg-2);margin-top:3px;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.pcap .rd-chip{font-size:10px;font-weight:600;padding:2px 7px;border-radius:var(--r-pill);background:rgba(255,255,255,.06);color:var(--fg-2);text-transform:capitalize}
.pcap .rd-stream-actions{display:flex;gap:6px;align-items:center;flex-shrink:0}
.pcap .rd-x{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;background:transparent;border:none;color:var(--fg-3);transition:.15s}
.pcap .rd-x:hover{color:var(--danger);background:var(--danger-soft)}
.pcap .rd-score{margin-top:12px}
.pcap .rd-score-top{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:7px}
.pcap .rd-score-top .lbl{font-size:11px;color:var(--fg-2);font-weight:500}
.pcap .rd-score-top .val{font-size:20px;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums;line-height:1}
.pcap .rd-track{height:8px;border-radius:var(--r-pill);background:rgba(255,255,255,.07);overflow:hidden;position:relative}
.pcap .rd-fill{height:100%;border-radius:var(--r-pill);transition:background .6s;position:relative}
.pcap .rd-fill::after{content:'';position:absolute;right:0;top:0;bottom:0;width:14px;background:rgba(255,255,255,.5);filter:blur(5px);opacity:.7}
.pcap .rd-thr{position:absolute;top:-2px;bottom:-2px;width:2px;background:rgba(255,255,255,.65);box-shadow:0 0 5px rgba(255,255,255,.45);border-radius:1px;pointer-events:none}
.pcap .rd-track.working::after{content:'';position:absolute;top:0;bottom:0;width:36%;
  background:linear-gradient(90deg,transparent,rgba(199,155,255,.5),transparent);
  animation:rdScan 1.7s ease-in-out infinite;pointer-events:none}
@keyframes rdScan{0%{left:-36%}100%{left:100%}}
.pcap .rd-livedot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--acc);
  margin-right:7px;vertical-align:middle;animation:rdBreathe 1.4s ease-in-out infinite}
@keyframes rdBreathe{0%,100%{opacity:.35;transform:scale(.82)}50%{opacity:1;transform:scale(1)}}
@media(prefers-reduced-motion:reduce){.pcap .rd-track.working::after{animation:none;opacity:.25}
.pcap .rd-livedot{animation:none;opacity:.9}}
.pcap .rd-sigs{display:flex;gap:5px;margin-top:8px;flex-wrap:wrap}
.pcap .rd-sig{font-size:10px;padding:2px 7px;border-radius:6px;background:rgba(255,255,255,.05);color:var(--fg-2);font-variant-numeric:tabular-nums}
.pcap .tr-dim-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:5px}
.pcap .tr-dim-label{font-size:13.5px;font-weight:700}
.pcap .tr-dim-hint{font-size:11.5px;font-weight:500;color:var(--fg-3);margin-left:9px}
.pcap .tr-dim-val{font-size:18px;font-weight:800;color:var(--acc);font-variant-numeric:tabular-nums;min-width:26px;text-align:right}
.pcap .tr-slider{width:100%;height:6px;-webkit-appearance:none;appearance:none;background:rgba(255,255,255,.09);border-radius:99px;outline:none;cursor:pointer}
.pcap .tr-slider::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:18px;height:18px;border-radius:50%;background:linear-gradient(135deg,#f943ff,#a855f7);box-shadow:0 0 10px rgba(168,85,247,.6);cursor:pointer}
.pcap .tr-slider::-moz-range-thumb{width:18px;height:18px;border:none;border-radius:50%;background:linear-gradient(135deg,#f943ff,#a855f7);box-shadow:0 0 10px rgba(168,85,247,.6);cursor:pointer}
.pcap .rd-profile{margin-top:12px;padding:11px;border-radius:var(--r-md);background:rgba(0,0,0,.25);border:1px solid var(--hair)}
.pcap .rd-pgrid{display:grid;grid-template-columns:1fr 1fr;gap:9px 12px}
.pcap .rd-pcell .k{font-size:10px;color:var(--fg-3);text-transform:uppercase;letter-spacing:.06em;font-weight:600}
.pcap .rd-pcell .v{font-size:14px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:2px}
.pcap .rd-learn{margin-top:10px;font-size:10px;font-weight:600;display:flex;align-items:center;gap:6px}
.pcap .rd-learnbar{flex:1;height:4px;border-radius:var(--r-pill);background:rgba(255,255,255,.08);overflow:hidden}
.pcap .rd-learnbar>div{height:100%;background:var(--grad);border-radius:var(--r-pill);transition:width .4s}
.pcap .rd-empty{text-align:center;color:var(--fg-3);font-size:13px;padding:32px 12px;line-height:1.6}
.pcap .rd-empty .ic{color:var(--fg-3);display:flex;justify-content:center;margin-bottom:10px}
.pcap .rd-main{min-height:0;display:flex;flex-direction:column;gap:16px;overflow:hidden}
.pcap .rd-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
.pcap .rd-stat{border-radius:var(--r-lg);padding:16px 18px;position:relative;overflow:hidden}
.pcap .rd-stat .k{font-size:11px;color:var(--fg-2);font-weight:600;letter-spacing:.02em;display:flex;align-items:center;gap:7px}
.pcap .rd-stat .k .si{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;background:var(--grad-soft);color:var(--acc)}
.pcap .rd-stat .v{font-size:30px;font-weight:800;letter-spacing:-.035em;margin-top:10px;font-variant-numeric:tabular-nums;line-height:1}
.pcap .rd-stat .sub{font-size:11px;color:var(--fg-3);margin-top:6px}
.pcap .rd-stat.accent{background:var(--grad-soft);border-color:rgba(199,155,255,.22)}
.pcap .rd-toolbar{display:flex;align-items:center;gap:12px}
.pcap .rd-toolbar h2{font-size:17px;font-weight:700;letter-spacing:-.02em}
.pcap .cull-panel{position:absolute;top:calc(100% + 8px);right:0;z-index:40;width:260px;padding:16px;border-radius:12px;display:flex;flex-direction:column;gap:10px}
.pcap .cull-row{display:flex;justify-content:space-between;align-items:baseline}
.pcap .cull-lbl{font-size:12px;color:var(--fg-2);font-weight:600}
.pcap .cull-val{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums}
.pcap .cull-slider{width:100%;accent-color:var(--acc);cursor:pointer}
.pcap .cull-preview{display:flex;justify-content:space-between;font-size:12px;font-weight:700}
.pcap .plat-switch{position:relative;display:flex;gap:0;background:rgba(255,255,255,.06);border:1px solid var(--hair);border-radius:99px;padding:3px;user-select:none}
.pcap .plat-sw-pill{position:absolute;top:3px;bottom:3px;left:3px;width:calc(50% - 3px);border-radius:99px;pointer-events:none;transition:transform .35s cubic-bezier(.34,1.4,.64,1),background .3s ease,box-shadow .3s ease}
.pcap .plat-sw-pill.kick{transform:translateX(100%);background:#53fc18;box-shadow:0 2px 14px -3px rgba(83,252,24,.7)}
.pcap .plat-sw-pill.twitch{transform:translateX(0);background:#9146ff;box-shadow:0 2px 14px -3px rgba(145,70,255,.7)}
.pcap .plat-sw-btn{position:relative;z-index:1;flex:1;border:none;border-radius:99px;padding:8px 18px;font-size:12px;font-weight:700;cursor:pointer;background:transparent;transition:color .25s ease,transform .12s ease;-webkit-tap-highlight-color:transparent}
.pcap .plat-sw-btn:active{transform:scale(.93)}
.pcap .plat-sw-btn.sw-on-twitch{color:#fff}
.pcap .plat-sw-btn.sw-on-kick{color:#0a0a0e}
.pcap .plat-sw-btn.sw-off{color:var(--fg-2)}
.pcap .rd-filters{display:flex;gap:6px;background:rgba(255,255,255,.04);padding:4px;border-radius:var(--r-pill);border:1px solid var(--hair)}
.pcap .rd-filter{border:none;background:transparent;color:var(--fg-2);font-size:12px;font-weight:600;padding:7px 15px;border-radius:var(--r-pill);transition:.18s}
.pcap .rd-filter:hover{color:var(--fg)}
.pcap .rd-filter.active{color:#fff;background:var(--grad);box-shadow:0 4px 14px -4px rgba(168,85,247,.6)}
.pcap .rd-grid{flex:1;overflow-y:auto;padding-right:4px;display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:18px;align-content:start;align-items:stretch;min-height:0}
.pcap .rd-clip{border-radius:var(--r-lg);overflow:hidden;background:var(--panel);border:1px solid var(--hair);transition:transform .22s cubic-bezier(.4,0,.2,1),border-color .22s,box-shadow .22s;display:flex;flex-direction:column;min-height:360px}
.pcap .rd-clip:hover{transform:translateY(-4px);border-color:rgba(199,155,255,.35);box-shadow:var(--shadow-card)}
.pcap .rd-media{position:relative;width:100%;aspect-ratio:16/9;overflow:hidden}
.pcap .rd-thumb{position:absolute;inset:0}
.pcap .rd-thumb::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 40%,rgba(0,0,0,.55))}
.pcap .rd-media::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 40%,rgba(0,0,0,.55));pointer-events:none;z-index:1}
.pcap .rd-play{position:absolute;inset:0;display:grid;place-items:center;z-index:2}
.pcap .rd-play .ring{width:56px;height:56px;border-radius:50%;display:grid;place-items:center;padding-left:3px;
  background:rgba(20,12,30,.4);border:1.5px solid rgba(255,255,255,.85);color:#fff;backdrop-filter:blur(4px);transition:transform .2s,background .2s}
.pcap .rd-clip:hover .rd-play .ring{transform:scale(1.08);background:var(--grad);border-color:transparent;box-shadow:var(--glow)}
.pcap .rd-scorebadge{position:absolute;top:10px;right:10px;z-index:2;display:inline-flex;align-items:center;gap:5px;
  font-size:12px;font-weight:700;padding:5px 10px;border-radius:var(--r-pill);color:#fff;
  background:rgba(10,8,14,.82);border:1px solid rgba(255,255,255,.16);font-variant-numeric:tabular-nums}
.pcap .rd-scorebadge .pip{width:6px;height:6px;border-radius:50%}
.pcap .rd-viralbadge{position:absolute;top:10px;left:10px;z-index:2;display:inline-flex;align-items:center;gap:5px;
  font-size:11.5px;font-weight:800;padding:5px 10px;border-radius:var(--r-pill);color:#fff;
  background:rgba(10,8,14,.82);border:1px solid rgba(255,255,255,.16);font-variant-numeric:tabular-nums}
.pcap .rd-viralbadge.hot{background:linear-gradient(135deg,#ff7700,#f943ff);border-color:transparent;box-shadow:0 3px 14px -3px rgba(255,119,0,.65)}
.pcap .rd-viralbadge.warm{color:#ffcc5c;border-color:rgba(255,204,92,.35)}
.pcap .rd-clippedbadge{position:absolute;top:38px;left:10px;z-index:2;display:inline-flex;align-items:center;gap:5px;
  font-size:10.5px;font-weight:800;letter-spacing:.02em;padding:3px 8px;border-radius:99px;color:#0b0b12;
  background:linear-gradient(135deg,#3ee08a,#2ee0c8);box-shadow:0 3px 12px -3px rgba(62,224,138,.6)}
.pcap .rd-dur{position:absolute;left:10px;bottom:10px;z-index:2;font-size:11px;font-weight:600;color:#fff;
  background:rgba(10,8,14,.6);padding:3px 8px;border-radius:7px;font-variant-numeric:tabular-nums}
.pcap .rd-clip-body{padding:14px;flex:1;display:flex;flex-direction:column}
.pcap .rd-clip-head{display:flex;align-items:center;justify-content:space-between;gap:8px}
.pcap .rd-clip-ch{font-size:14px;font-weight:700;letter-spacing:-.01em;display:flex;align-items:center;gap:7px}
.pcap .rd-clip-ch .av{width:22px;height:22px;border-radius:7px;background:var(--grad);display:grid;place-items:center;font-size:11px;font-weight:800;color:#1a0322}
.pcap .rd-status{font-size:11px;font-weight:600;padding:4px 10px;border-radius:var(--r-pill);display:inline-flex;align-items:center;gap:5px;text-transform:capitalize}
.pcap .rd-status.pending{background:var(--pending-soft);color:var(--pending)}
.pcap .rd-status.approved{background:var(--live-soft);color:var(--live)}
.pcap .rd-status.rejected{background:var(--danger-soft);color:var(--danger)}
.pcap .rd-clip-title{font-size:13px;color:var(--fg);margin-top:9px;font-weight:500;line-height:1.35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pcap .rd-clip-meta{display:flex;gap:7px;margin-top:10px;flex-wrap:wrap;overflow:hidden;max-height:48px}
.pcap .rd-tag{font-size:11px;color:var(--fg-2);background:rgba(255,255,255,.05);padding:3px 9px;border-radius:var(--r-pill)}
.pcap .rd-clip-actions{display:flex;gap:9px;margin-top:auto;padding-top:14px;flex-wrap:wrap}
.pcap .rd-clip-actions .rd-btn{flex:1}
.pcap .rd-resolved{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--fg-2);padding:4px 0;flex-wrap:wrap}
.pcap .rd-grid-empty{grid-column:1/-1;text-align:center;padding:70px 0;color:var(--fg-3)}
.pcap .rd-grid-empty .ic{display:flex;justify-content:center;margin-bottom:16px;color:var(--fg-3)}
.pcap .rd-grid-empty .big{font-size:18px;font-weight:700;color:var(--fg);margin-bottom:8px;letter-spacing:-.01em}
.pcap .rd-emptylink{display:inline-block;margin-top:16px;font-size:13px;font-weight:600;color:var(--acc);
  padding:8px 15px;border-radius:9px;border:1px solid var(--hair-2);transition:.16s}
.pcap .rd-emptylink:hover{background:rgba(255,255,255,.05);border-color:var(--acc);color:var(--fg)}
.pcap .rd-toast{position:fixed;bottom:26px;left:50%;transform:translate(-50%,90px);opacity:0;
  display:inline-flex;align-items:center;gap:10px;padding:13px 20px;border-radius:var(--r-pill);
  background:rgba(18,14,24,.85);border:1px solid rgba(199,155,255,.35);color:var(--fg);font-size:13px;font-weight:500;
  -webkit-backdrop-filter:blur(20px);backdrop-filter:blur(20px);box-shadow:0 16px 40px -12px rgba(0,0,0,.7);z-index:50;transition:all .35s cubic-bezier(.34,1.56,.64,1)}
.pcap .rd-toast.show{transform:translate(-50%,0);opacity:1}
.pcap .rd-undo{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:80;
  display:flex;align-items:center;gap:11px;padding:11px 14px;border-radius:12px;
  background:rgba(18,18,24,.96);border:1px solid var(--hair-2);
  box-shadow:0 10px 34px rgba(0,0,0,.5);font-size:13px;color:var(--fg);
  -webkit-backdrop-filter:blur(18px);backdrop-filter:blur(18px)}
.pcap .rd-undo .ico{display:flex;color:var(--fg-3)}
.pcap .rd-undo .msg{font-weight:600}
.pcap .rd-undo .act{background:rgba(168,85,247,.18);border:1px solid var(--acc);color:var(--acc);
  font-weight:700;font-size:12px;padding:5px 12px;border-radius:8px;cursor:pointer}
.pcap .rd-undo .act:hover{background:rgba(168,85,247,.3);color:var(--fg)}
.pcap .rd-undo .left{font-size:11px;color:var(--fg-3);font-variant-numeric:tabular-nums;min-width:26px}
.pcap .rd-undo .x{background:none;border:none;color:var(--fg-3);cursor:pointer;font-size:15px;line-height:1;padding:0 2px}
.pcap .rd-undo .x:hover{color:var(--fg)}
@media(max-width:600px){.pcap .rd-undo{left:12px;right:12px;transform:none;justify-content:center}}
.pcap .rd-toast .ico{width:24px;height:24px;border-radius:50%;background:var(--grad);display:grid;place-items:center;color:#fff}
.pcap ::-webkit-scrollbar{width:8px;height:8px}
.pcap ::-webkit-scrollbar-track{background:transparent}
.pcap ::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:99px;border:2px solid transparent;background-clip:padding-box}
.pcap ::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.18);background-clip:padding-box}
.pcap .rd-nav{display:flex;flex-direction:column;align-items:center;gap:6px;padding:16px 0;
  border-right:1px solid var(--hair);background:rgba(10,10,14,.5);
  -webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px);z-index:6}
.pcap .rd-nav .logo{margin-bottom:18px;display:flex}
.pcap .rd-nav .logo img{height:34px;filter:drop-shadow(0 0 12px rgba(199,155,255,.45))}
.pcap .rd-navitem{width:88px;height:64px;border-radius:16px;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:5px;background:transparent;border:none;
  color:var(--fg-3);font-size:11.5px;font-weight:600;letter-spacing:.01em;transition:.16s;position:relative}
.pcap .rd-navitem:hover{color:var(--fg-2);background:rgba(255,255,255,.05)}
.pcap .rd-navitem.blocked{opacity:.32;cursor:not-allowed;filter:saturate(.4)}
.pcap .rd-navitem.blocked:hover{color:var(--fg-3);background:transparent}
.pcap .rd-navitem.active{color:#fff}
.pcap .rd-navitem.active::before{content:'';position:absolute;inset:0;border-radius:16px;
  background:var(--grad-soft);border:1px solid rgba(199,155,255,.3)}
.pcap .rd-navitem.active .ic{color:var(--acc)}
.pcap .rd-navitem .ic,.pcap .rd-navitem span{position:relative;z-index:1}
.pcap .rd-nav .sp{flex:1}
.pcap .rd-menubtn,.pcap .rd-navscrim{display:none}
.pcap .rd-nav .navbadge{position:absolute;top:7px;right:9px;min-width:16px;height:16px;padding:0 4px;
  border-radius:99px;background:var(--grad);color:#fff;font-size:9px;font-weight:800;display:grid;place-items:center;z-index:2}
.pcap .rd-header .htitle{font-size:18px;font-weight:700;letter-spacing:-.02em}
.pcap .rd-header .hsub{font-size:12px;color:var(--fg-3);margin-top:1px}
.pcap .rd-scroll{flex:1;overflow-y:auto;min-height:0;padding:20px 22px}
.pcap .rd-section-title{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.pcap .rd-section-title h2{font-size:19px;font-weight:800;letter-spacing:-.025em}
.pcap .rd-section-title .cnt{font-size:12px;color:var(--fg-3)}
.pcap .rd-streams-layout{display:grid;grid-template-columns:322px 1fr;gap:18px;flex:1;min-height:0;padding:20px 22px}
.pcap .rd-addrow{flex-direction:column}
.pcap .rd-chanlist{display:flex;flex-direction:column;gap:9px;overflow-y:auto;min-height:0;padding-right:2px}
.pcap .rd-chanlist .rd-eyebrow{padding:4px 2px 2px}
.pcap .rd-chan{text-align:left;padding:12px;border-radius:15px;background:rgba(255,255,255,.025);
  border:1px solid var(--hair);transition:.16s;display:flex;align-items:center;gap:11px;width:100%}
.pcap .rd-chan:hover{background:rgba(255,255,255,.05)}
.pcap .rd-chan.active{background:var(--grad-soft);border-color:rgba(199,155,255,.32)}
.pcap .rd-chan .av{width:38px;height:38px;border-radius:12px;background:var(--grad);display:grid;place-items:center;
  font-weight:800;color:#1a0322;font-size:13px;flex-shrink:0}
.pcap .rd-chan .nm{font-weight:700;font-size:14px;letter-spacing:-.01em}
.pcap .rd-chan .mt{font-size:11px;color:var(--fg-2);margin-top:2px}
.pcap .rd-chan .mini{margin-left:auto;font-size:16px;font-weight:800;font-variant-numeric:tabular-nums}
.pcap .rd-detail{display:flex;flex-direction:column;gap:16px;overflow-y:auto;min-height:0;padding-right:4px}
.pcap .rd-detail-head{display:flex;align-items:center;gap:15px}
.pcap .rd-detail-head .av{width:54px;height:54px;border-radius:16px;background:var(--grad);display:grid;place-items:center;
  font-weight:800;color:#1a0322;font-size:19px;box-shadow:var(--glow)}
.pcap .rd-detail-head h2{font-size:23px;font-weight:800;letter-spacing:-.025em}
.pcap .rd-detail-head .mt{font-size:12px;color:var(--fg-2);margin-top:3px;display:flex;gap:7px;align-items:center}
.pcap .rd-detail-head .sp{flex:1}
.pcap .rd-card2{border-radius:18px;padding:18px}
.pcap .rd-chart-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px}
.pcap .rd-chart-head .lbl{font-size:13px;font-weight:600;color:var(--fg-2)}
.pcap .rd-chart-head .big{font-size:30px;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.pcap .rd-chart{width:100%;height:150px;display:block}
.pcap .rd-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.pcap .rd-metric{border-radius:15px;padding:15px}
.pcap .rd-metric .k{font-size:11px;color:var(--fg-2);font-weight:500}
.pcap .rd-metric .v{font-size:24px;font-weight:800;letter-spacing:-.03em;margin-top:7px;font-variant-numeric:tabular-nums}
.pcap .rd-weight{display:flex;align-items:center;gap:12px;margin-bottom:13px}
.pcap .rd-weight:last-child{margin-bottom:0}
.pcap .rd-weight .wl{width:130px;font-size:12px;color:var(--fg-2)}
.pcap .rd-weight .wt{flex:1;height:8px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
.pcap .rd-weight .wf{height:100%;border-radius:99px;background:var(--grad);transition:width .4s}
.pcap .rd-weight .wv{width:46px;text-align:right;font-size:12px;font-weight:700;font-variant-numeric:tabular-nums}
.pcap .rd-settings{max-width:900px;margin:0 auto;display:flex;flex-direction:column;gap:16px;width:100%}
.pcap .rd-card{border-radius:18px;padding:22px}
.pcap .rd-card h3{font-size:15px;font-weight:700;display:flex;align-items:center;gap:10px;letter-spacing:-.01em}
.pcap .rd-card h3 .si{width:30px;height:30px;border-radius:9px;background:var(--grad-soft);color:var(--acc);display:grid;place-items:center}
.pcap .rd-card .desc{font-size:12px;color:var(--fg-3);margin:6px 0 18px 40px}
.pcap .rd-drop{border:2px dashed var(--hair);border-radius:16px;padding:34px 20px;text-align:center;
  cursor:pointer;transition:border-color .18s,background .18s;background:rgba(255,255,255,.015)}
.pcap .rd-drop:hover{border-color:var(--acc-2);background:rgba(168,85,247,.05)}
.pcap .rd-drop.over{border-color:var(--acc);background:rgba(168,85,247,.11)}
.pcap .rd-drop .di{color:var(--acc);margin-bottom:10px}
.pcap .rd-drop .dt{font-size:14px;font-weight:700;margin-bottom:5px}
.pcap .rd-drop .ds{font-size:12px;color:var(--fg-3)}
.pcap .rd-quota{height:7px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden;margin:9px 0 6px}
.pcap .rd-quota i{display:block;height:100%;border-radius:99px;background:var(--grad);transition:width .3s}
.pcap .rd-quota-full i{background:linear-gradient(135deg,#ff5a78,#ff8a4c)}
.pcap .rd-up{border-radius:14px;border:1px solid var(--hair);background:rgba(255,255,255,.02);overflow:hidden;
  display:flex;flex-direction:column}
.pcap .rd-up video{width:100%;aspect-ratio:16/9;background:#000;display:block;object-fit:contain}
.pcap .rd-up .ub{padding:11px 13px;display:flex;align-items:center;gap:10px}
.pcap .rd-up .un{font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.pcap .rd-up .um{font-size:11px;color:var(--fg-3);margin-top:2px}
.pcap .rd-tw{border-radius:14px;border:1px solid var(--hair);background:rgba(255,255,255,.02);
  overflow:hidden;display:flex;flex-direction:column}
.pcap .rd-tw iframe{width:100%;aspect-ratio:16/9;border:none;display:block;background:#000}
.pcap .rd-tw .tw-thumb{position:relative;display:block;width:100%;aspect-ratio:16/9;padding:0;border:none;
  background:#0b0b12;cursor:pointer;overflow:hidden}
.pcap .rd-tw .tw-thumb img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .25s}
.pcap .rd-tw .tw-thumb:hover img{transform:scale(1.04)}
.pcap .rd-tw .tw-noimg{width:100%;height:100%;display:grid;place-items:center;color:var(--fg-3)}
.pcap .rd-tw .tw-play{position:absolute;inset:0;margin:auto;width:40px;height:40px;border-radius:50%;
  display:grid;place-items:center;background:rgba(0,0,0,.55);color:#fff;
  -webkit-backdrop-filter:blur(4px);backdrop-filter:blur(4px);transition:.18s}
.pcap .rd-tw .tw-thumb:hover .tw-play{background:var(--acc-2);transform:scale(1.08)}
.pcap .rd-tw .tw-dur{position:absolute;right:7px;bottom:7px;font-size:10.5px;font-weight:700;color:#fff;
  background:rgba(0,0,0,.7);padding:2px 6px;border-radius:6px}
.pcap .rd-tw .tw-meta{padding:10px 12px;display:flex;flex-direction:column;gap:3px;min-width:0}
.pcap .rd-tw .tw-title{font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pcap .rd-tw .tw-sub{font-size:11px;color:var(--fg-3)}
.pcap .rd-tw .tw-link{font-size:11px;color:var(--acc);font-weight:600;margin-top:2px}
.pcap .rd-tw .tw-link:hover{text-decoration:underline}
.pcap .tw-box{width:min(900px,100%);border-radius:18px;padding:18px}
.pcap .tw-frame{position:relative;width:100%;aspect-ratio:16/9;border-radius:12px;overflow:hidden;background:#000}
.pcap .tw-frame iframe{position:absolute;inset:0;width:100%;height:100%;border:none}
.pcap .ed-bg{position:fixed;inset:0;z-index:200;background:rgba(4,4,8,.86);display:flex;
  align-items:center;justify-content:center;padding:20px;-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px)}
.pcap .ed{width:min(1080px,100%);max-height:94vh;overflow-y:auto;border-radius:20px;padding:20px;
  background:var(--panel);border:1px solid var(--hair)}
.pcap .ed-head{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.pcap .ed-head h3{font-size:16px;font-weight:700;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pcap .ed-body{display:grid;grid-template-columns:1fr 260px;gap:18px}
@media(max-width:820px){.pcap .ed-body{grid-template-columns:1fr}}
.pcap .ed-stage{background:#000;border-radius:14px;overflow:hidden;display:grid;place-items:center;min-height:300px}
.pcap .ed-stage canvas{max-width:100%;max-height:56vh;display:block}
.pcap .ed-side{display:flex;flex-direction:column;gap:16px}
.pcap .ed-grp{display:flex;flex-direction:column;gap:7px}
.pcap .ed-grp label{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--fg-3)}
.pcap .ed-row{display:flex;align-items:center;gap:8px}
.pcap .ed-row input[type=range]{flex:1;accent-color:var(--acc);cursor:pointer}
.pcap .ed-num{font-size:11px;color:var(--fg-3);font-variant-numeric:tabular-nums;min-width:44px;text-align:right}
.pcap .ed-in{width:100%;background:rgba(255,255,255,.05);border:1px solid var(--hair);border-radius:10px;
  padding:8px 10px;color:var(--fg);font-size:13px;font-family:inherit}
.pcap .ed-in:focus{outline:none;border-color:var(--acc-2)}
.pcap .ed-seg{display:flex;gap:6px;flex-wrap:wrap}
.pcap .ed-seg button{flex:1;min-width:64px;padding:7px 9px;border-radius:9px;font-size:11.5px;font-weight:700;
  background:rgba(255,255,255,.05);border:1px solid var(--hair);color:var(--fg-3);cursor:pointer;transition:.15s}
.pcap .ed-seg button.on{background:var(--grad-soft);border-color:rgba(199,155,255,.4);color:#fff}
.pcap .ed-track{position:relative;height:36px;border-radius:10px;background:rgba(255,255,255,.06);
  border:1px solid var(--hair);overflow:hidden;cursor:pointer;margin-top:2px}
.pcap .ed-track .sel{position:absolute;top:0;bottom:0;background:var(--grad-soft);
  border-left:2px solid var(--acc);border-right:2px solid var(--acc)}
.pcap .ed-track .play{position:absolute;top:0;bottom:0;width:3px;margin-left:-1.5px;
  background:#fff;box-shadow:0 0 6px #fff;pointer-events:none}
.pcap .ed-prog{height:6px;border-radius:99px;background:rgba(255,255,255,.08);overflow:hidden}
.pcap .ed-prog i{display:block;height:100%;background:var(--grad);border-radius:99px;transition:width .15s}
.pcap .ed-note{font-size:11px;color:var(--fg-3);line-height:1.5}
.pcap .pub-row{display:flex;align-items:center;gap:9px;margin-top:7px}
.pcap .pub-row .rd-btn{flex-shrink:0;min-width:104px;justify-content:center}
.pcap .pub-ok{font-size:11px;color:var(--acc)}
.pcap .pub-warn{font-size:11px;color:#ff9a52;line-height:1.4}
.pcap .q-row{display:flex;align-items:center;gap:10px;padding:9px 12px;margin-bottom:6px;
  border-radius:12px;background:rgba(255,255,255,.03);border:1px solid var(--hair)}
.pcap .q-row.due{border-color:rgba(168,85,247,.5);background:var(--grad-soft)}
.pcap .q-row.missed{border-color:rgba(255,138,76,.35)}
.pcap .q-when{flex-shrink:0;min-width:74px;font-size:11.5px;font-weight:700;color:var(--acc)}
.pcap .q-row.missed .q-when{color:#ff9a52}
.pcap .q-mid{flex:1;min-width:0}
.pcap .q-name{font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pcap .q-sub{font-size:11px;color:var(--fg-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pcap .sc-list{display:flex;flex-direction:column;gap:14px;margin-top:14px}
.pcap .sc-card{display:flex;gap:16px;padding:16px;border-radius:18px}
@media(max-width:760px){.pcap .sc-card{flex-direction:column}}
.pcap .sc-card.due{border-color:rgba(168,85,247,.55)}
.pcap .sc-card.missed{border-color:rgba(255,138,76,.4)}
.pcap .sc-media{flex-shrink:0;width:184px}
@media(max-width:760px){.pcap .sc-media{width:100%}}
.pcap .sc-media video{width:100%;border-radius:12px;background:#000;aspect-ratio:9/16;object-fit:contain}
.pcap .sc-body{flex:1;min-width:0;display:flex;flex-direction:column;gap:10px}
.pcap .sc-top{display:flex;align-items:center;gap:10px}
.pcap .sc-name{flex:1;min-width:0;font-size:14px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pcap .sc-when{font-size:11.5px;font-weight:800;color:var(--acc);flex-shrink:0}
.pcap .sc-when.missed{color:#ff9a52}
.pcap .sc-plats{display:flex;flex-direction:column;gap:6px}
.pcap .sc-plat{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.pcap .sc-plat .rd-btn{min-width:96px;justify-content:center}
.pcap .sc-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.pcap .sr-tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
@media(max-width:620px){.pcap .sr-tiles{grid-template-columns:repeat(2,1fr)}}
.pcap .sr-tile{background:rgba(255,255,255,.04);border:1px solid var(--hair);
  border-radius:12px;padding:12px;text-align:center}
.pcap .sr-tile .k{font-size:10.5px;color:var(--fg-3);text-transform:uppercase;letter-spacing:.05em}
.pcap .sr-tile .v{font-size:23px;font-weight:800;letter-spacing:-.02em;margin-top:3px}
.pcap .sr-list{margin-top:10px;display:flex;flex-direction:column;gap:7px}
.pcap .sr-row{display:flex;align-items:center;gap:11px}
.pcap .sr-when{flex-shrink:0;width:104px;font-size:11.5px;color:var(--fg-3)}
.pcap .sr-bar{flex:1;height:8px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
.pcap .sr-bar i{display:block;height:100%;background:var(--grad);border-radius:99px}
.pcap .sr-nums{flex-shrink:0;font-size:11.5px;color:var(--fg-2)}
.pcap .rd-lost{display:flex;align-items:center;gap:13px;padding:13px 16px;margin-bottom:14px;
  border-radius:14px;background:rgba(255,138,76,.09);border:1px solid rgba(255,138,76,.3)}
.pcap .rd-lost .ic{flex-shrink:0;color:#ff9a52;display:grid;place-items:center}
.pcap .rd-lost .tx{flex:1;min-width:0;font-size:12.8px;line-height:1.5;color:var(--fg-2)}
.pcap .rd-lost .tx b{color:#ff9a52}
.pcap .rd-lost-x{flex-shrink:0;background:none;border:0;color:var(--fg-3);font-size:22px;
  line-height:1;cursor:pointer;padding:0 2px;transition:color .12s}
.pcap .rd-lost-x:hover{color:var(--fg-1)}
@media(max-width:640px){.pcap .rd-lost{flex-direction:column;align-items:flex-start}}
.pcap .rv{max-width:460px;width:100%;padding:26px 28px;border-radius:20px;
  display:flex;flex-direction:column;gap:12px}
.pcap .rv h3{font-size:19px;font-weight:800;margin:0}
.pcap .rv-sub{font-size:12.5px;color:var(--fg-3);margin:0;line-height:1.5}
.pcap .rv-stars{display:flex;gap:4px;margin:2px 0}
.pcap .rv-star{background:none;border:0;cursor:pointer;font-size:32px;line-height:1;
  padding:0 2px;color:rgba(255,255,255,.2);transition:color .12s}
.pcap .rv-star.on{color:#ffc75a}
.pcap .rv-check{display:flex;align-items:flex-start;gap:8px;font-size:12.5px;
  color:var(--fg-2);cursor:pointer;line-height:1.45}
.pcap .rv-check input{margin-top:2px;flex-shrink:0}
.pcap .rv-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:4px}
.pcap .rv-actions .rd-btn.grad{flex:1 1 120px;justify-content:center}
.pcap .rv-done{display:flex;flex-direction:column;align-items:center;gap:10px;
  padding:22px 0;color:var(--acc);text-align:center}
.pcap .ed-warn{font-size:11.5px;color:#ff9a52;background:rgba(255,138,76,.1);
  border:1px solid rgba(255,138,76,.28);border-radius:10px;padding:8px 10px;line-height:1.45}
.pcap .rd-how{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
@media(max-width:760px){.pcap .rd-how{grid-template-columns:1fr}}
.pcap .rd-step{display:flex;gap:11px;align-items:flex-start;padding:13px 15px;border-radius:14px;
  background:rgba(255,255,255,.025);border:1px solid var(--hair)}
.pcap .rd-step .sn{flex-shrink:0;width:22px;height:22px;border-radius:7px;display:grid;place-items:center;
  background:var(--grad-soft);color:var(--acc);font-size:11.5px;font-weight:800}
.pcap .rd-step .st{font-size:13px;font-weight:700;display:flex;align-items:center;gap:6px;margin-bottom:3px}
.pcap .rd-step .sb{font-size:11.5px;color:var(--fg-3);line-height:1.5}
.pcap .rd-picks{display:flex;gap:8px;flex-wrap:wrap}
.pcap .rd-pick{display:inline-flex;align-items:center;gap:7px;max-width:220px;padding:7px 11px;
  border-radius:99px;background:rgba(255,255,255,.05);border:1px solid var(--hair);
  color:var(--fg-2);font-size:12px;font-weight:600;cursor:pointer;transition:.15s}
.pcap .rd-pick:hover{background:var(--grad-soft);border-color:rgba(199,155,255,.4);color:#fff}
.pcap .rd-pick span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pcap .rd-uprow{display:flex;align-items:center;gap:11px;padding:10px 0;border-bottom:1px solid var(--hair)}
.pcap .rd-uprow:last-child{border-bottom:none}
.pcap .rd-uprow .pb{flex:1;height:6px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
.pcap .rd-uprow .pb i{display:block;height:100%;background:var(--grad);border-radius:99px;transition:width .2s}
.pcap .rd-preset-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.pcap .rd-preset{border-radius:14px;padding:15px;border:1px solid var(--hair);background:rgba(255,255,255,.02)}
.pcap .rd-preset .pn{font-weight:700;font-size:14px;text-transform:capitalize;display:flex;align-items:center;justify-content:space-between}
.pcap .rd-preset .pn .badge2{font-size:10px;font-weight:700;color:var(--acc);background:var(--grad-soft);padding:3px 8px;border-radius:99px}
.pcap .rd-preset .pr{display:flex;justify-content:space-between;font-size:11px;color:var(--fg-2);margin-top:9px}
.pcap .rd-preset .pr b{color:var(--fg);font-weight:700;font-variant-numeric:tabular-nums}
.pcap .rd-field{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 0;border-bottom:1px solid var(--hair)}
.pcap .rd-field:last-child{border-bottom:none;padding-bottom:0}
.pcap .rd-field:first-of-type{padding-top:0}
.pcap .rd-field .fl{font-size:13px;font-weight:500}
.pcap .rd-field .fd{font-size:11px;color:var(--fg-3);margin-top:3px}
.pcap .rd-modal-bg{position:fixed;inset:0;background:rgba(5,4,8,.88);
  z-index:60;display:grid;place-items:center;padding:32px}
.pcap .rd-modal{width:min(900px,100%);max-height:90vh;border-radius:22px;overflow:hidden;display:flex;flex-direction:column;
  box-shadow:var(--shadow-card);background:rgba(16,14,22,.9);border:1px solid var(--hair-2)}
.pcap .rd-modal-media{position:relative;width:100%;padding-bottom:46%;flex-shrink:0}
.pcap .rd-modal-media .thumb{position:absolute;inset:0}
.pcap .rd-modal-media .thumb::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 50%,rgba(0,0,0,.6))}
.pcap .rd-modal-close{position:absolute;top:14px;right:14px;width:36px;height:36px;border-radius:50%;border:none;
  
  background:rgba(10,8,14,.86);color:#fff;display:grid;place-items:center;z-index:2}
.pcap .rd-modal-close:hover{background:rgba(10,8,14,.85)}
.pcap .rd-modal-play{position:absolute;inset:0;display:grid;place-items:center}
.pcap .rd-modal-play .ring{width:76px;height:76px;border-radius:50%;display:grid;place-items:center;padding-left:4px;background:var(--grad);color:#fff;box-shadow:var(--glow)}
.pcap .rd-modal-body{padding:20px 22px;overflow-y:auto}
.pcap .rd-modal-head{display:flex;align-items:center;gap:12px}
.pcap .rd-modal-head .av{width:40px;height:40px;border-radius:12px;background:var(--grad);display:grid;place-items:center;font-weight:800;color:#1a0322}
.pcap .rd-modal-head h3{font-size:17px;font-weight:700;letter-spacing:-.02em}
.pcap .rd-modal-head .mt{font-size:12px;color:var(--fg-2);margin-top:2px}
.pcap .rd-modal-grid{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:20px}
.pcap .rd-sigbar{margin-bottom:12px}
.pcap .rd-sigbar .sh{display:flex;justify-content:space-between;font-size:12px;margin-bottom:5px}
.pcap .rd-sigbar .sh .sk{color:var(--fg-2)}
.pcap .rd-sigbar .sh .sv{font-weight:700;font-variant-numeric:tabular-nums}
.pcap .rd-sigbar .st{height:7px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
.pcap .rd-sigbar .sf{height:100%;border-radius:99px;background:var(--grad)}
.pcap .rd-modal-actions{display:flex;gap:10px;margin-top:8px}
.pcap .rd-modal-actions .rd-btn{flex:1}
.pcap .rd-meta-row{display:flex;justify-content:space-between;font-size:13px;padding:9px 0;border-bottom:1px solid var(--hair)}
.pcap .rd-meta-row:last-child{border-bottom:none}
.pcap .rd-meta-row .mk{color:var(--fg-2)}
.pcap .rd-meta-row .mv{font-weight:600}
@media(max-width:900px){.pcap .rd-body{grid-template-columns:1fr;grid-template-rows:auto 1fr}
.pcap .rd-col{max-height:300px}
.pcap .rd-stats{grid-template-columns:repeat(2,1fr)}
.pcap .rd-streams-layout{grid-template-columns:1fr}
.pcap .rd-metrics{grid-template-columns:repeat(2,1fr)}
.pcap .rd-modal-grid{grid-template-columns:1fr}}
@media(max-width:700px){.pcap{overflow:auto}
.pcap .rd-app{grid-template-columns:1fr;height:auto;min-height:100dvh}
.pcap .rd-frame{min-height:0;overflow:visible}
.pcap .rd-screen{overflow:visible;padding-bottom:16px}
.pcap .rd-navscrim{display:block}
.pcap .rd-nav{position:fixed;top:0;bottom:0;left:0;z-index:60;width:268px;max-width:82vw;
    flex-direction:column;align-items:stretch;gap:4px;padding:18px 12px calc(18px + env(safe-area-inset-bottom));
    border-right:1px solid var(--hair);border-top:none;overflow-y:auto;
    background:#0c0c12;
    transform:translateX(-102%);transition:transform .26s cubic-bezier(.4,0,.2,1);
    box-shadow:0 0 40px rgba(0,0,0,.6)}
.pcap .rd-nav.open{transform:translateX(0)}
.pcap .rd-nav .logo{display:flex;justify-content:center;margin-bottom:14px}
.pcap .rd-nav .sp{flex:1;display:block;min-height:10px}
.pcap .rd-navitem{width:auto;height:auto;min-height:48px;flex-direction:row;justify-content:flex-start;
    align-items:center;gap:12px;padding:0 14px;border-radius:12px;font-size:14px;font-weight:600;text-align:left}
.pcap .rd-navitem .navbadge{position:static;order:3;margin-left:auto}
.pcap .rd-navscrim{position:fixed;inset:0;z-index:59;background:rgba(0,0,0,.55);
    opacity:0;pointer-events:none;transition:opacity .26s ease;-webkit-backdrop-filter:blur(2px);backdrop-filter:blur(2px)}
.pcap .rd-navscrim.open{opacity:1;pointer-events:auto}
.pcap .rd-menubtn{display:inline-flex;align-items:center;justify-content:center;width:38px;height:38px;
    flex-shrink:0;border-radius:11px;background:rgba(255,255,255,.06);border:1px solid var(--hair);
    color:var(--fg);cursor:pointer}
.pcap .rd-frame{grid-template-rows:56px auto}
.pcap .rd-header{padding:0 12px;gap:10px;height:56px}
.pcap .rd-menubtn{display:inline-flex}
.pcap .rd-header .htitle{font-size:15px}
.pcap .rd-header .hsub{display:none}
.pcap .rd-header .rd-live{display:none}
.pcap .rd-body{grid-template-columns:1fr;padding:12px;gap:12px;overflow:visible}
.pcap .rd-col{max-height:none}
.pcap .rd-main{gap:12px;overflow:visible}
.pcap .rd-stats{grid-template-columns:repeat(2,1fr);gap:8px}
.pcap .rd-stat{padding:12px 14px}
.pcap .rd-stat .v{font-size:22px}
.pcap .rd-toolbar{flex-wrap:wrap;gap:8px}
.pcap .rd-filters{margin-left:0;width:100%;justify-content:space-between}
.pcap .rd-filter{flex:1;text-align:center;padding:7px 6px}
.pcap .rd-grid{grid-template-columns:1fr;padding-right:0;overflow:visible}
.pcap .rd-clip{height:auto}
.pcap .rd-streams-layout{grid-template-columns:1fr;padding:12px;gap:12px;overflow:visible}
.pcap .rd-chanlist{overflow-y:visible;max-height:none}
.pcap .rd-detail{overflow-y:visible}
.pcap .rd-metrics{grid-template-columns:repeat(2,1fr);gap:8px}
.pcap .rd-weight .wl{width:90px;font-size:11px}
.pcap .rd-scroll{padding:12px}
.pcap .rd-settings{gap:12px}
.pcap .rd-preset-grid{grid-template-columns:1fr;gap:10px}
.pcap .rd-card{padding:16px}
.pcap .rd-modal-bg{padding:0;align-items:flex-end}
.pcap .rd-modal{width:100%;max-height:92dvh;border-radius:22px 22px 0 0;overflow:hidden}
.pcap .rd-modal-media{padding-bottom:56.25%}
.pcap .rd-modal-body{flex:1;min-height:0;overflow-y:auto;padding:14px 16px}
.pcap .rd-modal-grid{grid-template-columns:1fr;gap:16px}
.pcap .rd-modal-actions{flex-wrap:wrap}
.pcap .rd-user-chip .uc-name{display:none}
.pcap .rd-user-chip{padding:2px;gap:0}
.pcap .rd-toast{bottom:20px;font-size:12px;padding:10px 16px;max-width:90vw;text-align:center}}
.pcap .rd-app::before{content:'';position:fixed;inset:0;z-index:-2;
  background:
    radial-gradient(1050px 560px at 15% -10%,rgba(168,85,247,.26),transparent 62%),
    radial-gradient(860px 500px at 94% 2%,rgba(249,67,255,.16),transparent 58%),
    radial-gradient(940px 720px at 55% 116%,rgba(124,107,255,.16),transparent 62%),
    var(--rd-bg)}
.pcap .rd-app::after{content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;
  background-image:radial-gradient(rgba(255,255,255,.045) 1px,transparent 1px);
  background-size:26px 26px;
  -webkit-mask-image:radial-gradient(1000px 640px at 50% 0%,#000 20%,transparent 78%);
  mask-image:radial-gradient(1000px 640px at 50% 0%,#000 20%,transparent 78%)}
@supports (background:linear-gradient(#000,#000) padding-box) and (color:color-mix(in srgb,#000 50%,#fff)){.pcap .glass{border-color:transparent;
    background:linear-gradient(var(--panel),var(--panel)) padding-box,
      linear-gradient(165deg,color-mix(in srgb,var(--acc-2) 34%,transparent),
        rgba(255,255,255,.075) 30%,rgba(255,255,255,.06) 66%,
        color-mix(in srgb,var(--acc) 26%,transparent)) border-box;
    box-shadow:0 18px 44px -22px rgba(0,0,0,.6)}
.pcap .rd-modal{border-color:transparent;
    background:linear-gradient(rgba(16,14,22,.94),rgba(16,14,22,.94)) padding-box,
      linear-gradient(165deg,color-mix(in srgb,var(--acc-2) 45%,transparent),
        rgba(255,255,255,.1) 34%,rgba(255,255,255,.08) 64%,
        color-mix(in srgb,var(--acc) 34%,transparent)) border-box}
.pcap .rd-toast{border-color:transparent;
    background:linear-gradient(rgba(18,14,24,.88),rgba(18,14,24,.88)) padding-box,
      linear-gradient(120deg,color-mix(in srgb,var(--acc-2) 55%,transparent),
        rgba(255,255,255,.14),color-mix(in srgb,var(--acc) 45%,transparent)) border-box}
.pcap .rd-navitem.active::before{border-color:transparent;
    background:linear-gradient(135deg,color-mix(in srgb,var(--acc-2) 16%,transparent),
        color-mix(in srgb,var(--acc) 11%,transparent)) padding-box,
      linear-gradient(150deg,color-mix(in srgb,var(--acc) 55%,transparent),
        rgba(255,255,255,.1) 45%,color-mix(in srgb,var(--acc-2) 40%,transparent)) border-box}}
.pcap .rd-stat{isolation:isolate}
.pcap .rd-stat::before{content:'';position:absolute;top:-34px;right:-34px;width:120px;height:120px;
  border-radius:50%;z-index:-1;pointer-events:none;
  background:radial-gradient(circle,color-mix(in srgb,var(--acc-2) 20%,transparent),transparent 70%)}
.pcap .rd-btn.grad{position:relative;overflow:hidden}
.pcap .rd-btn.grad::after{content:'';position:absolute;top:0;left:-80%;width:50%;height:100%;
  background:linear-gradient(100deg,transparent,rgba(255,255,255,.34),transparent);
  transform:skewX(-20deg);transition:left .5s ease}
.pcap .rd-btn.grad:hover::after{left:135%}
.pcap .rd-clip:hover{box-shadow:0 26px 54px -20px rgba(0,0,0,.72),
  0 0 44px -16px color-mix(in srgb,var(--acc-2) 55%,transparent)}
.pcap .rd-chan.active,.pcap .rd-stream:hover{box-shadow:0 0 30px -14px color-mix(in srgb,var(--acc-2) 45%,transparent)}
.pcap .rd-header{background:linear-gradient(180deg,rgba(12,11,17,.72),rgba(10,10,14,.5))}
.pcap .rd-nav{background:linear-gradient(180deg,rgba(13,12,19,.66),rgba(10,10,14,.44))}
.pcap .rd-navitem.active .ic{filter:drop-shadow(0 0 9px color-mix(in srgb,var(--acc) 75%,transparent))}
.pcap ::-webkit-scrollbar-thumb{background:color-mix(in srgb,var(--acc-2) 26%,rgba(255,255,255,.08));
  border-radius:99px;border:2px solid transparent;background-clip:padding-box}
.pcap ::-webkit-scrollbar-thumb:hover{background:color-mix(in srgb,var(--acc-2) 44%,rgba(255,255,255,.1));background-clip:padding-box}
@media(prefers-reduced-motion:no-preference){.pcap .rd-nav .logo img{animation:rdLogoGlow 4.5s ease-in-out infinite alternate}
@keyframes rdLogoGlow{from{filter:drop-shadow(0 0 9px rgba(199,155,255,.4))}
    to{filter:drop-shadow(0 0 17px rgba(199,155,255,.75))}}
.pcap .rd-screen{animation:rdScreenIn .4s cubic-bezier(.16,1,.3,1)}
@keyframes rdScreenIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.pcap .rd-grid .rd-clip{animation:rdCardIn .5s cubic-bezier(.16,1,.3,1) backwards}
.pcap .rd-grid .rd-clip:nth-child(2){animation-delay:.05s}
.pcap .rd-grid .rd-clip:nth-child(3){animation-delay:.1s}
.pcap .rd-grid .rd-clip:nth-child(4){animation-delay:.15s}
.pcap .rd-grid .rd-clip:nth-child(5){animation-delay:.2s}
.pcap .rd-grid .rd-clip:nth-child(6){animation-delay:.25s}
@keyframes rdCardIn{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}}
@media(max-width:700px){.pcap .cull-panel{position:fixed;bottom:66px;left:10px;right:10px;width:auto;top:auto;z-index:50}
.pcap,.pcap{overflow-x:hidden}
.pcap .rd-app{grid-template-columns:minmax(0,1fr)}
.pcap .rd-body{grid-template-columns:minmax(0,1fr)}
.pcap .rd-streams-layout{grid-template-columns:minmax(0,1fr)}
.pcap .rd-frame,.pcap .rd-screen,.pcap .rd-main,.pcap .rd-col,.pcap .rd-rail{min-width:0}
.pcap .rd-frame{grid-template-rows:auto 1fr}
.pcap .rd-header{flex-wrap:wrap;height:auto;min-height:0;padding:10px 12px;gap:8px 10px}
.pcap .rd-header>*{min-width:0}
.pcap .rd-header .htitle{font-size:16px}
.pcap .rd-header .hsub{display:none}
.pcap .plat-sw-btn{padding:7px 13px;font-size:11px}
.pcap .rd-live{font-size:11px;padding:5px 9px}
.pcap .rd-stats{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.pcap .rd-stat{padding:12px 13px}
.pcap .rd-stat .k{white-space:normal;line-height:1.3}
.pcap .rd-stat .v{font-size:24px}
.pcap .rd-toolbar{flex-wrap:wrap;gap:8px}
.pcap .rd-addrow{flex-wrap:wrap}
.pcap .rd-addrow .rd-input{flex:1 1 100%}
.pcap .rd-addrow .rd-suggwrap{flex:1 1 100%}
.pcap .rd-addrow .rd-select{flex:1}
.pcap .rd-grid{grid-template-columns:1fr}
.pcap .rd-body,.pcap .rd-scroll,.pcap .rd-streams-layout{padding-bottom:18px}
.pcap .rd-detail,.pcap .rd-chanlist{padding-bottom:18px}
.pcap .rd-nav{background:#0c0c12}
.pcap .wm-card{padding:26px 20px !important;border-radius:18px !important}
.pcap .rd-toolbar>div{flex-wrap:wrap;min-width:0}
.pcap .rd-filters{width:auto;max-width:100%;flex-wrap:wrap}
.pcap .rd-filter{flex:1 1 auto}
.pcap .rd-section-title{flex-wrap:wrap}
.pcap .rd-clip-head{min-width:0}
.pcap .rd-clip-ch{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pcap .rd-detail-head{flex-wrap:wrap}
.pcap .rd-detail-head>div{min-width:0}
.pcap .rd-detail-head h2{font-size:19px;word-break:break-word}}"""

# Live Streams: the channel list and the monitor panel.
STREAMS_HTML = """<div class="rd-streams-layout"><aside class="rd-col" style="min-height: 0px;"><div class="rd-rail glass" style="flex: 0 0 auto; overflow: visible; position: relative; z-index: 5;"><div class="rd-eyebrow">Add a stream</div><div class="rd-addrow"><div class="rd-suggwrap"><input class="rd-input" placeholder="search a streamer" value=""></div><select class="rd-select"><option value="default">Default</option><option value="small">Small streamer</option><option value="fps">FPS</option><option value="moba">MOBA</option><option value="chess">Chess / Strategy</option><option value="casino">Casino / Gambling</option><option value="irl">IRL / Outdoor</option><option value="variety">Variety / Just Chatting</option><option value="sports">Sports</option></select></div><div style="display: flex; align-items: center; gap: 6px; margin-top: 6px; padding: 5px 10px; border-radius: 8px; background: rgba(255, 255, 255, 0.04); border: 1px solid var(--hair);"><span style="width: 7px; height: 7px; border-radius: 50%; background: var(--acc); box-shadow: 0 0 6px var(--acc); flex-shrink: 0;"></span><span style="font-size: 12px; font-weight: 600; color: var(--fg-2); text-transform: capitalize;">twitch</span></div><span class="rd-btn grad" style="margin-top: 8px;"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M5 12h14"></path><path d="M12 5v14"></path></svg>Monitor stream</span></div><div class="rd-rail glass" style="flex: 1 1 0%; min-height: 0px;"><div class="rd-rail-head"><span class="rd-eyebrow">Monitored streams</span><span class="rd-count">5</span></div><div class="rd-streams"><div class="rd-streampick on"><div class="rd-stream"><div class="rd-stream-top"><div><div class="nm"><span class="plat" style="background: var(--acc); box-shadow: 0 0 8px var(--acc);"></span>jynxzi</div><div class="mt"><span class="rd-chip">twitch</span><span class="rd-chip">fps</span><span style="color: var(--live); font-weight: 600;">live</span></div></div><div class="rd-stream-actions"><span class="rd-btn ghost-force sm"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>Clip</span><span class="rd-x" title="Remove"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg></span></div></div><div class="rd-score"><div class="rd-score-top"><span class="lbl">Trigger score</span><span class="val" style="color: var(--pending);">61.7</span></div><div class="rd-track"><div class="rd-fill" style="width: 61.7%; background: var(--pending);"></div><div class="rd-thr" title="Fires at 51" style="left: 51%;"></div></div><div class="rd-sigs" style="margin-top: 6px;"><span class="rd-sig" style="color: rgb(134, 239, 172);">● engine live</span><span class="rd-sig" style="color: rgb(134, 239, 172);">CHAT 1.1/s (base 1.95) · last msg just now</span><span class="rd-sig" style="color: var(--fg-2);">fires at 51</span></div><div class="rd-sigs"><span class="rd-sig" style="background: rgba(168, 85, 247, 0.18); color: var(--fg-1);">CHAT_VELOCITY: 0.38</span><span class="rd-sig">EMOTE_HOMOGENEITY: 0.00</span><span class="rd-sig" style="background: rgba(168, 85, 247, 0.18); color: var(--fg-1);">AUDIO_SPIKE: 0.09</span><span class="rd-sig">KEYWORD: 0.00</span><span class="rd-sig" style="background: rgba(168, 85, 247, 0.18); color: var(--fg-1);">SENTIMENT: 1.00</span><span class="rd-sig" style="background: rgba(168, 85, 247, 0.18); color: var(--fg-1);">VIEWER_SPIKE: 0.13</span><span class="rd-sig">SILENCE_BURST: 0.00</span></div><div class="rd-sigs" style="margin-top: 4px;"><span class="rd-sig" style="color: var(--fg-3);">AUDIO -63.2dB peak -63.2dB (base -64.5dB)</span><span class="rd-sig" style="color: var(--fg-2);">VIEWERS 9599 (base 9012)</span></div></div><div class="rd-profile"><div class="rd-pgrid"><div class="rd-pcell"><div class="k">Threshold</div><div class="v">51</div></div><div class="rd-pcell"><div class="k">Velocity</div><div class="v">1.6<span style="font-size: 10px; color: var(--fg-3); font-weight: 500;"> m/s</span></div></div><div class="rd-pcell"><div class="k">Clips</div><div class="v">552</div></div><div class="rd-pcell"><div class="k">Approval</div><div class="v" style="color: var(--danger);">15%</div></div></div><div class="rd-learn" style="color: var(--live);"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="20 6 9 17 4 12"></polyline></svg>Calibrated · 32016 samples</div></div></div></div><div class="rd-streampick"><div class="rd-stream"><div class="rd-stream-top"><div><div class="nm"><span class="plat" style="background: var(--acc); box-shadow: 0 0 8px var(--acc);"></span>lacy</div><div class="mt"><span class="rd-chip">twitch</span><span class="rd-chip">default</span><span style="color: var(--live); font-weight: 600;">live</span></div></div><div class="rd-stream-actions"><span class="rd-btn ghost-force sm"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>Clip</span><span class="rd-x" title="Remove"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg></span></div></div><div class="rd-score"><div class="rd-score-top"><span class="lbl">Trigger score</span><span class="val" style="color: var(--acc);">34.1</span></div><div class="rd-track"><div class="rd-fill" style="width: 34.1%; background: var(--grad);"></div><div class="rd-thr" title="Fires at 60" style="left: 60%;"></div></div><div class="rd-sigs" style="margin-top: 6px;"><span class="rd-sig" style="color: rgb(134, 239, 172);">● engine live</span><span class="rd-sig" style="color: rgb(134, 239, 172);">CHAT 2.6/s (base 2.63) · last msg just now</span><span class="rd-sig" style="color: var(--fg-2);">fires at 60</span></div><div class="rd-sigs"><span class="rd-sig" style="background: rgba(168, 85, 247, 0.18); color: var(--fg-1);">CHAT_VELOCITY: 0.21</span><span class="rd-sig">EMOTE_HOMOGENEITY: 0.00</span><span class="rd-sig">AUDIO_SPIKE: 0.04</span><span class="rd-sig">KEYWORD: 0.00</span><span class="rd-sig" style="background: rgba(168, 85, 247, 0.18); color: var(--fg-1);">SENTIMENT: 0.62</span><span class="rd-sig">VIEWER_SPIKE: 0.00</span><span class="rd-sig">SILENCE_BURST: 0.00</span></div><div class="rd-sigs" style="margin-top: 4px;"><span class="rd-sig" style="color: var(--fg-3);">AUDIO -54.9dB peak -54.9dB (base -63.4dB)</span><span class="rd-sig" style="color: var(--fg-2);">VIEWERS 2411 (base 2380)</span></div></div><div class="rd-profile"><div class="rd-pgrid"><div class="rd-pcell"><div class="k">Threshold</div><div class="v">60</div></div><div class="rd-pcell"><div class="k">Velocity</div><div class="v">2.6<span style="font-size: 10px; color: var(--fg-3); font-weight: 500;"> m/s</span></div></div><div class="rd-pcell"><div class="k">Clips</div><div class="v">70</div></div><div class="rd-pcell"><div class="k">Approval</div><div class="v" style="color: var(--danger);">30%</div></div></div><div class="rd-learn" style="color: var(--live);"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="20 6 9 17 4 12"></polyline></svg>Calibrated · 5844 samples</div></div></div></div><div class="rd-streampick"><div class="rd-stream"><div class="rd-stream-top"><div><div class="nm"><span class="plat" style="background: var(--acc); box-shadow: 0 0 8px var(--acc);"></span>jasontheween</div><div class="mt"><span class="rd-chip">twitch</span><span class="rd-chip">irl</span><span style="color: var(--live); font-weight: 600;">live</span></div></div><div class="rd-stream-actions"><span class="rd-btn ghost-force sm"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>Clip</span><span class="rd-x" title="Remove"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg></span></div></div><div class="rd-score"><div class="rd-score-top"><span class="lbl">Trigger score</span><span class="val" style="color: var(--pending);">66.9</span></div><div class="rd-track"><div class="rd-fill" style="width: 66.9%; background: var(--pending);"></div><div class="rd-thr" title="Fires at 60" style="left: 60%;"></div></div><div class="rd-sigs" style="margin-top: 6px;"><span class="rd-sig" style="color: rgb(134, 239, 172);">● engine live</span><span class="rd-sig" style="color: rgb(134, 239, 172);">CHAT 3.9/s (base 3.13) · last msg just now</span><span class="rd-sig" style="color: var(--fg-2);">fires at 60</span></div><div class="rd-sigs"><span class="rd-sig" style="background: rgba(168, 85, 247, 0.18); color: var(--fg-1);">CHAT_VELOCITY: 0.55</span><span class="rd-sig" style="background: rgba(168, 85, 247, 0.18); color: var(--fg-1);">EMOTE_HOMOGENEITY: 0.12</span><span class="rd-sig" style="background: rgba(168, 85, 247, 0.18); color: var(--fg-1);">AUDIO_SPIKE: 0.31</span><span class="rd-sig" style="background: rgba(168, 85, 247, 0.18); color: var(--fg-1);">KEYWORD: 0.08</span><span class="rd-sig" style="background: rgba(168, 85, 247, 0.18); color: var(--fg-1);">SENTIMENT: 0.74</span><span class="rd-sig" style="background: rgba(168, 85, 247, 0.18); color: var(--fg-1);">VIEWER_SPIKE: 0.22</span><span class="rd-sig">SILENCE_BURST: 0.00</span></div><div class="rd-sigs" style="margin-top: 4px;"><span class="rd-sig" style="color: rgb(134, 239, 172);">AUDIO -48.1dB peak -44.2dB (base -52dB)</span><span class="rd-sig" style="color: var(--fg-2);">VIEWERS 14208 (base 13740)</span></div></div><div class="rd-profile"><div class="rd-pgrid"><div class="rd-pcell"><div class="k">Threshold</div><div class="v">60</div></div><div class="rd-pcell"><div class="k">Velocity</div><div class="v">3.1<span style="font-size: 10px; color: var(--fg-3); font-weight: 500;"> m/s</span></div></div><div class="rd-pcell"><div class="k">Clips</div><div class="v">135</div></div><div class="rd-pcell"><div class="k">Approval</div><div class="v" style="color: var(--danger);">23%</div></div></div><div class="rd-learn" style="color: var(--live);"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="20 6 9 17 4 12"></polyline></svg>Calibrated · 1078 samples</div></div></div></div><div class="rd-streampick"><div class="rd-stream"><div class="rd-stream-top"><div><div class="nm"><span class="plat" style="background: var(--acc); box-shadow: 0 0 8px var(--acc);"></span>stableronaldo</div><div class="mt"><span class="rd-chip">twitch</span><span class="rd-chip">default</span><span style="color: var(--fg-2); font-weight: 600;">offline</span></div></div><div class="rd-stream-actions"><span class="rd-btn ghost-force sm"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>Clip</span><span class="rd-x" title="Remove"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg></span></div></div><div class="rd-score"><div class="rd-score-top"><span class="lbl">Trigger score</span><span class="val" style="color: var(--acc);">0.0</span></div><div class="rd-track"><div class="rd-fill" style="width: 0%; background: var(--grad);"></div></div><div class="rd-sigs" style="margin-top: 6px;"><span class="rd-sig" style="color: var(--fg-3);">● engine — waiting for first update…</span></div><div class="rd-sigs"></div><div class="rd-sigs" style="margin-top: 4px;"></div></div><div class="rd-profile"><div class="rd-pgrid"><div class="rd-pcell"><div class="k">Threshold</div><div class="v">60</div></div><div class="rd-pcell"><div class="k">Velocity</div><div class="v">5.6<span style="font-size: 10px; color: var(--fg-3); font-weight: 500;"> m/s</span></div></div><div class="rd-pcell"><div class="k">Clips</div><div class="v">221</div></div><div class="rd-pcell"><div class="k">Approval</div><div class="v" style="color: var(--danger);">8%</div></div></div><div class="rd-learn" style="color: var(--live);"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="20 6 9 17 4 12"></polyline></svg>Calibrated · 19802 samples</div></div></div></div><div class="rd-streampick"><div class="rd-stream"><div class="rd-stream-top"><div><div class="nm"><span class="plat" style="background: var(--acc); box-shadow: 0 0 8px var(--acc);"></span>theburntpeanut</div><div class="mt"><span class="rd-chip">twitch</span><span class="rd-chip">default</span><span style="color: var(--pending); font-weight: 600;">reconnecting</span></div></div><div class="rd-stream-actions"><span class="rd-btn ghost-force sm"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>Clip</span><span class="rd-x" title="Remove"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg></span></div></div><div class="rd-score"><div class="rd-score-top"><span class="lbl">Trigger score</span><span class="val" style="color: var(--acc);">39.7</span></div><div class="rd-track"><div class="rd-fill" style="width: 39.7%; background: var(--grad);"></div><div class="rd-thr" title="Fires at 63" style="left: 63%;"></div></div><div class="rd-sigs" style="margin-top: 6px;"><span class="rd-sig" style="color: rgb(134, 239, 172);">● engine live</span><span class="rd-sig" style="color: rgb(134, 239, 172);">CHAT 0.9/s (base 1.99) · last msg 4s ago</span><span class="rd-sig" style="color: var(--fg-2);">fires at 63</span></div><div class="rd-sigs"><span class="rd-sig" style="background: rgba(168, 85, 247, 0.18); color: var(--fg-1);">CHAT_VELOCITY: 0.18</span><span class="rd-sig">EMOTE_HOMOGENEITY: 0.00</span><span class="rd-sig">AUDIO_SPIKE: 0.02</span><span class="rd-sig">KEYWORD: 0.00</span><span class="rd-sig" style="background: rgba(168, 85, 247, 0.18); color: var(--fg-1);">SENTIMENT: 0.41</span><span class="rd-sig">VIEWER_SPIKE: 0.00</span><span class="rd-sig">SILENCE_BURST: 0.00</span></div><div class="rd-sigs" style="margin-top: 4px;"><span class="rd-sig" style="color: var(--fg-3);">AUDIO -57.7dB peak -57.7dB (base -59.1dB)</span><span class="rd-sig" style="color: var(--fg-2);">VIEWERS 812 (base 803)</span></div></div><div class="rd-profile"><div class="rd-pgrid"><div class="rd-pcell"><div class="k">Threshold</div><div class="v">63</div></div><div class="rd-pcell"><div class="k">Velocity</div><div class="v">2.0<span style="font-size: 10px; color: var(--fg-3); font-weight: 500;"> m/s</span></div></div><div class="rd-pcell"><div class="k">Clips</div><div class="v">47</div></div><div class="rd-pcell"><div class="k">Approval</div><div class="v" style="color: var(--danger);">19%</div></div></div><div class="rd-learn" style="color: var(--live);"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="20 6 9 17 4 12"></polyline></svg>Calibrated · 4546 samples</div></div></div></div></div></div></aside><div class="rd-detail"><div class="rd-detail-head"><span class="av">JY</span><div><h2>jynxzi</h2><div class="mt"><span class="rd-chip">twitch</span><span class="rd-chip">fps</span><span style="color: var(--live); font-weight: 600;">● live</span></div></div><div class="sp"></div><span class="rd-btn ghost-force"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>Force clip</span></div><div class="rd-card2 glass"><div class="rd-chart-head"><span class="lbl">Trigger score · live</span><span class="big" style="color: var(--pending);">61.7</span></div><svg class="rd-chart" viewBox="0 0 600 150" preserveAspectRatio="none"><defs><linearGradient id="pcap-streams-cg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="rgba(168,85,247,.5)"></stop><stop offset="1" stop-color="rgba(168,85,247,0)"></stop></linearGradient></defs><line x1="0" x2="600" y1="109.5" y2="109.5" stroke="rgba(255,255,255,.05)" stroke-width="1"></line><line x1="0" x2="600" y1="75" y2="75" stroke="rgba(255,255,255,.05)" stroke-width="1"></line><line x1="0" x2="600" y1="40.5" y2="40.5" stroke="rgba(255,255,255,.05)" stroke-width="1"></line><path d="M6.0 144.0 L594.0 144.0 L 594 150 L 6 150 Z" fill="url(#cg)"></path><path d="M6.0 144.0 L594.0 144.0" fill="none" stroke="#c79bff" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"></path><circle cx="594" cy="144" r="3.5" fill="#fff"></circle></svg></div><div class="rd-metrics"><div class="rd-metric glass"><div class="k">Threshold</div><div class="v">51</div></div><div class="rd-metric glass"><div class="k">Avg velocity</div><div class="v">1.6<span style="font-size: 11px; color: var(--fg-3);"> m/s</span></div></div><div class="rd-metric glass"><div class="k">Approval rate</div><div class="v" style="color: var(--danger);">15%</div></div><div class="rd-metric glass"><div class="k">Total clips</div><div class="v">552</div></div></div><div class="rd-card2 glass"><h3 style="font-size: 14px; font-weight: 700; margin-bottom: 16px; display: flex; align-items: center; gap: 9px;"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0; color: var(--acc);"><line x1="4" x2="4" y1="21" y2="14"></line><line x1="4" x2="4" y1="10" y2="3"></line><line x1="12" x2="12" y1="21" y2="12"></line><line x1="12" x2="12" y1="8" y2="3"></line><line x1="20" x2="20" y1="21" y2="16"></line><line x1="20" x2="20" y1="12" y2="3"></line><line x1="2" x2="6" y1="14" y2="14"></line><line x1="10" x2="14" y1="8" y2="8"></line><line x1="18" x2="22" y1="16" y2="16"></line></svg>Learned signal weights</h3><div class="rd-weight"><span class="wl">Chat velocity</span><span class="wt"><span class="wf" style="width: 40%;"></span></span><span class="wv" style="color: var(--fg);">1.00x</span></div><div class="rd-weight"><span class="wl">Keyword</span><span class="wt"><span class="wf" style="width: 40%;"></span></span><span class="wv" style="color: var(--fg);">1.00x</span></div><div class="rd-weight"><span class="wl">Sentiment</span><span class="wt"><span class="wf" style="width: 40%;"></span></span><span class="wv" style="color: var(--fg);">1.00x</span></div><div class="rd-weight"><span class="wl">Audio spike</span><span class="wt"><span class="wf" style="width: 40%;"></span></span><span class="wv" style="color: var(--fg);">1.00x</span></div><div class="rd-weight"><span class="wl">Viewer spike</span><span class="wt"><span class="wf" style="width: 40%;"></span></span><span class="wv" style="color: var(--fg);">1.00x</span></div><div class="rd-weight"><span class="wl">Silence burst</span><span class="wt"><span class="wf" style="width: 40%;"></span></span><span class="wv" style="color: var(--fg);">1.00x</span></div></div><div><div class="rd-eyebrow" style="margin-bottom: 12px;">Recent clips · jynxzi</div><div class="rd-grid" style="overflow: visible; padding-right: 0px;"><div class="rd-clip"><div class="rd-media" style="cursor: pointer;"><div class="rd-thumb" style="background: linear-gradient(135deg, rgb(87, 50, 25), rgb(42, 45, 11));"></div><div class="rd-play"><span class="ring"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none"></polygon></svg></span></div><span class="rd-scorebadge"><span class="pip" style="background: var(--pending);"></span>62%</span><span class="rd-viralbadge warm" title="Virality — how shareable this moment looks"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline><polyline points="16 7 22 7 22 13"></polyline></svg>44% viral</span><span class="rd-dur">30s</span></div><div class="rd-clip-body"><div class="rd-clip-head"><span class="rd-clip-ch"><span class="av">JY</span>jynxzi</span><span class="rd-status pending">pending</span></div><div class="rd-clip-title">jynxzi — Sub/Raid Hype</div><div class="rd-clip-meta"><span class="rd-tag">12:16 AM</span><span class="rd-tag">Rainbow Six Siege</span></div><div class="rd-clip-actions"><span class="rd-btn live sm"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="20 6 9 17 4 12"></polyline></svg>Approve</span><span class="rd-btn danger sm"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>Reject</span></div></div></div><div class="rd-clip"><div class="rd-media" style="cursor: pointer;"><div class="rd-thumb" style="background: linear-gradient(135deg, rgb(87, 50, 25), rgb(42, 45, 11));"></div><div class="rd-play"><span class="ring"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none"></polygon></svg></span></div><span class="rd-scorebadge"><span class="pip" style="background: var(--live);"></span>82%</span><span class="rd-viralbadge warm" title="Virality — how shareable this moment looks"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline><polyline points="16 7 22 7 22 13"></polyline></svg>50% viral</span><span class="rd-dur">30s</span></div><div class="rd-clip-body"><div class="rd-clip-head"><span class="rd-clip-ch"><span class="av">JY</span>jynxzi</span><span class="rd-status approved">approved</span></div><div class="rd-clip-title">jynxzi — Everything Pops Off At Once</div><div class="rd-clip-meta"><span class="rd-tag">12:00 AM</span><span class="rd-tag">Rainbow Six Siege</span></div><div class="rd-clip-actions"></div></div></div><div class="rd-clip"><div class="rd-media" style="cursor: pointer;"><div class="rd-thumb" style="background: linear-gradient(135deg, rgb(87, 50, 25), rgb(42, 45, 11));"></div><div class="rd-play"><span class="ring"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none"></polygon></svg></span></div><span class="rd-scorebadge"><span class="pip" style="background: var(--pending);"></span>71%</span><span class="rd-viralbadge warm" title="Virality — how shareable this moment looks"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline><polyline points="16 7 22 7 22 13"></polyline></svg>47% viral</span><span class="rd-dur">30s</span></div><div class="rd-clip-body"><div class="rd-clip-head"><span class="rd-clip-ch"><span class="av">JY</span>jynxzi</span><span class="rd-status approved">approved</span></div><div class="rd-clip-title">jynxzi — Chat Calls For The Clip</div><div class="rd-clip-meta"><span class="rd-tag">11:31 PM</span><span class="rd-tag">Rainbow Six Siege</span></div><div class="rd-clip-actions"></div></div></div></div></div></div></div>"""
STREAMS_BOX   = (1496, 1032)
STREAMS_BOX_M = (430, 4054)

# Clip Review: the queue, its counters and its filters.
REVIEW_HTML = """<section class="rd-main"><div class="rd-stats"><div class="rd-stat glass accent"><div class="k"><span class="si"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"></path><path d="M20 3v4"></path><path d="M22 5h-4"></path><path d="M4 17v2"></path><path d="M5 18H3"></path></svg></span>Pending review</div><div class="v">5</div><div class="sub">awaiting your call</div></div><div class="rd-stat glass"><div class="k"><span class="si"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="20 6 9 17 4 12"></polyline></svg></span>Approved</div><div class="v">2</div><div class="sub">ready to use</div></div><div class="rd-stat glass"><div class="k"><span class="si"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"></path><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"></path><circle cx="12" cy="12" r="2"></circle><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"></path><path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"></path></svg></span>Active streams</div><div class="v">5</div><div class="sub">monitored live</div></div><div class="rd-stat glass"><div class="k"><span class="si"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline><polyline points="16 7 22 7 22 13"></polyline></svg></span>Avg trigger</div><div class="v">40</div><div class="sub">across all channels</div></div></div><div class="rd-toolbar"><h2>Clip review</h2><div style="display: flex; gap: 8px; align-items: center; margin-left: auto;"><div style="position: relative;"><span class="rd-btn sm" style="background: rgba(255, 255, 255, 0.06); border-width: 1px; border-style: solid; border-color: var(--hair); border-image: initial; color: var(--fg-2);"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"></path><path d="M20 3v4"></path><path d="M22 5h-4"></path><path d="M4 17v2"></path><path d="M5 18H3"></path></svg>Cull clips</span></div><span class="rd-btn sm" title="Empty the review queue without rejecting anything" style="background: rgba(255, 255, 255, 0.06); border: 1px solid var(--hair); color: var(--fg-2);"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M3 6h18"></path><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path></svg>Clear queue</span><select class="rd-select" title="Filter by streamer" style="padding: 6px 10px; font-size: 12px; font-weight: 600;"><option value="all">All streamers</option><option value="jasontheween">jasontheween</option><option value="jynxzi">jynxzi</option><option value="lacy">lacy</option><option value="stableronaldo">stableronaldo</option><option value="theburntpeanut">theburntpeanut</option></select><div class="rd-filters"><span class="rd-filter active">All</span><span class="rd-filter">Pending</span><span class="rd-filter">Approved</span></div><div class="rd-filters"><span class="rd-filter active" title="Sort by date added"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg> Newest</span><span class="rd-filter" title="Sort by virality score"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline><polyline points="16 7 22 7 22 13"></polyline></svg> Top Virality</span></div></div></div><div class="rd-grid"><div class="rd-clip"><div class="rd-media" style="cursor: pointer;"><div class="rd-thumb" style="background: linear-gradient(135deg, rgb(87, 50, 25), rgb(42, 45, 11));"></div><div class="rd-play"><span class="ring"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none"></polygon></svg></span></div><span class="rd-scorebadge"><span class="pip" style="background: var(--pending);"></span>62%</span><span class="rd-viralbadge warm" title="Virality — how shareable this moment looks"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline><polyline points="16 7 22 7 22 13"></polyline></svg>44% viral</span><span class="rd-dur">30s</span></div><div class="rd-clip-body"><div class="rd-clip-head"><span class="rd-clip-ch"><span class="av">JY</span>jynxzi</span><span class="rd-status pending">pending</span></div><div class="rd-clip-title">jynxzi — Sub/Raid Hype</div><div class="rd-clip-meta"><span class="rd-tag">12:16 AM</span><span class="rd-tag">Rainbow Six Siege</span></div><div class="rd-clip-actions"><span class="rd-btn live sm"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="20 6 9 17 4 12"></polyline></svg>Approve</span><span class="rd-btn danger sm"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>Reject</span></div></div></div><div class="rd-clip"><div class="rd-media" style="cursor: pointer;"><div class="rd-thumb" style="background: linear-gradient(135deg, rgb(87, 61, 25), rgb(35, 45, 11));"></div><div class="rd-play"><span class="ring"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none"></polygon></svg></span></div><span class="rd-scorebadge"><span class="pip" style="background: var(--pending);"></span>58%</span><span class="rd-viralbadge" title="Virality — how shareable this moment looks"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline><polyline points="16 7 22 7 22 13"></polyline></svg>29% viral</span><span class="rd-dur">30s</span></div><div class="rd-clip-body"><div class="rd-clip-head"><span class="rd-clip-ch"><span class="av">LA</span>lacy</span><span class="rd-status pending">pending</span></div><div class="rd-clip-title">lacy — Chat Erupts</div><div class="rd-clip-meta"><span class="rd-tag">12:08 AM</span><span class="rd-tag">Just Chatting</span></div><div class="rd-clip-actions"><span class="rd-btn live sm"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="20 6 9 17 4 12"></polyline></svg>Approve</span><span class="rd-btn danger sm"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>Reject</span></div></div></div><div class="rd-clip"><div class="rd-media" style="cursor: pointer;"><div class="rd-thumb" style="background: linear-gradient(135deg, rgb(25, 86, 87), rgb(11, 21, 45));"></div><div class="rd-play"><span class="ring"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none"></polygon></svg></span></div><span class="rd-scorebadge"><span class="pip" style="background: var(--pending);"></span>55%</span><span class="rd-viralbadge" title="Virality — how shareable this moment looks"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline><polyline points="16 7 22 7 22 13"></polyline></svg>23% viral</span><span class="rd-dur">30s</span></div><div class="rd-clip-body"><div class="rd-clip-head"><span class="rd-clip-ch"><span class="av">JA</span>jasontheween</span><span class="rd-status pending">pending</span></div><div class="rd-clip-title">jasontheween — Chat Speaks As One</div><div class="rd-clip-meta"><span class="rd-tag">11:45 PM</span><span class="rd-tag">Just Chatting</span></div><div class="rd-clip-actions"><span class="rd-btn live sm"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="20 6 9 17 4 12"></polyline></svg>Approve</span><span class="rd-btn danger sm"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>Reject</span></div></div></div><div class="rd-clip"><div class="rd-media" style="cursor: pointer;"><div class="rd-thumb" style="background: linear-gradient(135deg, rgb(55, 25, 87), rgb(45, 11, 39));"></div><div class="rd-play"><span class="ring"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none"></polygon></svg></span></div><span class="rd-scorebadge"><span class="pip" style="background: var(--pending);"></span>65%</span><span class="rd-viralbadge" title="Virality — how shareable this moment looks"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline><polyline points="16 7 22 7 22 13"></polyline></svg>31% viral</span><span class="rd-dur">30s</span></div><div class="rd-clip-body"><div class="rd-clip-head"><span class="rd-clip-ch"><span class="av">TH</span>theburntpeanut</span><span class="rd-status pending">pending</span></div><div class="rd-clip-title">theburntpeanut — Hype Moment</div><div class="rd-clip-meta"><span class="rd-tag">11:23 PM</span><span class="rd-tag">Minecraft</span></div><div class="rd-clip-actions"><span class="rd-btn live sm"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="20 6 9 17 4 12"></polyline></svg>Approve</span><span class="rd-btn danger sm"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>Reject</span></div></div></div><div class="rd-clip"><div class="rd-media" style="cursor: pointer;"><div class="rd-thumb" style="background: linear-gradient(135deg, rgb(87, 61, 25), rgb(35, 45, 11));"></div><div class="rd-play"><span class="ring"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none"></polygon></svg></span></div><span class="rd-scorebadge"><span class="pip" style="background: var(--pending);"></span>60%</span><span class="rd-viralbadge" title="Virality — how shareable this moment looks"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline><polyline points="16 7 22 7 22 13"></polyline></svg>26% viral</span><span class="rd-dur">30s</span></div><div class="rd-clip-body"><div class="rd-clip-head"><span class="rd-clip-ch"><span class="av">LA</span>lacy</span><span class="rd-status pending">pending</span></div><div class="rd-clip-title">lacy — Emotions Run High</div><div class="rd-clip-meta"><span class="rd-tag">11:11 PM</span><span class="rd-tag">Just Chatting</span></div><div class="rd-clip-actions"><span class="rd-btn live sm"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="20 6 9 17 4 12"></polyline></svg>Approve</span><span class="rd-btn danger sm"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>Reject</span></div></div></div><div class="rd-clip"><div class="rd-media" style="cursor: pointer;"><div class="rd-thumb" style="background: linear-gradient(135deg, rgb(87, 50, 25), rgb(42, 45, 11));"></div><div class="rd-play"><span class="ring"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none"></polygon></svg></span></div><span class="rd-scorebadge"><span class="pip" style="background: var(--live);"></span>82%</span><span class="rd-viralbadge warm" title="Virality — how shareable this moment looks"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline><polyline points="16 7 22 7 22 13"></polyline></svg>50% viral</span><span class="rd-dur">30s</span></div><div class="rd-clip-body"><div class="rd-clip-head"><span class="rd-clip-ch"><span class="av">JY</span>jynxzi</span><span class="rd-status approved">approved</span></div><div class="rd-clip-title">jynxzi — Everything Pops Off At Once</div><div class="rd-clip-meta"><span class="rd-tag">12:00 AM</span><span class="rd-tag">Rainbow Six Siege</span></div><div class="rd-clip-actions"><span class="rd-resolved"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0; color: var(--live);"><polyline points="20 6 9 17 4 12"></polyline></svg>Approved</span></div></div></div><div class="rd-clip"><div class="rd-media" style="cursor: pointer;"><div class="rd-thumb" style="background: linear-gradient(135deg, rgb(87, 50, 25), rgb(42, 45, 11));"></div><div class="rd-play"><span class="ring"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none"></polygon></svg></span></div><span class="rd-scorebadge"><span class="pip" style="background: var(--pending);"></span>71%</span><span class="rd-viralbadge warm" title="Virality — how shareable this moment looks"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline><polyline points="16 7 22 7 22 13"></polyline></svg>47% viral</span><span class="rd-dur">30s</span></div><div class="rd-clip-body"><div class="rd-clip-head"><span class="rd-clip-ch"><span class="av">JY</span>jynxzi</span><span class="rd-status approved">approved</span></div><div class="rd-clip-title">jynxzi — Chat Calls For The Clip</div><div class="rd-clip-meta"><span class="rd-tag">11:31 PM</span><span class="rd-tag">Rainbow Six Siege</span></div><div class="rd-clip-actions"><span class="rd-resolved"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0; color: var(--live);"><polyline points="20 6 9 17 4 12"></polyline></svg>Approved</span></div></div></div><div class="rd-clip"><div class="rd-media" style="cursor: pointer;"><div class="rd-thumb" style="background: linear-gradient(135deg, rgb(25, 87, 62), rgb(11, 35, 45));"></div><div class="rd-play"><span class="ring"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none"></polygon></svg></span></div><span class="rd-scorebadge"><span class="pip" style="background: var(--pending);"></span>63%</span><span class="rd-viralbadge" title="Virality — how shareable this moment looks"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline><polyline points="16 7 22 7 22 13"></polyline></svg>17% viral</span><span class="rd-dur">30s</span></div><div class="rd-clip-body"><div class="rd-clip-head"><span class="rd-clip-ch"><span class="av">ST</span>stableronaldo</span><span class="rd-status rejected">rejected</span></div><div class="rd-clip-title">stableronaldo — Loud Reaction</div><div class="rd-clip-meta"><span class="rd-tag">11:53 PM</span><span class="rd-tag">Just Chatting</span></div><div class="rd-clip-actions"><span class="rd-resolved"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0; color: var(--danger);"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>Rejected</span></div></div></div></div></section>"""
REVIEW_BOX   = (1452, 939)
REVIEW_BOX_M = (406, 3492)

# One clip opened: why it fired, and what it is.
DETAIL_HTML = """<div class="rd-modal"><div class="rd-modal-media"><div class="thumb" style="background: linear-gradient(135deg, rgb(87, 50, 25), rgb(42, 45, 11));"></div><div class="rd-modal-play"><span class="ring"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none"></polygon></svg></span></div><span class="rd-modal-close"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg></span><span class="rd-scorebadge" style="top: 14px; right: 60px;"><span class="pip" style="background: var(--pending);"></span>62% trigger</span></div><div class="rd-modal-body"><div class="rd-modal-head"><span class="av">JY</span><div style="flex: 1 1 0%;"><h3>jynxzi — Sub/Raid Hype</h3><div class="mt">jynxzi · Rainbow Six Siege · 12:16 AM</div></div><span class="rd-status pending">pending</span></div><div class="rd-modal-grid"><div><div class="rd-eyebrow" style="margin-bottom: 14px;">Why it fired</div><div class="rd-sigbar"><div class="sh"><span class="sk">Chat velocity</span><span class="sv" style="color: var(--pending);">72%</span></div><div class="st"><div class="sf" style="width: 72%;"></div></div></div><div class="rd-sigbar"><div class="sh"><span class="sk">Keyword hits</span><span class="sv" style="color: var(--pending);">65%</span></div><div class="st"><div class="sf" style="width: 65%;"></div></div></div><div class="rd-sigbar"><div class="sh"><span class="sk">Sentiment</span><span class="sv" style="color: var(--live);">82%</span></div><div class="st"><div class="sf" style="width: 82%;"></div></div></div><div class="rd-sigbar"><div class="sh"><span class="sk">Audio spike</span><span class="sv" style="color: var(--live);">81%</span></div><div class="st"><div class="sf" style="width: 81%;"></div></div></div></div><div><div class="rd-eyebrow" style="margin-bottom: 14px;">Details</div><div class="rd-meta-row"><span class="mk">Duration</span><span class="mv">30s</span></div><div class="rd-meta-row"><span class="mk">Platform</span><span class="mv" style="text-transform: capitalize;">twitch</span></div><div class="rd-meta-row"><span class="mk">Game</span><span class="mv">Rainbow Six Siege</span></div><div class="rd-meta-row"><span class="mk">Captured</span><span class="mv">12:16 AM</span></div><div class="rd-meta-row"><span class="mk">Virality</span><span class="mv">44%</span></div><div class="rd-modal-actions"><span class="rd-btn live sm"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><polyline points="20 6 9 17 4 12"></polyline></svg>Approve</span><span class="rd-btn danger sm"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block; flex-shrink: 0;"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>Reject</span></div></div></div></div></div>"""
DETAIL_BOX   = (900, 729)
DETAIL_BOX_M = (430, 746)
