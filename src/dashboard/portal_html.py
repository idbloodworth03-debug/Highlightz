"""
The affiliate portal page.

Kept in its own module for the same reason compare_html and tutorial_content
are: api.py is already twelve thousand lines and a page is content, not
routing.

DESIGN NOTE. This is deliberately one screen with no navigation. An affiliate
has exactly one question — how is my code doing — and every control that is
not an answer to it is a thing to get lost in. It borrows the admin page's
palette so it reads as part of the same product, and nothing else.
"""

PORTAL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>Affiliate Portal — Highlightz</title>
<link rel="icon" href="/static/icon.png">
<style>
  :root{
    --bone:#0A0A0C; --void:#0E0B11; --wall:#151119;
    --ink:#F2EAF7; --ink-2:#B9AEC4; --ink-3:#9C90A6;
    --hair:rgba(242,234,247,.085); --hair-2:rgba(242,234,247,.15);
    --plum:#B86ADC; --flare:#D26AFB; --ember:#F7A745;
    --good:#4ADE80; --bad:#FF7A8A;
    --sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
    --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bone);color:var(--ink);font-family:var(--sans);
       font-size:15px;line-height:1.6;-webkit-font-smoothing:antialiased}
  .wrap{max-width:840px;margin:0 auto;padding:40px 20px 80px}
  header{display:flex;align-items:center;gap:14px;margin-bottom:32px;flex-wrap:wrap}
  header img{width:38px;height:38px;border-radius:9px;display:block}
  .ttl{font-size:20px;font-weight:800;letter-spacing:-.02em}
  .who{margin-left:auto;font-size:13px;color:var(--ink-3);display:flex;align-items:center;gap:10px}
  .who a{color:var(--ink-3);text-decoration:none;border-bottom:1px solid var(--hair-2)}
  .card{background:var(--void);border:1px solid var(--hair);border-radius:14px;
        padding:24px;margin-bottom:18px}
  h2{font-size:12px;text-transform:uppercase;letter-spacing:.11em;color:var(--ink-3);
     margin:0 0 14px;font-weight:700}
  .code{font-family:var(--mono);font-size:26px;font-weight:600;color:var(--flare);
        letter-spacing:.02em;word-break:break-all}
  .linkrow{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
  .linkbox{flex:1;min-width:240px;background:var(--wall);border:1px solid var(--hair);
           border-radius:9px;padding:11px 14px;font-family:var(--mono);font-size:13px;
           color:var(--ink-2);overflow-x:auto;white-space:nowrap}
  .btn{background:var(--plum);color:#12060f;border:0;border-radius:9px;padding:11px 18px;
       font-weight:700;font-size:13.5px;cursor:pointer;font-family:var(--sans);white-space:nowrap}
  .btn:hover{background:var(--flare)}
  .btn.ok{background:var(--good)}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px}
  .tile{background:var(--void);border:1px solid var(--hair);border-radius:14px;padding:20px}
  .tile .k{font-size:11.5px;text-transform:uppercase;letter-spacing:.09em;color:var(--ink-3);
           font-weight:700;margin-bottom:8px}
  .tile .v{font-size:32px;font-weight:800;letter-spacing:-.03em;line-height:1}
  .tile .s{font-size:12.5px;color:var(--ink-3);margin-top:7px;line-height:1.45}
  .v.good{color:var(--good)} .v.plum{color:var(--flare)} .v.amber{color:var(--ember)}
  .lede{color:var(--ink-2);font-size:14px;margin:0 0 16px}
  .lede b{color:var(--ink)}
  .muted{color:var(--ink-3);font-size:13px}
  .empty{text-align:center;padding:48px 24px}
  .empty .big{font-size:19px;font-weight:700;margin-bottom:10px}
  dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:8px 16px;font-size:13.5px}
  dt{color:var(--ink);font-weight:600;white-space:nowrap}
  dd{margin:0;color:var(--ink-3)}
  .loading{color:var(--ink-3);padding:24px 0}
  @media(max-width:560px){
    .wrap{padding:24px 14px 60px}
    .tile .v{font-size:27px}
    dl{grid-template-columns:1fr;gap:2px 0}
    dd{margin-bottom:10px}
  }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <img src="/static/icon.png" alt="">
    <span class="ttl">Affiliate Portal</span>
    <span class="who" id="who"></span>
  </header>

  <div id="root"><div class="loading">Loading&hellip;</div></div>

</div>

<script>
function esc(s){ return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function tile(k, v, sub, cls){
  return '<div class="tile"><div class="k">' + esc(k) + '</div>'
       + '<div class="v ' + (cls || '') + '">' + esc(v) + '</div>'
       + (sub ? '<div class="s">' + esc(sub) + '</div>' : '') + '</div>';
}

function render(d){
  var root = document.getElementById('root');
  document.getElementById('who').innerHTML =
    esc(d.username || '') + ' <a href="/logout">Sign out</a>';

  // Not an affiliate. Says what to do rather than just refusing — somebody who
  // followed a link here and sees a bare "no access" has no idea whether that
  // is a mistake or a decision.
  if(!d.code){
    root.innerHTML = '<div class="card empty">'
      + '<div class="big">No affiliate code on this account</div>'
      + '<div class="muted">You are signed in as <b>' + esc(d.username || 'your account')
      + '</b>, but no code is attached to it yet.<br>If you are expecting one, reply to the '
      + 'email you were invited with and it will be added.</div></div>';
    return;
  }

  var s = d.stats || {};
  var elig = s.retention_eligible || 0;
  var pct = elig ? Math.round((s.retained_wk2 || 0) / elig * 100) : null;

  root.innerHTML =
      '<div class="card">'
    +   '<h2>Your code</h2>'
    +   '<div class="code">' + esc(s.code) + '</div>'
    +   '<div class="linkrow">'
    +     '<div class="linkbox" id="lnk">' + esc(s.link) + '</div>'
    +     '<button class="btn" id="copy">Copy link</button>'
    +   '</div>'
    +   '<div class="muted" style="margin-top:12px">Share the link, or have people type '
    +     '<b style="color:var(--ink-2)">' + esc(s.code) + '</b> when they sign up. Both count the same.</div>'
    + '</div>'

    + '<div class="grid">'
    +   tile('Signups', s.signups || 0, 'People who made an account through your code', 'plum')
    +   tile('Connected a channel', s.connected || 0, 'Got as far as actually using it')
    +   tile('Paying', s.paid || 0, 'On a paid plan right now', (s.paid ? 'good' : ''))
    + '</div>'

    + '<div class="grid" style="margin-top:14px">'
    +   tile('Last 7 days', s.last_7 || 0, 'Signups this week')
    +   tile('Last 30 days', s.last_30 || 0, 'Signups this month')
    +   tile('Still active week 2', pct === null ? '—' : pct + '%',
            elig ? ((s.retained_wk2 || 0) + ' of ' + elig + ' old enough to count')
                 : 'Nobody has been signed up a full week yet', 'amber')
    + '</div>'

    + '<div class="card" style="margin-top:18px">'
    +   '<h2>What these mean</h2>'
    +   '<dl>'
    +     '<dt>Signups</dt><dd>Accounts created through your code. First touch wins &mdash; '
    +       'if somebody arrives on your link and later on someone else\\'s, they stay yours.</dd>'
    +     '<dt>Connected a channel</dt><dd>They linked Twitch and started monitoring. '
    +       'A signup that never gets here never saw the product work.</dd>'
    +     '<dt>Paying</dt><dd>On a paid plan at this moment, not ever. Somebody who '
    +       'subscribed and cancelled is not counted.</dd>'
    +     '<dt>Still active week 2</dt><dd>Of the people signed up more than seven days ago, '
    +       'how many came back in the last seven. Anyone newer is left out of both sides '
    +       'rather than counted as churned.</dd>'
    +   '</dl>'
    + '</div>';

  var btn = document.getElementById('copy');
  btn.onclick = function(){
    var t = s.link;
    var done = function(){ btn.textContent = 'Copied'; btn.className = 'btn ok';
                           setTimeout(function(){ btn.textContent = 'Copy link';
                                                  btn.className = 'btn'; }, 1600); };
    // navigator.clipboard needs a secure context and can be refused outright,
    // so there is a fallback rather than a button that silently does nothing.
    if(navigator.clipboard && window.isSecureContext){
      navigator.clipboard.writeText(t).then(done, fallback);
    } else { fallback(); }
    function fallback(){
      var ta = document.createElement('textarea');
      ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); done(); }
      catch(e){ btn.textContent = 'Copy failed'; }
      document.body.removeChild(ta);
    }
  };
}

fetch('/portal/stats', { headers: { 'Accept': 'application/json' } })
  .then(function(r){
    if(r.status === 401){ window.location.href = '/login'; return null; }
    if(!r.ok) throw new Error(r.status);
    return r.json();
  })
  .then(function(d){ if(d) render(d); })
  .catch(function(){
    document.getElementById('root').innerHTML =
      '<div class="card empty"><div class="big">Could not load your numbers</div>'
      + '<div class="muted">Refresh in a moment. If it keeps happening, let us know.</div></div>';
  });
</script>
</body>
</html>
"""
