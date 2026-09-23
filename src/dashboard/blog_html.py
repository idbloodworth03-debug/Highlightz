"""Renders /blog and /blog/<slug> from src/dashboard/blog_content.py.

Same split as /compare and /tutorial: every claim lives in the content
module, this file only lays it out, and the design system is imported from
tutorial_html rather than copied — the bar fixed over the top, a black hero
in the display voice, the paper ground with hairline rows and the mono for
numbers, a black close, the one-row footer.

WHAT IS PARTICULAR TO THE BLOG:
  * Every platform profile answers blog_content.REQ_FIELDS in the same
    order, and an answer no source states renders as a visible "Not
    published", never as an empty cell.
  * Logos. A platform's logo is shown only when its official file has been
    put in static/blog/logos/<slug>.(svg|png|webp) — taken from the brand's
    own press kit. Until then the profile shows a lettered tile, which is
    plainly not anyone's logo. Drawing a company's mark by hand is how a page
    ends up showing one that is wrong.
  * The chart is HTML, not an SVG: the labels stay at reading size on a
    phone, where a scaled SVG would shrink them. One series, one ink, a
    direct value label on every bar, a hover title, and the same numbers as
    a table under it.
  * Every outbound link is rel="nofollow noopener": the page names five
    companies and endorses none of them.

TWO RULES, same as the tutorial and for the same reasons:
  * No f-strings around CSS or JS — both are full of braces.
  * No backslashes in embedded JS — Python parses this file before the browser
    sees it, so an escape here is not the one that reaches the page.
"""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path

from src.dashboard import blog_content as B
from src.dashboard.tutorial_html import BASE_CSS

_SITE = "https://highlightz.app"
_STATIC = Path(__file__).parent / "static"
_LOGO_DIR = _STATIC / "blog" / "logos"
_LOGO_EXTS = (".svg", ".png", ".webp")

# The chart's scale: dollars per 1,000 views, 0 to this.
_CHART_MAX = 6.0
_CHART_TICKS = (0, 2, 4, 6)


_CSS = BASE_CSS + """
  /* ── blog-specific ───────────────────────────────────────────────────── */
  .blog-hero{background:#000;color:var(--white);padding:calc(var(--nav-h) + var(--s-8)) 0 var(--s-9)}
  .blog-hero .k{color:var(--ember)}
  .blog-hero h1{font-size:clamp(40px,5.6vw,80px);max-width:16ch;margin-top:var(--s-4);color:var(--white)}
  .blog-hero .lead{margin:var(--s-5) 0 0;font-size:clamp(16px,1.4vw,19px);line-height:1.5;
    color:rgba(255,255,255,.72);max-width:var(--measure)}
  .blog-hero .meta{margin-top:var(--s-5);font-family:var(--mono);font-size:12px;letter-spacing:.08em;
    text-transform:uppercase;color:rgba(255,255,255,.62)}
  .blog-hero .meta a{color:var(--white);border-bottom:1px solid rgba(255,255,255,.35)}

  /* Back to the index: at the top of every article, and again where the
     reading ends, so nobody has to scroll up or hunt for the bar (which
     hides its links on a phone) to get back to the list. */
  .back{display:inline-flex;align-items:center;gap:var(--s-2);padding:var(--s-2) 0;
    font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.12em;text-transform:uppercase;
    color:rgba(255,255,255,.72);transition:color var(--dur-fast)}
  .back .ar{display:inline-block;font-size:16px;line-height:1;transition:transform var(--dur-fast) var(--ease)}
  .back:hover{color:var(--white)}
  .back:hover .ar{transform:translateX(-4px)}
  .blog-hero .back{margin-bottom:var(--s-5)}
  .art-body > .back{margin-top:var(--s-7);color:var(--paper-ink-2);border:0}
  .art-body > .back:hover{color:var(--paper-ink)}

  .blog-sec{padding:var(--s-9) 0 0}
  .blog-sec.end{padding-bottom:var(--s-9)}
  .blog-sec > .wrap > .k{color:var(--paper-ink-3)}
  .blog-sec h2.disp{font-size:clamp(28px,3.4vw,44px);max-width:18ch;margin-top:var(--s-4);color:var(--paper-ink)}
  .blog-sec .sub{margin-top:var(--s-4);font-size:17px;line-height:1.55;color:var(--paper-ink-2);max-width:var(--measure)}

  /* The index: each guide a hairline row, the landing page's FAQ rhythm. */
  .posts{margin-top:var(--s-6);list-style:none;max-width:960px}
  .post{display:grid;grid-template-columns:48px minmax(0,1fr) auto;column-gap:var(--s-5);align-items:baseline;
    padding:var(--s-5) 0;border-top:1px solid var(--paper-hair)}
  .posts li:last-child .post{border-bottom:1px solid var(--paper-hair)}
  .post .n{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.12em;color:var(--ember)}
  .post .pk{font-family:var(--mono);font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--paper-ink-3)}
  .post h3{font-family:var(--sans);font-weight:800;font-size:clamp(20px,2vw,26px);letter-spacing:-.025em;
    line-height:1.15;color:var(--paper-ink);margin-top:var(--s-1)}
  .post p{margin-top:var(--s-2);font-size:15px;line-height:1.55;color:var(--paper-ink-2);max-width:64ch}
  .post .go{font-family:var(--mono);font-size:12px;letter-spacing:.12em;text-transform:uppercase;
    color:var(--paper-ink-3);white-space:nowrap;transition:color var(--dur-fast)}
  .post:hover h3{text-decoration:underline;text-decoration-thickness:2px;text-underline-offset:4px}
  .post:hover .go{color:var(--paper-ink)}

  /* The five platforms at a glance: logo, name, fit, one line. */
  .glance{margin-top:var(--s-6);display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:var(--s-5)}
  .gl{display:flex;flex-direction:column;gap:var(--s-3);padding-top:var(--s-4);border-top:2px solid var(--paper-ink);min-width:0}
  .gl.strong{border-top-color:var(--ember)}
  .gl b{font-family:var(--sans);font-weight:800;font-size:18px;letter-spacing:-.02em;line-height:1.2;color:var(--paper-ink)}
  .gl span.d{font-size:14px;line-height:1.5;color:var(--paper-ink-2)}
  .gl:hover b{text-decoration:underline;text-underline-offset:4px}

  /* Logos: the official file in a white tile, or a lettered tile that is
     plainly not a logo. Same box either way so the rows line up. */
  .logo{width:48px;height:48px;flex:none;border:1px solid var(--paper-hair);border-radius:8px;background:#fff;
    display:flex;align-items:center;justify-content:center;overflow:hidden}
  .logo img{max-width:80%;max-height:80%;width:auto;height:auto;display:block}
  .logo.mono{font-family:var(--sans);font-weight:800;font-size:20px;letter-spacing:-.02em;color:var(--paper-ink)}

  /* Fit: always a word, never a colour alone. */
  .fit{display:inline-flex;align-items:center;gap:var(--s-2);font-family:var(--mono);font-weight:600;font-size:12px;
    letter-spacing:.1em;text-transform:uppercase;border:1px solid currentColor;border-radius:3px;padding:4px 8px;
    white-space:nowrap;align-self:flex-start}
  .fit.strong{color:#8A4B00}
  .fit.good{color:var(--paper-ink)}
  .fit.limited{color:var(--paper-ink-3)}
  .fit .fk{font-weight:400;opacity:.8}

  /* ── the article ── */
  .art{padding:var(--s-9) 0}
  .art-body{max-width:880px}
  .art-body > p,.art-body > ul,.art-body > .note{max-width:var(--measure)}
  .art-body > p{margin-top:var(--s-4);font-size:17px;line-height:1.6;color:var(--paper-ink-2)}
  .art-body > h2{font-family:var(--sans);font-weight:800;font-size:clamp(24px,2.6vw,34px);letter-spacing:-.03em;
    line-height:1.1;color:var(--paper-ink);margin-top:var(--s-8);scroll-margin-top:calc(var(--nav-h) + var(--s-5))}
  .art-body > h2:first-child{margin-top:0}
  .art-body a{color:var(--paper-ink);border-bottom:1px solid var(--paper-ink-3)}
  .art-body a:hover{border-bottom-color:var(--paper-ink)}
  .art-body > ul{margin-top:var(--s-4);list-style:none}
  .art-body > ul li{position:relative;padding:var(--s-3) 0 var(--s-3) var(--s-5);border-top:1px solid var(--paper-hair);
    font-size:16px;line-height:1.55;color:var(--paper-ink-2)}
  .art-body > ul li:last-child{border-bottom:1px solid var(--paper-hair)}
  .art-body > ul li::before{content:"";position:absolute;left:0;top:24px;width:8px;height:2px;background:var(--ember)}
  .note{margin-top:var(--s-7);padding:var(--s-3) var(--s-4);border-left:2px solid var(--ember);
    font-size:14px;line-height:1.55;color:var(--paper-ink-2)}

  /* A requirement table: the question in the sans, the answer beside it. */
  .req{width:100%;border-collapse:collapse;margin-top:var(--s-5);font-size:15px}
  .req th,.req td{padding:var(--s-3) var(--s-4) var(--s-3) 0;border-bottom:1px solid var(--paper-hair);
    text-align:left;vertical-align:top;line-height:1.5}
  .req tr:first-child th,.req tr:first-child td{border-top:1px solid var(--paper-hair)}
  .req th{width:30%;font-weight:700;color:var(--paper-ink)}
  .req td{color:var(--paper-ink-2)}
  .np{font-family:var(--mono);font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--paper-ink-3);
    border:1px dashed var(--paper-hair);border-radius:3px;padding:4px 8px;white-space:nowrap}

  /* A platform profile. */
  .plat{margin-top:var(--s-8);padding-top:var(--s-6);border-top:2px solid var(--paper-ink);
    scroll-margin-top:calc(var(--nav-h) + var(--s-5))}
  .plat.strong{border-top-color:var(--ember)}
  .plat-h{display:flex;align-items:center;gap:var(--s-4);flex-wrap:wrap}
  .plat-h h3{font-family:var(--sans);font-weight:800;font-size:clamp(26px,2.8vw,36px);letter-spacing:-.035em;
    line-height:1;color:var(--paper-ink);margin:0}
  .plat-h .fit{margin-left:auto;align-self:center}
  .plat .site{display:inline-block;margin-top:var(--s-3);font-family:var(--mono);font-size:12px;letter-spacing:.06em;
    color:var(--paper-ink-3)}
  .plat .site a{color:var(--paper-ink);border-bottom:1px solid var(--paper-hair)}
  .plat .sum{margin-top:var(--s-4);font-size:17px;line-height:1.55;color:var(--paper-ink);max-width:var(--measure)}
  .facts{margin-top:var(--s-5);display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--s-5)}
  .facts dt{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.12em;text-transform:uppercase;
    color:var(--paper-ink-3)}
  .facts dd{margin-top:var(--s-2);font-size:15px;line-height:1.55;color:var(--paper-ink-2)}
  .plat .lbl{margin-top:var(--s-6);font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.12em;
    text-transform:uppercase;color:var(--paper-ink-3)}
  .plat .req{margin-top:var(--s-3)}
  .watch{margin-top:var(--s-3);list-style:none;max-width:var(--measure)}
  .watch li{position:relative;padding:var(--s-2) 0 var(--s-2) var(--s-5);font-size:15px;line-height:1.55;color:var(--paper-ink-2)}
  .watch li::before{content:"!";position:absolute;left:0;top:8px;font-family:var(--mono);font-weight:600;font-size:14px;
    color:var(--ember)}
  .fitbox{margin-top:var(--s-6);display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--s-5)}
  .fitbox > div{border-left:2px solid var(--ember);padding:var(--s-1) 0 var(--s-1) var(--s-4)}
  .fitbox > div.plain{border-left-color:var(--paper-ink)}
  .fitbox h4{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.12em;text-transform:uppercase;
    color:var(--paper-ink)}
  .fitbox p{margin-top:var(--s-2);font-size:15px;line-height:1.55;color:var(--paper-ink-2)}
  .psrc{margin-top:var(--s-5);font-family:var(--mono);font-size:12px;letter-spacing:.02em;line-height:1.7;
    color:var(--paper-ink-3)}
  .psrc a{color:var(--paper-ink-2);border-bottom:1px solid var(--paper-hair)}

  /* What Highlightz does: the tutorial's rows. */
  .hz{margin-top:var(--s-5);list-style:none;max-width:var(--measure)}
  .hz li{padding:var(--s-4) 0;border-top:1px solid var(--paper-hair)}
  .hz li:last-child{border-bottom:1px solid var(--paper-hair)}
  .hz b{display:block;font-size:17px;color:var(--paper-ink);margin-bottom:var(--s-1)}
  .hz span{font-size:15px;line-height:1.55;color:var(--paper-ink-2)}

  /* Pictures: real product screens, in the landing page's frame. */
  .art-body .tm{margin-top:var(--s-6)}
  .blog-shot .tm-box{cursor:default}

  /* The chart: one series, one ink, the value written on every bar. */
  .chart{margin-top:var(--s-5);max-width:880px}
  .chart-t{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.12em;text-transform:uppercase;
    color:var(--paper-ink-3)}
  .bars{margin-top:var(--s-4)}
  /* The gridlines are drawn by each row's track, so the rows carry NO
     vertical padding and the track stretches to the full row height:
     that is what makes the four segments of each line meet as one
     unbroken rule, whatever height a row's label wraps to. The label and
     the value take the breathing room instead. The axis row repeats the
     same background as short ticks, so each line ends on its dollar
     figure. */
  .brow,.baxis{display:grid;grid-template-columns:200px minmax(0,1fr) 112px;column-gap:var(--s-4)}
  .brow{align-items:stretch}
  .brow .bl{align-self:center;padding:var(--s-3) 0;font-size:15px;line-height:1.3;color:var(--paper-ink)}
  .brow .bt,.baxis .ax{position:relative;border-left:1px solid var(--paper-hair);border-right:1px solid var(--paper-hair);
    background:linear-gradient(to right,transparent calc(33.3333% - 1px),var(--paper-hair) calc(33.3333% - 1px),
      var(--paper-hair) 33.3333%,transparent 33.3333%,transparent calc(66.6667% - 1px),var(--paper-hair) calc(66.6667% - 1px),
      var(--paper-hair) 66.6667%,transparent 66.6667%) no-repeat}
  .brow .bt{min-height:48px}
  .brow .bv{position:absolute;top:50%;height:12px;min-width:4px;transform:translateY(-50%);background:var(--paper-ink);
    border-radius:4px;cursor:default}
  .brow .bv:hover{background:#26252B;outline:2px solid var(--ember);outline-offset:2px}
  .brow .bn{align-self:center;padding:var(--s-3) 0;font-family:var(--mono);font-weight:600;font-size:14px;
    color:var(--paper-ink);white-space:nowrap;font-variant-numeric:tabular-nums}
  .baxis .ax{height:32px;background-size:100% 8px;border-left:0;border-right:0}
  /* The two end lines, as 8px ticks like the middle two. */
  .baxis .ax::before,.baxis .ax::after{content:"";position:absolute;top:0;width:1px;height:8px;background:var(--paper-hair)}
  .baxis .ax::before{left:0}
  .baxis .ax::after{right:0}
  .baxis .ax span{position:absolute;top:12px;transform:translateX(-50%);font-family:var(--mono);font-size:12px;
    line-height:1.5;color:var(--paper-ink-3);font-variant-numeric:tabular-nums}
  .baxis .ax span:first-child{transform:none}
  .baxis .ax span:last-child{transform:translateX(-100%)}
  .chart details{margin-top:var(--s-4)}
  .chart summary{cursor:pointer;font-family:var(--mono);font-size:12px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--paper-ink-2)}
  .chart summary:hover{color:var(--paper-ink)}
  .ctab{width:100%;border-collapse:collapse;margin-top:var(--s-3);font-size:14px}
  .ctab th,.ctab td{padding:var(--s-2) var(--s-3) var(--s-2) 0;border-bottom:1px solid var(--paper-hair);text-align:left;
    vertical-align:top;line-height:1.5}
  .ctab thead th{font-weight:700;color:var(--paper-ink);border-bottom:2px solid var(--paper-ink)}
  .ctab td{color:var(--paper-ink-2)}
  .ctab td.num{font-family:var(--mono);font-weight:600;color:var(--paper-ink);white-space:nowrap;
    font-variant-numeric:tabular-nums}
  .chart .csrc{margin-top:var(--s-3);font-family:var(--mono);font-size:12px;line-height:1.7;color:var(--paper-ink-3)}
  .chart .csrc a{color:var(--paper-ink-2);border-bottom:1px solid var(--paper-hair)}

  /* Sources, numbered, at the foot of every article. */
  .srcs{margin-top:var(--s-5);list-style:none;counter-reset:src;max-width:880px}
  .srcs li{counter-increment:src;position:relative;padding:var(--s-3) 0 var(--s-3) var(--s-7);
    border-top:1px solid var(--paper-hair);font-size:15px;line-height:1.5;overflow-wrap:anywhere}
  .srcs li:last-child{border-bottom:1px solid var(--paper-hair)}
  .srcs li::before{content:counter(src);position:absolute;left:0;top:12px;font-family:var(--mono);font-weight:600;
    font-size:12px;letter-spacing:.08em;line-height:2;color:var(--ember)}
  .srcs a{color:var(--paper-ink);border-bottom:1px solid var(--paper-hair)}
  .srcs .host{display:block;font-family:var(--mono);font-size:12px;color:var(--paper-ink-3)}

  /* The close: black, one line, one button. */
  .closer{background:#000;color:var(--white);padding:var(--s-9) 0}
  .closer h2{font-size:clamp(32px,4.6vw,64px);max-width:14ch;color:var(--white)}
  .closer p{margin:var(--s-5) 0 0;color:rgba(255,255,255,.72);font-size:clamp(16px,1.4vw,19px);line-height:1.5;max-width:var(--measure)}
  .closer .act{margin-top:var(--s-6);display:flex;gap:var(--s-4);flex-wrap:wrap;align-items:center}

  @media (max-width:1100px){
    .glance{grid-template-columns:repeat(3,minmax(0,1fr))}
  }
  @media (max-width:900px){
    .blog-hero h1{font-size:clamp(36px,9vw,56px)}
    .glance{grid-template-columns:repeat(2,minmax(0,1fr))}
    .facts,.fitbox{grid-template-columns:minmax(0,1fr)}
    /* Stacked, not scrolled: the question over its answer. */
    .req,.req tbody,.req tr,.req th,.req td{display:block;width:100%}
    .req tr{padding:var(--s-3) 0;border-bottom:1px solid var(--paper-hair)}
    .req tr:first-child{border-top:1px solid var(--paper-hair)}
    .req th,.req td,.req tr:first-child th,.req tr:first-child td{border:0;padding:0}
    .req td{margin-top:var(--s-1)}
    .plat-h .fit{margin-left:0}
  }
  @media (max-width:700px){
    .post{grid-template-columns:32px minmax(0,1fr);column-gap:var(--s-3)}
    .post .go{display:none}
    .glance{grid-template-columns:minmax(0,1fr)}
    .gl{flex-direction:row;flex-wrap:wrap;align-items:center}
    .gl b{flex:1 1 auto}
    .gl span.d{flex-basis:100%}
    /* On a phone the label sits on its own line above the bar, so a
       gridline could only be drawn in broken pieces. There are none here:
       every bar carries its figure, and the axis keeps its ticks. */
    .brow,.baxis{grid-template-columns:minmax(0,1fr) 96px}
    .brow .bl{grid-column:1 / -1;padding:var(--s-3) 0 0}
    .brow .bt{min-height:32px;background:none;border-color:transparent}
    .brow .bn{padding:0}
    .baxis > span:first-child{display:none}
  }
"""


# ── pieces ───────────────────────────────────────────────────────────────────

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _a(href: str, label: str) -> str:
    """A link. Our own paths are plain; anything else leaves the site, and
    names a company we do not vouch for, so it is nofollow and opens apart."""
    if href.startswith("/"):
        return '<a href="' + escape(href) + '">' + label + "</a>"
    return ('<a href="' + escape(href) + '" target="_blank" rel="nofollow noopener">'
            + label + "</a>")


def _inline(text: str) -> str:
    """Escaped text with [label](href) turned into links — the only markup
    the content module is allowed."""
    out, i = [], 0
    for m in _LINK.finditer(text):
        out.append(escape(text[i:m.start()]))
        out.append(_a(m.group(2), escape(m.group(1))))
        i = m.end()
    out.append(escape(text[i:]))
    return "".join(out)


def _host(url: str) -> str:
    h = url.split("//", 1)[-1].split("/", 1)[0]
    return h[4:] if h.startswith("www.") else h


def logo_file(slug: str) -> str | None:
    """The official logo's published path, if one has been added."""
    for ext in _LOGO_EXTS:
        if (_LOGO_DIR / (slug + ext)).is_file():
            return "/static/blog/logos/" + slug + ext
    return None


def _logo(p: "B.Platform") -> str:
    src = logo_file(p.slug)
    if src:
        return ('<span class="logo"><img src="' + escape(src) + '" alt="'
                + escape(p.name) + ' logo" width="40" height="40" loading="lazy"></span>')
    return '<span class="logo mono" aria-hidden="true">' + escape(p.name[:1]) + "</span>"


def _fit(fit: str) -> str:
    return ('<span class="fit ' + fit.lower() + '"><span class="fk">Highlightz fit</span>'
            + escape(fit) + "</span>")


def _answer(value: str) -> str:
    if value == B.NOT_PUBLISHED:
        return '<span class="np">Not published</span>'
    return _inline(value)


def _req(rows) -> str:
    return ('<table class="req"><tbody>'
            + "".join('<tr><th scope="row">' + escape(k) + "</th><td>" + _answer(v) + "</td></tr>"
                      for k, v in rows)
            + "</tbody></table>")


def _sources_inline(sources) -> str:
    return ", ".join(_a(s.url, escape(s.label)) for s in sources)


def _platform(p: "B.Platform") -> str:
    strong = " strong" if p.fit == "Strong" else ""
    return (
        '<section class="plat' + strong + '" id="' + escape(p.slug) + '">'
        '<div class="plat-h">' + _logo(p) + "<h3>" + escape(p.name) + "</h3>" + _fit(p.fit) + "</div>"
        '<span class="site">' + _a(p.url, escape(_host(p.url))) + "</span>"
        '<p class="sum">' + _inline(p.summary) + "</p>"
        '<dl class="facts"><div><dt>Who posts campaigns</dt><dd>' + _inline(p.who_posts)
        + "</dd></div><div><dt>How pay works</dt><dd>" + _inline(p.pay) + "</dd></div></dl>"
        '<div class="lbl">Requirements</div>' + _req(p.reqs)
        + '<div class="lbl">Know before you join</div><ul class="watch">'
        + "".join("<li>" + _inline(w) + "</li>" for w in p.watch_outs) + "</ul>"
        '<div class="fitbox"><div class="plain"><h4>How Highlightz fits</h4><p>' + _inline(p.fit_why)
        + "</p></div><div><h4>Our suggestion</h4><p>" + _inline(p.tip) + "</p></div></div>"
        '<p class="psrc">Checked ' + escape(B.CHECKED_ON) + ". Sources: "
        + _sources_inline(p.sources) + ".</p>"
        "</section>"
    )


def _glance(href_base: str) -> str:
    """The five as a row of tiles: logo, name, fit, who posts."""
    return '<div class="glance">' + "".join(
        '<a class="gl' + (" strong" if p.fit == "Strong" else "") + '" href="'
        + escape(href_base + "#" + p.slug) + '">' + _logo(p)
        + "<b>" + escape(p.name) + "</b>" + _fit(p.fit)
        + '<span class="d">' + escape(p.summary.split(". ")[0].rstrip(".")) + ".</span></a>"
        for p in B.PLATFORMS) + "</div>"


def _money(x: float) -> str:
    return "$" + format(x, ".2f")


def _pct(x: float) -> str:
    return format(100.0 * x / _CHART_MAX, ".2f") + "%"


def _chart() -> str:
    rows = []
    for label, lo, hi, what in B.PAY_PER_1K:
        rng = _money(lo) + "–" + _money(hi)
        tip = label + ": " + rng + " per 1,000 views. " + what + "."
        rows.append(
            '<div class="brow"><span class="bl">' + escape(label) + "</span>"
            '<span class="bt"><span class="bv" style="left:' + _pct(lo) + ";width:" + _pct(hi - lo)
            + '" title="' + escape(tip) + '"></span></span>'
            '<span class="bn">' + escape(rng) + "</span></div>")
    axis = ('<div class="baxis" aria-hidden="true"><span></span><span class="ax">'
            + "".join('<span style="left:' + _pct(t) + '">$' + str(t) + "</span>" for t in _CHART_TICKS)
            + "</span><span></span></div>")
    summary = "; ".join(l + " " + _money(lo) + " to " + _money(hi) for l, lo, hi, _ in B.PAY_PER_1K)
    table = ('<details><summary>The numbers, as a table</summary><table class="ctab"><thead><tr>'
             "<th>Route</th><th>Per 1,000 views</th><th>What it covers</th></tr></thead><tbody>"
             + "".join("<tr><td>" + escape(l) + '</td><td class="num">' + escape(_money(lo) + "–" + _money(hi))
                       + "</td><td>" + escape(w) + "</td></tr>" for l, lo, hi, w in B.PAY_PER_1K)
             + "</tbody></table></details>")
    return ('<figure class="chart"><figcaption class="chart-t">Typical pay per 1,000 views, US dollars</figcaption>'
            '<div class="bars" role="img" aria-label="' + escape("Typical pay per 1,000 views: " + summary + ".")
            + '">' + "".join(rows) + axis + "</div>" + table
            + '<p class="csrc">Typical ranges, not guarantees; rates vary by country, niche and '
              "campaign. Checked " + escape(B.CHECKED_ON) + ". Sources: "
            + _sources_inline(B.PAY_SOURCES) + ".</p></figure>")


def _shot(key: str) -> str:
    file, alt = B.SHOTS[key]
    return ('<figure class="tm blog-shot"><div class="tm-box" style="--tm-w:1200;--tm-h:750">'
            '<img class="tm-i" src="/static/landing/' + escape(file) + '" width="1200" height="750" '
            'loading="lazy" decoding="async" alt="' + escape(alt) + '"></div>'
            '<figcaption class="tm-cap">' + escape(alt) + ".</figcaption></figure>")


def _highlightz() -> str:
    return ('<ul class="hz">' + "".join("<li><b>" + escape(t) + "</b><span>" + escape(d) + "</span></li>"
                                        for t, d in B.highlightz_points()) + "</ul>")


def _block(b: tuple) -> str:
    kind = b[0]
    if kind == "p":
        return "<p>" + _inline(b[1]) + "</p>"
    if kind == "h":
        return '<h2 id="' + _anchor(b[1]) + '">' + escape(b[1]) + "</h2>"
    if kind == "list":
        return "<ul>" + "".join("<li>" + _inline(x) + "</li>" for x in b[1]) + "</ul>"
    if kind == "req":
        return _req(b[1])
    if kind == "chart":
        return _chart()
    if kind == "platforms":
        return _glance("") + "".join(_platform(p) for p in B.PLATFORMS)
    if kind == "highlightz":
        return _highlightz()
    if kind == "shot":
        return _shot(b[1])
    if kind == "note":
        return '<p class="note">' + _inline(b[1]) + "</p>"
    raise ValueError("unknown blog block: " + repr(kind))


def _anchor(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _words(a: "B.Article") -> int:
    n = len(a.lead.split())
    for b in a.blocks:
        if b[0] in ("p", "h", "note"):
            n += len(b[1].split())
        elif b[0] == "list":
            n += sum(len(x.split()) for x in b[1])
        elif b[0] == "req":
            n += sum(len(k.split()) + len(v.split()) for k, v in b[1])
        elif b[0] == "platforms":
            for p in B.PLATFORMS:
                n += sum(len(t.split()) for t in (p.summary, p.who_posts, p.pay, p.fit_why, p.tip))
                n += sum(len(v.split()) for _, v in p.reqs)
    return n


def read_minutes(a: "B.Article") -> int:
    return max(1, round(_words(a) / 230))


# ── the page shell ───────────────────────────────────────────────────────────

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


def _schema(data: dict) -> str:
    return ('<script type="application/ld+json">'
            + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + "</script>")


def _org() -> dict:
    return {"@type": "Organization", "name": "Highlightz", "url": _SITE + "/",
            "logo": _SITE + "/static/icon.png"}


def _head(title: str, desc: str, path: str, og_type: str, schema: str) -> str:
    url = _SITE + path
    return (
"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>""" + escape(title) + """</title>
<meta name="description" content=\"""" + escape(desc) + """\">
<link rel="icon" type="image/png" href="/static/icon.png">
<link rel="canonical" href=\"""" + url + """\">
<link rel="preload" href="/static/fonts/sora-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/plexmono-600.woff2" as="font" type="font/woff2" crossorigin>
<meta property="og:type" content=\"""" + og_type + """\">
<meta property="og:site_name" content="Highlightz">
<meta property="og:url" content=\"""" + url + """\">
<meta property="og:title" content=\"""" + escape(title) + """\">
<meta property="og:description" content=\"""" + escape(desc) + """\">
<meta property="og:image" content="https://highlightz.app/static/og-card-v6.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content=\"""" + escape(title) + """\">
<meta name="twitter:description" content=\"""" + escape(desc) + """\">
<meta name="twitter:image" content="https://highlightz.app/static/og-card-v6.png">
<link rel="alternate" type="text/markdown" href="https://highlightz.app/llms.txt" title="Highlightz for language models">
""" + schema + """
<style>""" + _CSS + """</style>
</head>
<body>
""")


def _nav(on_index: bool) -> str:
    cur = ' class="nav-link on" aria-current="page"' if on_index else ' class="nav-link on"'
    return """
<nav class="nav">
  <a href="/" class="nav-logo"><img src="/static/logo-mark.png" alt="Highlightz"><span>Highlightz</span></a>
  <div class="nav-links">
    <a href="/#catches" class="nav-link">What it catches</a>
    <a href="/#score" class="nav-link">How it scores</a>
    <a href="/#watch" class="nav-link">Channels</a>
    <a href="/#pricing" class="nav-link">Pricing</a>
    <a href="/#faq" class="nav-link">FAQ</a>
    <a href="/tutorial" class="nav-link">Tutorial</a>
    <a href="/compare" class="nav-link">Compare</a>
    <a href="/blog\"""" + cur + """>Blog</a>
  </div>
  <div class="nav-right">
    <a href="/login" class="nav-link">Sign in</a>
    <a href="/login" class="btn btn-go">Get started</a>
  </div>
</nav>
"""


_TAIL = """
<section class="closer">
  <div class="wrap">
    <h2 class="disp">Be first to the moment.</h2>
    <p>Highlightz watches the channels you clip while they are live and hands you the moments as they happen. Free to start, no card needed.</p>
    <div class="act">
      <a href="/login" class="btn btn-go btn-lg">Start clipping free</a>
      <a href="/tutorial" class="btn btn-ghost btn-lg">Read the walkthrough</a>
    </div>
  </div>
</section>

<footer class="footer">
  <img src="/static/logo-mark.png" alt="Highlightz" width="374" height="501">
  <nav aria-label="Site"><a href="/tutorial">Tutorial</a><a href="/compare">Compare</a><a href="/blog">Blog</a><a href="/tos">Terms of Service</a><a href="/privacy">Privacy Policy</a><a href="/cookies">Cookie Policy</a><a href="/opt-out">Streamer Opt-Out</a></nav>
  <span class="fl">&copy; 2026 ANTI Technology LLC</span>
</footer>

<script>""" + _JS + """</script>
</body>
</html>"""


def _back() -> str:
    return ('<a class="back" href="/blog"><span class="ar" aria-hidden="true">&larr;</span>'
            "All guides</a>")


def _post_rows(articles) -> str:
    out = []
    for i, a in enumerate(articles, start=1):
        out.append('<li><a class="post" href="/blog/' + escape(a.slug) + '">'
                   '<span class="n">' + format(i, "02d") + "</span><span>"
                   '<span class="pk">' + escape(a.kicker) + " &middot; " + str(read_minutes(a)) + " min read</span>"
                   "<h3>" + escape(a.title) + "</h3><p>" + escape(a.description) + "</p></span>"
                   '<span class="go">Read &rarr;</span></a></li>')
    return '<ul class="posts">' + "".join(out) + "</ul>"


# ── pages ────────────────────────────────────────────────────────────────────

def _posting(a: "B.Article") -> dict:
    return {"@type": "BlogPosting", "headline": a.title, "description": a.description,
            "url": _SITE + "/blog/" + a.slug, "datePublished": B.PUBLISHED,
            "dateModified": B.PUBLISHED, "author": _org(), "publisher": _org(),
            "image": _SITE + "/static/og-card-v6.png"}


def render_index() -> str:
    schema = _schema({"@context": "https://schema.org", "@type": "Blog",
                      "name": "Highlightz — " + B.INDEX_TITLE, "url": _SITE + "/blog",
                      "description": B.INDEX_DESC, "publisher": _org(),
                      "blogPost": [_posting(a) for a in B.ARTICLES]})
    return (
        _head(B.INDEX_TITLE + " — the Highlightz blog", B.INDEX_DESC, "/blog", "website", schema)
        + _nav(True)
        + """
<header class="blog-hero">
  <div class="wrap">
    <div class="k">Blog</div>
    <h1 class="disp">""" + escape(B.INDEX_TITLE) + """</h1>
    <p class="lead">""" + escape(B.INDEX_LEAD) + """</p>
    <div class="meta">Every figure checked """ + escape(B.CHECKED_ON) + """ &middot; No paid or affiliate links</div>
  </div>
</header>

<section class="blog-sec" id="guides">
  <div class="wrap">
    <div class="k">The guides</div>
    <h2 class="disp">Start with the money, then the platforms</h2>
    """ + _post_rows(B.ARTICLES) + """
  </div>
</section>

<section class="blog-sec" id="platforms">
  <div class="wrap">
    <div class="k">The five platforms</div>
    <h2 class="disp">Where clippers get paid, and how well Highlightz fits</h2>
    <p class="sub">""" + escape(B.PLATFORMS_INTRO) + """</p>
    """ + _glance("/blog/clipping-platforms") + """
  </div>
</section>

<section class="blog-sec end" id="rates">
  <div class="wrap">
    <div class="k">At a glance</div>
    <h2 class="disp">What 1,000 views is worth</h2>
    """ + _chart() + """
  </div>
</section>
""" + _TAIL)


def render_article(slug: str) -> str | None:
    a = B.article(slug)
    if a is None:
        return None
    path = "/blog/" + a.slug
    post = _posting(a)
    post["@context"] = "https://schema.org"
    post["mainEntityOfPage"] = _SITE + path
    post["citation"] = [s.url for s in a.sources]
    others = [x for x in B.ARTICLES if x.slug != a.slug]
    return (
        _head(a.title, a.description, path, "article", _schema(post))
        + _nav(False)
        + """
<header class="blog-hero">
  <div class="wrap">
    """ + _back() + """
    <div class="k">""" + escape(a.kicker) + """</div>
    <h1 class="disp">""" + escape(a.title) + """</h1>
    <p class="lead">""" + escape(a.lead) + """</p>
    <div class="meta">Checked """ + escape(B.CHECKED_ON) + " &middot; " + str(len(a.sources))
        + """ sources &middot; <a href="#sources">see them</a> &middot; """ + str(read_minutes(a)) + """ min read</div>
  </div>
</header>

<main class="art">
  <div class="wrap">
    <div class="art-body">
      """ + "".join(_block(b) for b in a.blocks) + _back() + """
    </div>
  </div>
</main>

<section class="blog-sec" id="sources">
  <div class="wrap">
    <div class="k">Sources</div>
    <h2 class="disp">Where these figures come from</h2>
    <ol class="srcs">""" + "".join(
            "<li>" + _a(s.url, escape(s.label)) + '<span class="host">' + escape(_host(s.url))
            + "</span></li>" for s in a.sources) + """</ol>
  </div>
</section>

<section class="blog-sec end" id="more">
  <div class="wrap">
    <div class="k">Keep reading</div>
    <h2 class="disp">More guides</h2>
    """ + _post_rows(others) + """
  </div>
</section>
""" + _TAIL)
