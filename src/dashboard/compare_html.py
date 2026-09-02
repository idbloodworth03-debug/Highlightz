"""Renders /compare from src/dashboard/compare_content.py.

Same split as the tutorial: claims live in the data file, this module only
knows how to lay them out. That matters more here than anywhere else on the
site, because these claims are about other companies and will need editing the
moment one of them changes a price.

The design system is imported from tutorial_html rather than copied, so this
reads as another room in the same building and there is one place to change a
token. Since the v4 pass that is the landing page's system: the bar fixed
over the top, a black hero in the display voice, the paper ground for the
reading with hairline rows and the mono for numbers, a black band for the
one argument that is made in numbers, a black close, the one-row footer.

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
  /* Hero: black, under the bar, the title in the display voice. */
  .cmp-hero{background:#000;color:var(--white);padding:calc(var(--nav-h) + var(--s-8)) 0 var(--s-9)}
  .cmp-hero .k{color:var(--ember)}
  .cmp-hero h1{font-size:clamp(40px,5.6vw,84px);max-width:14ch;margin-top:var(--s-4);color:var(--white)}
  .cmp-hero .lead{margin:var(--s-5) 0 0;font-size:clamp(16px,1.4vw,19px);line-height:1.5;
    color:rgba(255,255,255,.72);max-width:var(--measure)}

  /* The three products as the pricing page's columns: a rule on top, the
     name in the display voice, each tier a hairline row with the price in
     the mono. Ours is told apart by the orange rule, nothing else. */
  .cmp-cards{padding:var(--s-9) 0 var(--s-8)}
  .cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:clamp(24px,3vw,48px);align-items:start}
  .card{display:flex;flex-direction:column;min-width:0;border-top:2px solid var(--paper-ink);padding-top:var(--s-5)}
  .card.ours{border-top-color:var(--ember)}
  .card h3{font-family:var(--sans);font-weight:800;font-size:clamp(28px,3vw,40px);letter-spacing:-.035em;
    line-height:1;color:var(--paper-ink);margin:0}
  .card .tag{margin-top:var(--s-3);color:var(--paper-ink-2);font-size:15px;line-height:1.5;min-height:48px}
  .card .plan{display:flex;justify-content:space-between;align-items:baseline;gap:var(--s-4);
    padding:var(--s-3) 0 var(--s-1);border-top:1px solid var(--paper-hair);margin-top:var(--s-3)}
  .card .plan:first-of-type{margin-top:var(--s-5)}
  .card .pn{font-size:15px;color:var(--paper-ink)}
  .card .pp{font-family:var(--mono);font-weight:600;font-size:15px;color:var(--paper-ink);white-space:nowrap;
    font-variant-numeric:tabular-nums}
  .card .pnote{font-size:14px;color:var(--paper-ink-2);line-height:1.5;padding-bottom:var(--s-3)}
  .card .pnote:last-of-type{border-bottom:1px solid var(--paper-hair)}
  /* margin-top:auto — the three columns stretch to the tallest, and a source
     note floating mid-column reads as unfinished. Pinned to the bottom. */
  .card .src{margin-top:auto;padding-top:var(--s-4);font-family:var(--mono);font-size:12px;letter-spacing:.02em;
    color:var(--paper-ink-3);line-height:1.5}
  .card .src a{color:var(--paper-ink);border-bottom:1px solid var(--paper-hair)}

  .caveat{margin:var(--s-5) 0 0;padding:var(--s-3) var(--s-4);border-left:2px solid var(--ember);
    font-size:14px;line-height:1.5;color:var(--paper-ink-2);max-width:var(--measure)}
  .caveat b{color:var(--paper-ink)}

  /* The one argument made in numbers: a black band, full bleed. */
  .math{background:#000;color:var(--white);padding:var(--s-9) 0}
  .math .k{color:var(--ember)}
  .math h2{font-size:clamp(28px,3.6vw,52px);max-width:18ch;margin-top:var(--s-4);color:var(--white)}
  .math p{margin-top:var(--s-4);color:rgba(255,255,255,.72);font-size:clamp(16px,1.3vw,18px);line-height:1.55;max-width:var(--measure)}
  .math p:first-of-type{margin-top:var(--s-6)}

  .cmp-sec{padding:var(--s-9) 0 0}
  .cmp-sec .k{color:var(--paper-ink-3)}
  .cmp-sec h2{font-size:clamp(28px,3.4vw,44px);max-width:16ch;margin-top:var(--s-4);color:var(--paper-ink)}

  /* The matrix: hairline rows, the mono for the answers, ours first. */
  .mwrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:var(--s-6)}
  .matrix{width:100%;border-collapse:collapse;font-size:15px}
  .matrix th,.matrix td{padding:var(--s-4) var(--s-3);border-bottom:1px solid var(--paper-hair);text-align:left;
    vertical-align:top}
  .matrix th:first-child,.matrix td:first-child{padding-left:0}
  .matrix thead th{font-family:var(--sans);font-weight:800;font-size:18px;letter-spacing:-.02em;
    color:var(--paper-ink);border-bottom:2px solid var(--paper-ink)}
  .matrix thead th.us{color:var(--paper-ink)}
  .matrix thead th.c{text-align:center}
  .matrix td.c{text-align:center;width:128px;white-space:nowrap;font-family:var(--mono);font-weight:600;
    font-variant-numeric:tabular-nums}
  .matrix .feat{color:var(--paper-ink);line-height:1.4;font-weight:700}
  .matrix .why{display:block;margin-top:var(--s-1);color:var(--paper-ink-2);font-size:14px;line-height:1.5;max-width:52ch}
  .yes{color:var(--paper-ink)}
  .no{color:var(--paper-ink-3)}
  .part{color:var(--paper-ink-2);font-size:13px}

  /* Where they beat us: honest, so it gets the same hairline rows. */
  .fair .pt{padding:var(--s-4) 0;border-top:1px solid var(--paper-hair);max-width:var(--measure)}
  .fair .pt:first-of-type{margin-top:var(--s-6)}
  .fair .pt:last-of-type{border-bottom:1px solid var(--paper-hair)}
  .fair .pt b{display:block;font-size:17px;color:var(--paper-ink);margin-bottom:var(--s-1)}
  .fair .pt span{color:var(--paper-ink-2);font-size:15px;line-height:1.55}

  .cmp-faq{padding-bottom:var(--s-9)}

  /* The close: black, one line, one button. */
  .closer{background:#000;color:var(--white);padding:var(--s-9) 0}
  .closer h2{font-size:clamp(32px,4.6vw,64px);max-width:14ch;color:var(--white)}
  .closer p{margin:var(--s-5) 0 0;color:rgba(255,255,255,.72);font-size:clamp(16px,1.4vw,19px);line-height:1.5;max-width:var(--measure)}
  .closer .act{margin-top:var(--s-6);display:flex;gap:var(--s-4);flex-wrap:wrap;align-items:center}
  .closer .note{margin-top:var(--s-4);font-family:var(--mono);font-size:12px;letter-spacing:.08em;
    text-transform:uppercase;color:rgba(255,255,255,.62)}

  @media (max-width:900px){
    .cmp-hero h1{font-size:clamp(36px,9vw,56px)}
    .cards{grid-template-columns:minmax(0,1fr);gap:var(--s-7)}
    .card .tag{min-height:0}
    /* Stacked, not scrolled. Horizontally scrolling a comparison table means
       reading one product at a time, which is the one thing the page exists to
       avoid. Each row becomes a card: the claim, then all three answers. */
    .mwrap{overflow-x:visible}
    .matrix{font-size:15px;min-width:0}
    .matrix thead{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
    .matrix,.matrix tbody,.matrix tr,.matrix td{display:block;width:100%}
    .matrix tr{padding:var(--s-4) 0;border-bottom:1px solid var(--paper-hair)}
    .matrix td{border:0;padding:0}
    .matrix td.c{display:inline-flex;align-items:baseline;gap:var(--s-2);width:auto;
      margin:var(--s-3) var(--s-4) 0 0;text-align:left}
    .matrix td.c::before{content:attr(data-l);font-family:var(--mono);font-weight:400;font-size:12px;
      letter-spacing:.06em;text-transform:uppercase;color:var(--paper-ink-3)}
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
    """The landing page's FAQ rows: a native <details> per question."""
    items = []
    for q, a in C.FAQ:
        items.append('<details class="faq-item"><summary class="faq-q">' + escape(q)
                     + '</summary><div class="faq-a">' + escape(a) + "</div></details>")
    return '<div class="faq-list">' + "".join(items) + "</div>"


def _comparison_schema() -> str:
    """ItemList of the three products, generated from compare_content.

    THE HONEST TYPE FOR THIS PAGE. There is no "comparison" schema, and dressing
    the page up as a Review or an AggregateRating would be asserting a rating
    nobody gave. An ItemList of SoftwareApplication says exactly what the page
    is: three named products with their prices, in a stated order.

    OUR OWN OFFERS ARE DERIVED from PLAN_LIMITS via compare_content; the
    competitor prices are the hand-checked figures shown on the page with their
    CHECKED_ON date, so the markup can never claim to be fresher than the page.
    """
    import json
    items = []
    for i, prod in enumerate(C.PRODUCTS, start=1):
        offers = [{
            "@type": "Offer",
            "name": pl.name,
            "price": "".join(ch for ch in pl.price if ch.isdigit() or ch == ".") or "0",
            "priceCurrency": "USD",
            "description": pl.note,
        } for pl in prod.plans]
        items.append({
            "@type": "ListItem",
            "position": i,
            "item": {
                "@type": "SoftwareApplication",
                "name": prod.name,
                "applicationCategory": "MultimediaApplication",
                "operatingSystem": "Web",
                "description": prod.tagline,
                "offers": offers,
            },
        })
    data = {"@context": "https://schema.org", "@type": "ItemList",
            "name": "Highlightz compared with Opus Clip and Eklipse",
            "itemListOrder": "https://schema.org/ItemListUnordered",
            "numberOfItems": len(items), "itemListElement": items}
    return ('<script type="application/ld+json">'
            + json.dumps(data, ensure_ascii=False) + "</script>")


# The bar's real height, written back as --nav-h. Same block as the landing
# page and the tutorial; no backslashes.
_JS = """
(function(){
  var nav = document.querySelector('.nav'), root = document.documentElement, last = 0;
  function measure(){
    if (!nav) return;
    var h = Math.round(nav.getBoundingClientRect().height);
    if (h && h !== last){ last = h; root.style.setProperty('--nav-h', h + 'px'); }
  }
  measure();
  window.addEventListener('resize', measure, { passive: true });
  if (nav && 'ResizeObserver' in window) new ResizeObserver(measure).observe(nav);
})();
"""


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
<meta property="og:image" content="https://highlightz.app/static/og-card-v4.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content=\"""" + escape(_TITLE) + """\">
<meta name="twitter:description" content=\"""" + escape(_DESC) + """\">
<meta name="twitter:image" content="https://highlightz.app/static/og-card-v4.png">
<link rel="alternate" type="text/markdown" href="https://highlightz.app/llms.txt" title="Highlightz for language models">
""" + _comparison_schema() + """
<style>""" + _CSS + """</style>
</head>
<body>

<nav class="nav">
  <a href="/" class="nav-logo"><img src="/static/logo-mark.png" alt="Highlightz"><span>Highlightz</span></a>
  <div class="nav-links">
    <a href="/#catches" class="nav-link">What it catches</a>
    <a href="/#score" class="nav-link">How it scores</a>
    <a href="/#watch" class="nav-link">Channels</a>
    <a href="/#pricing" class="nav-link">Pricing</a>
    <a href="/#faq" class="nav-link">FAQ</a>
    <a href="/tutorial" class="nav-link">Tutorial</a>
    <a href="/compare" class="nav-link on" aria-current="page">Compare</a>
  </div>
  <div class="nav-right">
    <a href="/login" class="nav-link">Sign in</a>
    <a href="/login" class="btn btn-go">Get started</a>
  </div>
</nav>

<header class="cmp-hero">
  <div class="wrap">
    <div class="k">Comparison</div>
    <h1 class="disp">""" + escape(C.HERO_TITLE) + """</h1>
    <p class="lead">""" + escape(C.HERO_LEAD) + """</p>
  </div>
</header>

<section class="cmp-cards">
  <div class="wrap">
    <div class="cards">""" + "".join(_card(p) for p in C.PRODUCTS) + """</div>
    """ + caveat + """
  </div>
</section>

<section class="math">
  <div class="wrap">
    <div class="k">""" + escape(C.THE_MATH["kicker"]) + """</div>
    <h2 class="disp">""" + escape(C.THE_MATH["title"]) + """</h2>
    """ + _paras(C.THE_MATH["body"]) + """
  </div>
</section>

<section class="cmp-sec" id="features">
  <div class="wrap">
    <div class="k">Feature by feature</div>
    <h2 class="disp">What each one is actually built for</h2>
    """ + _matrix() + """
  </div>
</section>

<section class="cmp-sec fair">
  <div class="wrap">
    <div class="k">""" + escape(C.THEY_DO_BETTER["kicker"]) + """</div>
    <h2 class="disp">""" + escape(C.THEY_DO_BETTER["title"]) + """</h2>
    """ + "".join('<div class="pt"><b>' + escape(t) + "</b><span>" + escape(d)
                  + "</span></div>" for t, d in C.THEY_DO_BETTER["points"]) + """
  </div>
</section>

<section class="cmp-sec cmp-faq" id="faq">
  <div class="wrap">
    <div class="k">Questions</div>
    <h2 class="disp">Before you decide</h2>
    """ + _faq() + """
  </div>
</section>

<section class="closer">
  <div class="wrap">
    <h2 class="disp">""" + escape(C.CLOSER["title"]) + """</h2>
    <p>""" + escape(C.CLOSER["body"]) + """</p>
    <div class="act">
      <a href="/login" class="btn btn-go btn-lg">""" + escape(C.CLOSER["cta"]) + """</a>
      <a href="/tutorial" class="btn btn-ghost btn-lg">Read the walkthrough</a>
    </div>
    <div class="note">""" + escape(C.CLOSER["cta_note"]) + """</div>
  </div>
</section>

<footer class="footer">
  <img src="/static/logo-mark.png" alt="Highlightz" width="374" height="501">
  <nav aria-label="Site"><a href="/tutorial">Tutorial</a><a href="/compare">Compare</a><a href="/tos">Terms of Service</a><a href="/privacy">Privacy Policy</a><a href="/cookies">Cookie Policy</a><a href="/opt-out">Streamer Opt-Out</a></nav>
  <span class="fl">&copy; 2026 ANTI Technology LLC</span>
</footer>

<script>""" + _JS + """</script>
</body>
</html>""")
