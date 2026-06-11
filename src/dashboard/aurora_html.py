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
.rd-app{position:relative;height:100vh;display:grid;grid-template-columns:78px 1fr;isolation:isolate}
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
.rd-stream .nm{font-size:14px;font-weight:700;letter-spacing:-.01em;display:flex;align-items:center;gap:7px}
.rd-stream .nm .plat{width:6px;height:6px;border-radius:50%;background:var(--acc);box-shadow:0 0 8px var(--acc)}
.rd-stream .mt{font-size:11px;color:var(--fg-2);margin-top:3px;display:flex;gap:6px;align-items:center}
.rd-chip{font-size:10px;font-weight:600;padding:2px 7px;border-radius:var(--r-pill);background:rgba(255,255,255,.06);color:var(--fg-2);text-transform:capitalize}
.rd-stream-actions{display:flex;gap:6px;align-items:center}
.rd-x{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;background:transparent;border:none;color:var(--fg-3);transition:.15s}
.rd-x:hover{color:var(--danger);background:var(--danger-soft)}
.rd-score{margin-top:12px}
.rd-score-top{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:7px}
.rd-score-top .lbl{font-size:11px;color:var(--fg-2);font-weight:500}
.rd-score-top .val{font-size:20px;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums;line-height:1}
.rd-track{height:8px;border-radius:var(--r-pill);background:rgba(255,255,255,.07);overflow:hidden;position:relative}
.rd-fill{height:100%;border-radius:var(--r-pill);transition:background .6s;position:relative}
.rd-fill::after{content:'';position:absolute;right:0;top:0;bottom:0;width:14px;background:rgba(255,255,255,.5);filter:blur(5px);opacity:.7}
.rd-sigs{display:flex;gap:5px;margin-top:8px;flex-wrap:wrap}
.rd-sig{font-size:10px;padding:2px 7px;border-radius:6px;background:rgba(255,255,255,.05);color:var(--fg-2);font-variant-numeric:tabular-nums}
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
.rd-filters{display:flex;gap:6px;margin-left:auto;background:rgba(255,255,255,.04);padding:4px;border-radius:var(--r-pill);border:1px solid var(--hair)}
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
.rd-navitem{width:56px;height:54px;border-radius:15px;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:4px;background:transparent;border:none;
  color:var(--fg-3);font-size:9.5px;font-weight:600;letter-spacing:.01em;transition:.16s;position:relative}
.rd-navitem:hover{color:var(--fg-2);background:rgba(255,255,255,.05)}
.rd-navitem.active{color:#fff}
.rd-navitem.active::before{content:'';position:absolute;inset:0;border-radius:15px;
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

function RdScoreChart({ data }) {
  const w=600, h=150, pad=6;
  const d = data && data.length>1 ? data : [0,0];
  const pts = d.map((v,i)=>[pad+(i/(d.length-1))*(w-2*pad), h-pad-(v/100)*(h-2*pad)]);
  const line = pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ');
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
  return (
    <div className="rd-stream">
      <div className="rd-stream-top">
        <div>
          <div className="nm"><span className="plat"/>{s.channel}</div>
          <div className="mt">
            <span className="rd-chip">{s.platform}</span>
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
        <div className="rd-track"><div className="rd-fill" style={{width:score+'%',background:scoreFill(score)}}/></div>
        <div className="rd-sigs">{Object.entries(breakdown).map(([k,v])=><span className="rd-sig" key={k}>{k.replace('SignalType.','')}: {typeof v==='number'?v.toFixed(2):v}</span>)}</div>
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
  const score = Math.round(clip.trigger_score||0);
  const dur = fmtDur(clip.duration_seconds);
  const time = fmtTime(clip.created_at);
  const title = clip.clip_title||clip.stream_title||'Live Stream';
  const thumb = clip.thumbnail_url || '';
  const twHref = clip.twitch_url || '';
  return (
    <div className="rd-clip">
      <div className="rd-media" style={{cursor:'pointer'}} onClick={()=>onOpen&&onOpen(clip)}>
        {thumb
          ? <img src={thumb} alt="" style={{position:'absolute',inset:0,width:'100%',height:'100%',objectFit:'cover'}}/>
          : <div className="rd-thumb" style={{background:thumbFor(clip.channel)}}/>}
        <div className="rd-play"><span className="ring"><Icon name="play" size={20}/></span></div>
        <span className="rd-scorebadge"><span className="pip" style={{background:scoreColor(score)}}/>{score}%</span>
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
          {clip.virality_score>0 && <span className="rd-tag" style={{color:clip.virality_score>=65?'#ff7700':clip.virality_score>=35?'#ffcc00':'var(--fg-2)'}}>
            {Math.round(clip.virality_score)}%
          </span>}
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

function ClipModal({ clip, onClose, onApprove, onReject }) {
  if (!clip) return null;
  const score = Math.round(clip.trigger_score||0);
  const dur = fmtDur(clip.duration_seconds);
  const time = fmtTime(clip.created_at);
  const title = clip.clip_title||clip.stream_title||'Live Stream';
  const embed = clip.embed_url || '';
  const twHref = clip.twitch_url || '';
  const thumb = clip.thumbnail_url || '';
  // Twitch requires a parent= param matching the embedding host.
  const embedSrc = embed
    ? embed + (embed.indexOf('?')>=0?'&':'?') + 'parent=' + location.hostname + '&autoplay=false'
    : '';
  const sigMap = {};
  for (const s of (clip.trigger_signals||[])) {
    const k = (s.type||'').replace('SignalType.','');
    sigMap[k] = (s.value||0)*100;
  }
  const sigKeys = [['CHAT_VELOCITY','Chat velocity'],['KEYWORD','Keyword hits'],['SENTIMENT','Sentiment'],['AUDIO_SPIKE','Audio spike']];

  return (
    <div className="rd-modal-bg" onClick={onClose}>
      <div className="rd-modal" onClick={e=>e.stopPropagation()}>
        <div className="rd-modal-media">
          {embedSrc
            ? <iframe src={embedSrc} allowFullScreen frameBorder="0" scrolling="no" style={{position:'absolute',inset:0,width:'100%',height:'100%',background:'#000'}}/>
            : thumb
              ? <><img src={thumb} alt="" style={{position:'absolute',inset:0,width:'100%',height:'100%',objectFit:'cover'}}/>{twHref&&<a href={twHref} target="_blank" rel="noopener" className="rd-modal-play"><span className="ring"><Icon name="play" size={26}/></span></a>}</>
              : <><div className="thumb" style={{background:thumbFor(clip.channel)}}/><div className="rd-modal-play"><span className="ring"><Icon name="play" size={26}/></span></div></>}
          <button className="rd-modal-close" onClick={onClose}><Icon name="x" size={16}/></button>
          <span className="rd-scorebadge" style={{top:14,right:60}}><span className="pip" style={{background:scoreColor(score)}}/>{score}% trigger</span>
        </div>

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
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ReviewScreen({ streams, scores, profiles, clips, filter, setFilter, onAdd, onRemove, onForce, onApprove, onReject, onOpen }) {
  const [ch, setCh] = useState('');
  const [preset, setPreset] = useState('default');
  const [webhook, setWebhook] = useState('');
  const add = () => { if(ch.trim()){onAdd(ch.trim(),preset,webhook.trim());setCh('');setWebhook('');} };
  const clipsArr = Object.values(clips);
  const pending = clipsArr.filter(c=>c.status==='pending').length;
  const approved = clipsArr.filter(c=>c.status==='approved').length;
  const streamsArr = Object.values(streams);
  const avgScore = streamsArr.length ? Math.round(streamsArr.reduce((a,s)=>a+(scores[s.channel]?.score||0),0)/streamsArr.length) : 0;
  const shown = (filter==='all' ? clipsArr : clipsArr.filter(c=>c.status===filter))
    .sort((a,b)=>{const sp={pending:0,approved:1,rejected:2};if(sp[a.status]!==sp[b.status])return sp[a.status]-sp[b.status];return(b.created_at||0)-(a.created_at||0);});
  return (
    <div className="rd-body" style={{flex:1}}>
      <aside className="rd-col" style={{minHeight:0}}>
        <div className="rd-rail glass" style={{flex:'0 0 auto'}}>
          <div className="rd-eyebrow">Add a stream</div>
          <div className="rd-addrow">
            <input className="rd-input" placeholder="channel name" value={ch} onChange={e=>setCh(e.target.value)} onKeyDown={e=>e.key==='Enter'&&add()}/>
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
          <input className="rd-input" placeholder="Discord webhook (optional)" value={webhook}
            onChange={e=>setWebhook(e.target.value)} onKeyDown={e=>e.key==='Enter'&&add()} style={{fontSize:12}}/>
          <button className="rd-btn grad" onClick={add}><Icon name="plus" size={15}/>Monitor stream</button>
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
          <div className="rd-filters">
            {['all','pending','approved','rejected'].map(f=><button key={f} className={'rd-filter'+(filter===f?' active':'')} onClick={()=>setFilter(f)}>{f[0].toUpperCase()+f.slice(1)}</button>)}
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
  const clipsArr = Object.values(clips)
    .filter(c=>f==='all'||c.status===f)
    .sort((a,b)=>(b.created_at||0)-(a.created_at||0));
  const total = Object.values(clips).length;
  const approvedCount = Object.values(clips).filter(c=>c.status==='approved').length;
  return (
    <div className="rd-scroll">
      <div className="rd-section-title">
        <h2>Clip library</h2>
        <span className="cnt">{total} captured · {approvedCount} approved</span>
        <div className="rd-filters" style={{marginLeft:'auto'}}>
          {['all','pending','approved','rejected'].map(x=><button key={x} className={'rd-filter'+(f===x?' active':'')} onClick={()=>setF(x)}>{x[0].toUpperCase()+x.slice(1)}</button>)}
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

function WebhookRow({ stream, onSave }) {
  const [url, setUrl] = useState(stream.discord_webhook||'');
  useEffect(()=>setUrl(stream.discord_webhook||''),[stream.discord_webhook]);
  return (
    <div className="rd-field">
      <div><div className="fl">{stream.channel}</div><div className="fd">{stream.platform} · {stream.preset}</div></div>
      <div style={{display:'flex',gap:8,flex:1,maxWidth:340}}>
        <input className="rd-input" value={url} onChange={e=>setUrl(e.target.value)}
          placeholder="https://discord.com/api/webhooks/..." style={{flex:1,fontSize:12}}
          onKeyDown={e=>e.key==='Enter'&&onSave(url)}/>
        <button className="rd-btn sm" onClick={()=>onSave(url)}>Save</button>
      </div>
    </div>
  );
}

function SettingsScreen({ streams, onSaveWebhook }) {
  const [stats, setStats] = useState(null);
  useEffect(()=>{ fetch('/stats').then(r=>r.json()).then(setStats).catch(()=>{}); },[]);
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
        {streamsArr.length>0 && <div className="rd-card glass">
          <h3><span className="si"><Icon name="bell" size={15}/></span>Discord webhooks</h3>
          <div className="desc">Get a notification when a clip is approved.</div>
          {streamsArr.map(s=><WebhookRow key={s.channel} stream={s} onSave={url=>onSaveWebhook(s.channel,url)}/>)}
        </div>}
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

const NAV=[{id:'review',label:'Review',icon:'grid'},{id:'streams',label:'Streams',icon:'radio'},{id:'library',label:'Library',icon:'film'},{id:'settings',label:'Settings',icon:'cog'},{id:'account',label:'Account',icon:'user'}];
const HEAD={review:['Clip review','Approve highlights the moment they fire'],streams:['Streams','Live per-channel analytics & learning'],library:['Library','Every clip you have captured'],settings:['Settings','Tune triggers, storage & workflow'],account:['Account','Billing, profile & legal']};

function AccountScreen({ me }) {
  const [deleting, setDeleting]   = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [delErr, setDelErr]       = useState('');
  const sub   = me.subscription_status || 'none';
  const subLabel = {active:'Active',trialing:'Trial',canceled:'Canceled',past_due:'Past due',none:'No subscription'}[sub] || sub;
  const subColor = (sub==='active'||sub==='trialing') ? 'var(--live)' : sub==='past_due' ? 'var(--pending)' : 'var(--fg-3)';
  const isSubscribed = sub==='active'||sub==='trialing';

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
          {isSubscribed && <div className="rd-field">
            <div><div className="fl">Billing</div><div className="fd">Manage or cancel via Stripe portal</div></div>
            <a href="/billing/portal" className="rd-btn sm" style={{textDecoration:'none'}}>Manage billing</a>
          </div>}
          {!isSubscribed && <div style={{marginTop:16}}>
            <a href="/billing/checkout" className="rd-btn grad" style={{textDecoration:'none',display:'inline-flex',gap:7,alignItems:'center'}}>
              <Icon name="zap" size={14}/>Subscribe now
            </a>
          </div>}
        </div>

        {/* Profile */}
        <div className="rd-card glass">
          <h3><span className="si"><Icon name="user" size={15}/></span>Profile</h3>
          <div className="desc">Your Discord account linked to Highlightz.</div>
          <div className="rd-field" style={{paddingTop:0}}>
            <div style={{display:'flex',alignItems:'center',gap:14}}>
              {me.avatar_url
                ? <img src={me.avatar_url} alt={me.username} style={{width:44,height:44,borderRadius:'50%',objectFit:'cover',flexShrink:0}}/>
                : <span style={{width:44,height:44,borderRadius:'50%',background:'var(--grad)',display:'grid',placeItems:'center',fontWeight:700,color:'#14021c',fontSize:16,flexShrink:0}}>{(me.username||'?')[0].toUpperCase()}</span>}
              <div>
                <div style={{fontWeight:700,fontSize:15}}>{me.username||'—'}</div>
                <div style={{fontSize:11,color:'var(--fg-3)',marginTop:2}}>Signed in with Discord</div>
              </div>
            </div>
            <button className="rd-btn sm danger" style={{flexShrink:0}} onClick={()=>fetch('/logout',{method:'POST'}).then(()=>{location.href='/login';})}>Sign out</button>
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

function RdApp() {
  const [route, setRoute] = useState('review');
  const [streams, setStreams] = useState({});
  const [scores, setScores] = useState({});
  const [profiles, setProfiles] = useState({});
  const [histories, setHistories] = useState({});
  const [clips, setClips] = useState({});
  const [filter, setFilter] = useState('all');
  const [toast, setToast] = useState('');
  const [modalClip, setModalClip] = useState(null);
  const [me, setMe] = useState({username:'', avatar_url:''});
  const toastTimer = useRef(null);

  const flash = useCallback(msg => {
    setToast(msg);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(()=>setToast(''), 3000);
  }, []);

  useEffect(()=>{
    Promise.all([fetch('/clips').then(r=>r.json()), fetch('/streams').then(r=>r.json())])
      .then(([ca,sa])=>{
        setClips(Object.fromEntries(ca.map(c=>[c.id,c])));
        setStreams(Object.fromEntries(sa.map(s=>[s.channel,s])));
      }).catch(()=>{});
    fetch('/profiles').then(r=>r.json()).then(arr=>{
      setProfiles(Object.fromEntries(arr.map(p=>[p.channel,p])));
    }).catch(()=>{});
    fetch('/me').then(r=>r.json()).then(data=>setMe(data)).catch(()=>{});
  },[]);

  useEffect(()=>{
    const proto = location.protocol==='https:'?'wss':'ws';
    let ws;
    const connect = ()=>{
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onmessage = e=>{
        const msg = JSON.parse(e.data);
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
          setScores(p=>({...p,[msg.channel]:{score:msg.score,breakdown:msg.breakdown||{}}}));
          setHistories(p=>{const h=[...(p[msg.channel]||[]),msg.score].slice(-40);return{...p,[msg.channel]:h};});
        }
        else if(msg.event==='profile_updated'){setProfiles(p=>({...p,[msg.profile.channel]:msg.profile}));}
      };
      ws.onclose = ()=>setTimeout(connect,3000);
    };
    connect();
    const ping = setInterval(()=>ws?.readyState===1&&ws.send('ping'),30000);
    return ()=>{clearInterval(ping);ws?.close();};
  },[flash]);

  const addStream = async(channel,preset,webhook='')=>{
    try{
      const r=await fetch('/streams',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel,platform:'twitch',preset,discord_webhook:webhook})});
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
  };
  const rejectClip = async(id)=>{
    await fetch(`/clips/${id}/reject`,{method:'POST'});
    setClips(p=>{const n={...p};delete n[id];return n;});
  };
  const deleteClip = async(id)=>{
    if(!confirm('Delete this clip? This cannot be undone.')) return;
    await fetch(`/clips/${id}/reject`,{method:'POST'});
    setClips(p=>{const n={...p};delete n[id];return n;});
    flash('Clip deleted');
  };
  const saveWebhook = async(channel,url)=>{
    const r=await fetch(`/streams/${encodeURIComponent(channel)}/webhook`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({discord_webhook:url.trim()})});
    if(r.ok){const s=await r.json();setStreams(p=>({...p,[channel]:s}));flash(url.trim()?`Webhook saved for ${channel}`:`Webhook removed for ${channel}`);}
    else flash('Failed to save webhook');
  };

  const pending = Object.values(clips).filter(c=>c.status==='pending').length;

  let screen;
  if(route==='review') screen=<ReviewScreen {...{streams,scores,profiles,clips,filter,setFilter,onAdd:addStream,onRemove:removeStream,onForce:forceClip,onApprove:approveClip,onReject:rejectClip,onOpen:setModalClip}}/>;
  else if(route==='streams') screen=<StreamsScreen {...{streams,scores,profiles,histories,clips,onForce:forceClip}}/>;
  else if(route==='library') screen=<LibraryScreen {...{clips,onOpen:setModalClip,onApprove:approveClip,onReject:rejectClip,onDelete:deleteClip}}/>;
  else if(route==='account') screen=<AccountScreen me={me}/>;
  else screen=<SettingsScreen {...{streams,onSaveWebhook:saveWebhook}}/>;

  return (
    <div className="rd-app" id="rd-app" data-grad="violet" data-density="comfortable" data-glow="on">
      <nav className="rd-nav">
        <span className="logo"><img src="/static/logo.jpg" alt="Highlightz"/></span>
        {NAV.map(n=>(
          <button key={n.id} className={'rd-navitem'+(route===n.id?' active':'')} onClick={()=>setRoute(n.id)}>
            {n.id==='review'&&pending>0&&<span className="navbadge">{pending}</span>}
            <span className="ic"><Icon name={n.icon} size={20}/></span>
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
          <span className="rd-live"><span className="dot"/>Live</span>
          <button className="rd-user-chip" title="Account" style={{border:'none',cursor:'pointer'}} onClick={()=>setRoute('account')}>
            {me.avatar_url
              ? <img src={me.avatar_url} alt={me.username}/>
              : <span className="uc-init">{(me.username||'?')[0].toUpperCase()}</span>}
            <span className="uc-name">{me.username||'Account'}</span>
          </button>
        </header>
        <main className="rd-screen">{screen}</main>
      </div>
      <RdToast msg={toast}/>
      <ClipModal clip={modalClip} onClose={()=>setModalClip(null)} onApprove={approveClip} onReject={rejectClip}/>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<RdApp/>);
</script>
</body>
</html>"""
