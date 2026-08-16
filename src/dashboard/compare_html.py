"""Renders /compare from src/dashboard/compare_content.py.

Same split as the tutorial: claims live in the data file, this module only
knows how to lay them out. That matters more here than anywhere else on the
site, because these claims are about other companies and will need editing the
moment one of them changes a price.

The design system is imported from tutorial_html rather than copied, so this
reads as another room in the same building and there is one place to change a
token.

TWO RULES, same as the tutorial and for the same reasons:
  * No f-strings around CSS or JS — both are full of braces.
  * No backslashes in embedded JS — Python parses this file before the browser
    sees it, so an escape here is not the one that reaches the page.
"""

from __future__ import annotations

from html import escape

from src.dashboard import compare_content as C
from src.dashboard.tutorial_html import BASE_CSS

_TITLE = "Highlightz vs Opus Clip vs Eklipse — price and feature comparison"
_DESC = ("Honest comparison of Highlightz, Opus Clip and Eklipse for stream "
         "clipping: pricing model, live capture, multi-channel monitoring, and "
         "what each tool is actually built for.")


_CSS = BASE_CSS + """
  /* ── comparison-specific ─────────────────────────────────────────────── */
  .cmp-hero{padding:96px 0 44px;text-align:center}
  .cmp-hero h1{font-size:clamp(34px,5.2vw,60px);line-height:1.04;letter-spacing:-.025em;
    margin:16px auto 18px;max-width:15ch}
  .cmp-hero .lead{color:var(--ink-2);font-size:clamp(16px,1.7vw,19.5px);line-height:1.6;
    max-width:60ch;margin:0 auto}

  .cards{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin:52px 0 12px}
  .card{border:1px solid var(--hair);border-radius:16px;padding:26px 24px;background:var(--wall);
    display:flex;flex-direction:column}
  .card.ours{border-color:rgba(184,106,220,.45);background:
    linear-gradient(180deg,rgba(184,106,220,.10),rgba(184,106,220,.02))}
  .card h3{font-size:19px;letter-spacing:-.01em;margin-bottom:6px}
  .card .tag{color:var(--ink-3);font-size:13.5px;line-height:1.5;min-height:44px}
  .card .plan{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
    padding:11px 0;border-top:1px solid var(--hair)}
  .card .plan:first-of-type{margin-top:16px}
  .card .pn{font-size:13.5px;color:var(--ink-2)}
  .card .pp{font-family:var(--mono);font-size:15px;color:var(--ink);white-space:nowrap}
  .card.ours .pp{color:var(--glow-ink)}
  .card .pnote{font-size:12.5px;color:var(--ink-3);line-height:1.5;padding-bottom:11px}
  /* margin-top:auto — the three cards stretch to the tallest, and a source note
     floating mid-card reads as unfinished. Pinned to the bottom they line up. */
  .card .src{margin-top:auto;padding-top:16px;font-size:11.5px;color:var(--ink-3);line-height:1.5}
  .card .src a{color:var(--ink-3)}

  .caveat{margin:18px 0 0;padding:13px 16px;border-radius:11px;font-size:13px;line-height:1.55;
    border:1px solid rgba(247,167,69,.32);background:rgba(247,167,69,.07);color:var(--ink-2)}

  .math{margin:64px 0;padding:34px 32px;border:1px solid var(--hair);border-radius:18px;
    background:var(--wall)}
  .math h2{font-size:clamp(23px,2.7vw,32px);letter-spacing:-.02em;margin:12px 0 16px;max-width:22ch}
  .math p{color:var(--ink-2);font-size:15.5px;line-height:1.7;max-width:66ch}
  .math p + p{margin-top:14px}

  .matrix{width:100%;border-collapse:collapse;margin-top:14px;font-size:14.5px}
  .matrix th,.matrix td{padding:15px 14px;border-bottom:1px solid var(--hair);text-align:left;
    vertical-align:top}
  .matrix thead th{font-family:var(--mono);font-size:11.5px;font-weight:600;letter-spacing:.06em;
    text-transform:uppercase;color:var(--ink-3);border-bottom-color:var(--bruise)}
  .matrix thead th.us{color:var(--glow-ink)}
  .matrix td.c{text-align:center;width:118px;white-space:nowrap}
  .matrix tbody tr:hover{background:rgba(242,234,247,.022)}
  .matrix .feat{color:var(--ink);line-height:1.45}
  .matrix .why{display:block;margin-top:5px;color:var(--ink-3);font-size:13px;line-height:1.55}
  .yes{color:#7BE0A8;font-weight:600}
  .no{color:var(--ink-3)}
  .part{color:var(--ember);font-family:var(--mono);font-size:12.5px}
  .mwrap{overflow-x:auto;-webkit-overflow-scrolling:touch}

  .fair{margin:64px 0;padding:34px 32px;border:1px solid var(--hair);border-radius:18px}
  .fair h2{font-size:clamp(23px,2.7vw,30px);letter-spacing:-.02em;margin:12px 0 22px}
  .fair .pt{padding:16px 0;border-top:1px solid var(--hair)}
  .fair .pt b{display:block;font-size:15.5px;margin-bottom:6px}
  .fair .pt span{color:var(--ink-2);font-size:14.5px;line-height:1.65}

  .closer{text-align:center;padding:74px 0 30px}
  .closer h2{font-size:clamp(26px,3.2vw,38px);letter-spacing:-.022em;margin-bottom:18px}
  .closer p{color:var(--ink-2);font-size:16px;line-height:1.7;max-width:62ch;margin:0 auto 28px}
  .closer .note{font-size:13px;color:var(--ink-3);margin-top:14px}

  @media (max-width:900px){
    .cards{grid-template-columns:1fr;gap:14px}
    .card .tag{min-height:0}
    .math,.fair{padding:26px 20px}
    /* Stacked, not scrolled. Horizontally scrolling a comparison table means
       reading one product at a time, which is the one thing the page exists to
       avoid. Each row becomes a card: the claim, then all three answers. */
    .mwrap{overflow-x:visible}
    .matrix{font-size:14px;min-width:0}
    .matrix thead{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
    .matrix,.matrix tbody,.matrix tr,.matrix td{display:block;width:100%}
    .matrix tr{padding:18px 0;border-bottom:1px solid var(--hair)}
    .matrix tr:hover{background:none}
    .matrix td{border:0;padding:0}
    .matrix td.c{display:inline-flex;align-items:baseline;gap:7px;width:auto;
      margin:12px 16px 0 0;text-align:left}
    .matrix td.c::before{content:attr(data-l);font-family:var(--mono);font-size:10px;
      letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
  }
"""


def _cell(value, label: str) -> str:
    """A matrix cell. True/False render as marks; a string renders verbatim.

    The string case is the honest one — several rows are not a yes or a no
    ("3–10 channels", "VOD only", "7 days"), and forcing them into a tick would
    overstate on our side, which is exactly how these pages lose credibility.
    """
    td = '<td class="c" data-l="' + escape(label) + '">'
    if value is True:
        return td + '<span class="yes" aria-label="yes">Yes</span></td>'
    if value is False:
        return td + '<span class="no" aria-label="no">&mdash;</span></td>'
    return td + '<span class="part">' + escape(str(value)) + "</span></td>"


def _plan_rows(p: "C.Product") -> str:
    out = []
    for plan in p.plans:
        out.append('<div class="plan"><span class="pn">' + escape(plan.name)
                   + '</span><span class="pp">' + escape(plan.price) + "</span></div>")
        out.append('<div class="pnote">' + escape(plan.note) + "</div>")
    return "".join(out)


def _card(p: "C.Product") -> str:
    if p.is_us:
        src = ('<div class="src">Our own pricing &mdash; '
               '<a href="/#pricing">see the plans</a>.</div>')
    else:
        src = ('<div class="src">As published on '
               '<a href="' + escape(p.source_url) + '" target="_blank" rel="nofollow noopener">'
               + escape(p.name) + "&rsquo;s pricing page</a>, checked "
               + escape(p.checked_on) + ".</div>")
    return ('<div class="card' + (" ours" if p.is_us else "") + '">'
            "<h3>" + escape(p.name) + "</h3>"
            '<div class="tag">' + escape(p.tagline) + "</div>"
            + _plan_rows(p) + src + "</div>")


def _matrix() -> str:
    head = ('<thead><tr><th>Feature</th><th class="c us">Highlightz</th>'
            '<th class="c">Opus Clip</th><th class="c">Eklipse</th></tr></thead>')
    rows = []
    for feat, ours, opus, ekl, why in C.FEATURES:
        rows.append("<tr><td>"
                    '<span class="feat">' + escape(feat) + "</span>"
                    '<span class="why">' + escape(why) + "</span></td>"
                    + _cell(ours, "Highlightz") + _cell(opus, "Opus Clip")
                    + _cell(ekl, "Eklipse") + "</tr>")
    return ('<div class="mwrap"><table class="matrix">' + head
            + "<tbody>" + "".join(rows) + "</tbody></table></div>")


def _paras(body: str) -> str:
    return "".join("<p>" + escape(part) + "</p>" for part in body.split("\n\n"))


def _faq() -> str:
    items = []
    for q, a in C.FAQ:
        items.append('<div class="faq-item"><div class="faq-q">' + escape(q)
                     + '</div><div class="faq-a">' + escape(a) + "</div></div>")
    return "".join(items)


def render() -> str:
    caveat = ""
    if not C.PRICES_CONFIRMED:
        # Visible, not a code comment: while this is False the page is quoting
        # figures nobody has checked against the source, and the reader is
        # entitled to know that before acting on them.
        caveat = ('<div class="caveat"><b>Pricing not yet re-verified.</b> '
                  "The competitor figures above were gathered from secondary "
                  "sources and have not been confirmed against each company's "
                  "own pricing page. Follow the links before relying on them."
                  "</div>")

    return (
"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>""" + escape(_TITLE) + """</title>
<meta name="description" content=\"""" + escape(_DESC) + """\">
<link rel="icon" type="image/png" href="/static/icon.png">
<link rel="canonical" href="https://highlightz.app/compare">
<link rel="preload" href="/static/fonts/sora-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/plexmono-600.woff2" as="font" type="font/woff2" crossorigin>
<meta property="og:type" content="article">
<meta property="og:site_name" content="Highlightz">
<meta property="og:url" content="https://highlightz.app/compare">
<meta property="og:title" content=\"""" + escape(_TITLE) + """\">
<meta property="og:description" content=\"""" + escape(_DESC) + """\">
<meta property="og:image" content="https://highlightz.app/static/og-card-v2.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content=\"""" + escape(_TITLE) + """\">
<meta name="twitter:description" content=\"""" + escape(_DESC) + """\">
<meta name="twitter:image" content="https://highlightz.app/static/og-card-v2.png">
<style>""" + _CSS + """</style>
</head>
<body>
<div class="grain" aria-hidden="true"></div>

<nav class="nav">
  <a href="/" class="nav-logo"><img src="/static/logo-mark.png" alt="Highlightz"><span>Highlightz</span></a>
  <div class="nav-links">
    <a href="/#how" class="nav-link">How it works</a>
    <a href="/tutorial" class="nav-link">Tutorial</a>
    <a href="/compare" class="nav-link on">Compare</a>
    <a href="/#features" class="nav-link">Features</a>
    <a href="/#pricing" class="nav-link">Pricing</a>
  </div>
  <div class="nav-right">
    <a href="/login" class="nav-link">Sign in</a>
    <a href="/login" class="btn btn-key" style="padding:10px 18px;font-size:13.5px">Get started</a>
  </div>
</nav>

<header class="wrap cmp-hero">
  <div class="kicker">Comparison</div>
  <h1>""" + escape(C.HERO_TITLE) + """</h1>
  <p class="lead">""" + escape(C.HERO_LEAD) + """</p>
</header>

<div class="wrap">
  <div class="cards">""" + "".join(_card(p) for p in C.PRODUCTS) + """</div>
  """ + caveat + """

  <section class="math">
    <div class="kicker">""" + escape(C.THE_MATH["kicker"]) + """</div>
    <h2>""" + escape(C.THE_MATH["title"]) + """</h2>
    """ + _paras(C.THE_MATH["body"]) + """
  </section>

  <section id="features">
    <div class="kicker">Feature by feature</div>
    <h2 style="font-size:clamp(23px,2.7vw,32px);letter-spacing:-.02em;margin:12px 0 6px">
      What each one is actually built for</h2>
    """ + _matrix() + """
  </section>

  <section class="fair">
    <div class="kicker">""" + escape(C.THEY_DO_BETTER["kicker"]) + """</div>
    <h2>""" + escape(C.THEY_DO_BETTER["title"]) + """</h2>
    """ + "".join('<div class="pt"><b>' + escape(t) + "</b><span>" + escape(d)
                  + "</span></div>" for t, d in C.THEY_DO_BETTER["points"]) + """
  </section>

  <section id="faq">
    <div class="kicker">Questions</div>
    <h2 style="font-size:clamp(23px,2.7vw,32px);letter-spacing:-.02em;margin:12px 0 18px">
      Before you decide</h2>
    """ + _faq() + """
  </section>

  <section class="closer">
    <h2>""" + escape(C.CLOSER["title"]) + """</h2>
    <p>""" + escape(C.CLOSER["body"]) + """</p>
    <a href="/login" class="btn btn-key btn-lg">""" + escape(C.CLOSER["cta"]) + """</a>
    <div class="note">""" + escape(C.CLOSER["cta_note"]) + """</div>
  </section>
</div>

<footer class="footer">
  <div class="fl">&copy; 2026 ANTI Technology LLC &mdash; All rights reserved.</div>
  <a href="/tutorial">Tutorial</a> &middot; <a href="/compare">Compare</a> &middot; <a href="/tos">Terms of Service</a> &middot; <a href="/privacy">Privacy Policy</a> &middot; <a href="/cookies">Cookie Policy</a> &middot; <a href="/opt-out">Streamer Opt-Out</a>
</footer>
</body>
</html>""")
