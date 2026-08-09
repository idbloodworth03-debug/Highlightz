"""Renders /tutorial from src/dashboard/tutorial_content.py.

WHY THIS IS A RENDERER AND NOT A TEMPLATE STRING. Every other page in this app
is one long HTML literal. That is fine for a page whose content never moves,
but the tutorial's whole job is to track a UI that keeps changing — so the copy
lives in a data file and this module only knows how to lay it out. Rewriting a
step or swapping a screenshot never touches markup.

TWO IMPLEMENTATION RULES, both learned the hard way in this repo:

  * NO f-STRINGS AROUND CSS OR JS. Both are full of braces, and an f-string
    eats them. _CSS and _JS are plain strings; f-strings are used only on
    data-driven fragments, which contain no braces.
  * NO BACKSLASHES IN THE JS. Python parses this file before the browser ever
    sees it, so an escape sequence here is not the one that reaches the page.
    The script below uses no regex and no escapes for that reason.

DESIGN. This is the landing page's system, not a new one: the same seven
palette tokens, the same three self-hosted faces, the same .wrap / .kicker /
.btn / .faq-item primitives, and the same nav and footer markup. It should read
as another room in the same building.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from src.dashboard import tutorial_content as C

_MEDIA_DIR = Path(__file__).parent / "static" / "tutorial"
_MEDIA_URL = "/static/tutorial/"


# ── helpers ──────────────────────────────────────────────────────────────────

def _bold(text: str) -> str:
    """**like this** -> <b>like this</b>.

    The content file quotes real UI labels constantly, and wrapping each one in
    raw <b> tags would make it unreadable to write and to review. Splitting on
    the delimiter avoids a regex (and therefore avoids backslashes).
    """
    parts = text.split("**")
    return "".join(p if i % 2 == 0 else "<b>" + p + "</b>"
                   for i, p in enumerate(parts))


def _exists(filename: str) -> bool:
    return (_MEDIA_DIR / filename).is_file()


def _placeholder(m: "C.Media") -> str:
    """What a slot looks like before its file has been captured.

    A broken image icon reads as a bug and makes the whole page look unfinished.
    A labelled box reads as "not filmed yet" and still tells the reader what
    they would be looking at — so the page is publishable before any capture
    run, and a failed run degrades instead of breaking.
    """
    kind = "Video" if m.kind == "video" else "Screenshot"
    return (
        '<div class="tm-ph" role="img" aria-label="' + escape(m.alt) + '">'
        '<span class="tm-ph-k">' + kind + " coming soon</span>"
        '<span class="tm-ph-f">' + escape(m.src) + "</span>"
        '<span class="tm-ph-a">' + escape(m.alt) + "</span>"
        "</div>"
    )


def media_html(m: "C.Media | None") -> str:
    """The one media component. Every slot on the page goes through here."""
    if m is None:
        return ""
    box_open = ('<figure class="tm" style="--tm-w:' + str(m.width)
                + ";--tm-h:" + str(m.height) + '">')

    webm = m.stem + ".webm"
    # A video needs EITHER encoding, not specifically the mp4. Keying the whole
    # block on m.src meant a missing .mp4 blanked a video whose .webm was sitting
    # right there and plays in every browser but Safari — which is exactly what
    # happened when .gitignore's `*.mp4` rule quietly dropped them from the
    # commit: the server had the webm and still drew "coming soon".
    have = _exists(m.src) or (m.kind == "video" and _exists(webm))
    if not have:
        return box_open + _placeholder(m) + "</figure>"

    if m.kind == "video":
        poster = _MEDIA_URL + m.poster_src if _exists(m.poster_src) else ""
        sources = ""
        if _exists(webm):
            sources += '<source src="' + _MEDIA_URL + webm + '" type="video/webm">'
        if _exists(m.src):
            sources += '<source src="' + _MEDIA_URL + m.src + '" type="video/mp4">'
        # autoplay+muted+loop makes it read like a GIF; the reduced-motion
        # branch in _JS strips autoplay and leaves the poster showing.
        vid = (
            '<video class="tm-v" muted loop playsinline preload="metadata" autoplay'
            + (' poster="' + poster + '"' if poster else "")
            + ' aria-label="' + escape(m.alt) + '">'
            + sources
            + "</video>"
        )
        btn = ('<button class="tm-play" type="button" '
               'aria-label="Play with sound and controls">Play with controls</button>')
        cap = ('<figcaption class="tm-cap">' + escape(m.caption) + "</figcaption>"
               if m.caption else "")
        return box_open + '<div class="tm-box">' + vid + btn + "</div>" + cap + "</figure>"

    img = ('<img class="tm-i" src="' + _MEDIA_URL + m.src + '" alt="' + escape(m.alt)
           + '" loading="lazy" decoding="async" width="' + str(m.width)
           + '" height="' + str(m.height) + '">')
    # The whole image is the lightbox trigger, so it is a button for keyboard
    # users rather than a click handler bolted onto an <img>.
    return (box_open + '<button class="tm-box tm-zoom" type="button" '
            'data-full="' + _MEDIA_URL + m.src + '" '
            'data-alt="' + escape(m.alt) + '" '
            'aria-label="Enlarge: ' + escape(m.alt) + '">'
            + img + '<span class="tm-mag" aria-hidden="true">Enlarge</span>'
            + "</button></figure>")


def _steps(section: "C.Section") -> str:
    if not section.steps:
        return ""
    items = "".join("<li>" + _bold(s) + "</li>" for s in section.steps)
    return '<ol class="tut-steps">' + items + "</ol>"


def _section(section: "C.Section", level: int = 2) -> str:
    h = "h" + str(level)
    plan = ('<span class="tut-plan">' + escape(section.plan) + "</span>"
            if section.plan else "")
    body = '<p class="tut-body">' + _bold(section.body) + "</p>" if section.body else ""
    note = '<p class="tut-note">' + _bold(section.note) + "</p>" if section.note else ""
    tip = ('<aside class="tut-tip"><span class="tut-tip-k">Tip</span><p>'
           + _bold(section.tip) + "</p></aside>") if section.tip else ""
    return (
        '<section class="tut-sec" id="' + section.id + '">'
        + "<" + h + ' class="tut-h">' + escape(section.title) + plan + "</" + h + ">"
        + body + _steps(section) + note + media_html(section.media) + tip
        + "</section>"
    )


def _toc_entries() -> list[tuple[str, str]]:
    out = [("overview", "Overview"), ("get-started", "Get started")]
    out += [(s.id, s.nav) for s in C.FEATURES]
    out += [("plans", "Plans"), ("questions", "Questions")]
    return out


def _plans_table() -> str:
    head, *rows = C.PLAN_ROWS
    ths = "".join("<th scope=\"col\">" + escape(c) + "</th>" if c else "<td></td>"
                  for c in head)
    trs = ""
    for r in rows:
        cells = "".join("<th scope=\"row\">" + escape(r[0]) + "</th>"
                        if i == 0 else "<td>" + escape(c) + "</td>"
                        for i, c in enumerate(r))
        trs += "<tr>" + cells + "</tr>"
    return ('<div class="tut-tablewrap"><table class="tut-table">'
            "<thead><tr>" + ths + "</tr></thead><tbody>" + trs + "</tbody></table></div>")


def _faq() -> str:
    items = ""
    for q, a in C.FAQ:
        items += ('<details class="faq-item"><summary><span class="faq-q">'
                  + escape(q) + '</span><span class="faq-c">+</span></summary>'
                  '<div class="faq-a">' + a + "</div></details>")
    return '<div class="faq-list">' + items + "</div>"


# ── CSS (plain string: braces everywhere) ────────────────────────────────────

_CSS = """
  @font-face{font-family:'Lobster';font-style:normal;font-weight:400;font-display:swap;src:url(/static/fonts/lobster-400.woff2) format('woff2')}
  @font-face{font-family:'Sora';font-style:normal;font-weight:100 900;font-display:swap;src:url(/static/fonts/sora-var.woff2) format('woff2')}
  @font-face{font-family:'Plex';font-style:normal;font-weight:400;font-display:swap;src:url(/static/fonts/plexmono-400.woff2) format('woff2')}
  @font-face{font-family:'Plex';font-style:normal;font-weight:600;font-display:swap;src:url(/static/fonts/plexmono-600.woff2) format('woff2')}

  :root{
    --void:#0E0B11; --wall:#1B1221; --bruise:#33203F;
    --glow:#B86ADC; --glow-ink:#C489E4; --flare:#D26AFB; --ember:#F7A745;
    --ink:#F2EAF7; --ink-2:#B9AEC4; --ink-3:#9C90A6;
    --hair:rgba(242,234,247,.085);
    --mono:'Plex',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Sora',system-ui,sans-serif;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{scroll-behavior:smooth;overflow-x:clip;scroll-padding-top:86px}
  /* NO overflow-x:hidden ON BODY. It computes overflow-y to `auto`, which makes
     body a scroll container — and then `position:sticky` children stick to
     body's scrollport instead of the viewport, so they never engage at all.
     That is why the sticky nav and TOC silently scrolled away. `overflow-x:clip`
     on <html> above stops sideways scrolling without creating a scroll
     container, which is exactly the difference between `clip` and `hidden`. */
  body{background:var(--void);color:var(--ink);font-family:var(--sans);font-weight:400;
    font-size:15.5px;line-height:1.68;
    -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}
  a{text-decoration:none;color:inherit}
  ::selection{background:rgba(210,106,251,.3);color:#fff}
  :focus-visible{outline:2px solid var(--flare);outline-offset:3px;border-radius:4px}

  .grain{position:fixed;inset:0;z-index:9;pointer-events:none;opacity:.032;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.82' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='180' height='180' filter='url(%23n)'/%3E%3C/svg%3E");
    background-size:180px 180px}

  .wrap{max-width:1140px;margin:0 auto;padding-left:26px;padding-right:26px}

  /* Nav copied from the landing page so the two pages share one header. */
  .nav{position:sticky;top:0;z-index:60;background:var(--void);
    border-bottom:1px solid var(--hair);display:flex;align-items:center;gap:18px;padding:13px 26px}
  .nav-logo{display:flex;align-items:center;gap:10px;flex-shrink:0}
  .nav-logo img{height:22px}
  .nav-logo span{font-family:var(--mono);font-weight:600;font-size:14px;letter-spacing:.12em;
    text-transform:uppercase;color:var(--ink)}
  .nav-links{display:flex;align-items:center;gap:2px;margin-left:14px}
  .nav-link{font-family:var(--mono);font-weight:400;font-size:12px;letter-spacing:.02em;
    color:var(--ink-3);padding:8px 11px;border-radius:3px}
  .nav-link:hover{color:var(--ink)}
  .nav-link.on{color:var(--glow-ink)}
  .nav-right{margin-left:auto;display:flex;align-items:center;gap:10px}

  .btn{display:inline-flex;align-items:center;justify-content:center;gap:9px;cursor:pointer;
    font-family:var(--sans);font-weight:600;font-size:14.5px;letter-spacing:-.005em;
    padding:13px 24px;border-radius:3px;border:1px solid transparent;color:var(--ink);
    transition:background .2s,color .2s;white-space:nowrap}
  .btn-key{background:linear-gradient(166deg,var(--bruise),#25172E) padding-box,
    linear-gradient(215deg,rgba(210,106,251,.75),rgba(184,106,220,.22) 40%,rgba(242,234,247,.05)) border-box}
  .btn-key:hover{background:linear-gradient(166deg,#3D2749,#2A1A33) padding-box,
    linear-gradient(215deg,rgba(210,106,251,.9),rgba(184,106,220,.3) 40%,rgba(242,234,247,.07)) border-box}
  .btn-quiet{background:linear-gradient(var(--wall),var(--wall)) padding-box,
    linear-gradient(215deg,rgba(184,106,220,.32),rgba(242,234,247,.05) 50%,rgba(242,234,247,.02)) border-box}
  .btn-lg{padding:16px 30px;font-size:15.5px}

  .kicker{font-family:var(--mono);font-weight:600;font-size:11px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--ember);display:flex;align-items:center;gap:12px}
  .kicker::after{content:'';flex:1;height:1px;max-width:190px;
    background:linear-gradient(90deg,rgba(247,167,69,.35),transparent)}

  /* ── Hero ── */
  .tut-hero{padding:56px 0 34px}
  .tut-hero h1{font-family:'Lobster',Georgia,serif;font-weight:400;
    font-size:clamp(38px,6vw,64px);line-height:1.04;letter-spacing:-.005em;margin:18px 0 18px}
  .tut-hero .lead{font-size:18px;color:var(--ink-2);max-width:620px;line-height:1.6}

  /* ── Layout: TOC rail + content ── */
  .tut-grid{display:block}
  .tut-toc{display:none}
  .tut-main{min-width:0;padding-bottom:40px}

  /* Mobile TOC: a real <details> so it is keyboard-operable with no JS. */
  .tut-toc-m{position:sticky;top:53px;z-index:40;margin:0 -26px 26px;
    background:var(--void);border-top:1px solid var(--hair);border-bottom:1px solid var(--hair)}
  .tut-toc-m summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:10px;
    padding:13px 26px;font-family:var(--mono);font-size:11.5px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--ink-2)}
  .tut-toc-m summary::-webkit-details-marker{display:none}
  .tut-toc-m .cx{margin-left:auto;transition:transform .25s}
  .tut-toc-m[open] .cx{transform:rotate(45deg);color:var(--flare)}
  .tut-toc-m ol{list-style:none;padding:2px 26px 14px}
  .tut-toc-m a{display:block;padding:9px 0;font-size:14px;color:var(--ink-2);
    border-bottom:1px solid var(--hair)}
  .tut-toc-m li:last-child a{border-bottom:none}

  .tut-sec{padding:34px 0;border-top:1px solid var(--hair)}
  .tut-sec:first-of-type{border-top:none}
  .tut-h{font-family:var(--sans);font-weight:700;font-size:clamp(22px,3vw,29px);
    line-height:1.18;letter-spacing:-.022em;margin-bottom:12px;display:flex;
    align-items:center;gap:12px;flex-wrap:wrap;scroll-margin-top:96px}
  .tut-plan{font-family:var(--mono);font-weight:600;font-size:10px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--ember);border:1px solid rgba(247,167,69,.4);
    border-radius:2px;padding:3px 8px}
  .tut-body{font-size:16px;color:var(--ink-2);max-width:68ch;line-height:1.66}
  .tut-body b,.tut-steps b{color:var(--ink);font-weight:600}
  .tut-steps{margin:20px 0 0;padding-left:0;list-style:none;counter-reset:tstep;max-width:68ch}
  .tut-steps li{counter-increment:tstep;position:relative;padding-left:38px;margin-bottom:11px;
    font-size:15px;color:var(--ink-2);line-height:1.6}
  .tut-steps li::before{content:counter(tstep);position:absolute;left:0;top:1px;
    width:24px;height:24px;border-radius:2px;display:grid;place-items:center;
    font-family:var(--mono);font-weight:600;font-size:11px;color:var(--glow-ink);
    background:var(--wall);border:1px solid rgba(184,106,220,.28)}
  .tut-note{margin-top:16px;font-size:14px;color:var(--ink-3);max-width:68ch;line-height:1.6}
  .tut-note b{color:var(--ink-2);font-weight:600}

  .tut-tip{margin-top:22px;max-width:68ch;border:1px solid transparent;border-radius:3px;
    padding:15px 18px;display:flex;gap:14px;align-items:flex-start;
    background:linear-gradient(var(--wall),var(--wall)) padding-box,
      linear-gradient(215deg,rgba(247,167,69,.4),rgba(247,167,69,.06) 45%,rgba(242,234,247,.02)) border-box}
  .tut-tip-k{font-family:var(--mono);font-weight:600;font-size:10px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--ember);flex-shrink:0;padding-top:3px}
  .tut-tip p{font-size:14.5px;color:var(--ink-2);line-height:1.62}
  .tut-tip b{color:var(--ink);font-weight:600}

  /* ── Media ── */
  .tm{margin:24px 0 0;max-width:820px}
  .tm-box{display:block;width:100%;position:relative;padding:0;border:1px solid transparent;
    border-radius:3px;overflow:hidden;cursor:zoom-in;background:
      linear-gradient(var(--wall),var(--wall)) padding-box,
      linear-gradient(215deg,rgba(184,106,220,.34),rgba(242,234,247,.05) 50%,rgba(242,234,247,.02)) border-box}
  .tm-i,.tm-v{display:block;width:100%;height:auto;aspect-ratio:var(--tm-w)/var(--tm-h)}
  .tm-v{cursor:pointer;background:#000}
  .tm-mag{position:absolute;right:10px;bottom:10px;font-family:var(--mono);font-size:10px;
    letter-spacing:.14em;text-transform:uppercase;color:var(--ink-2);
    background:rgba(14,11,17,.82);border:1px solid var(--hair);border-radius:2px;
    padding:5px 9px;opacity:0;transition:opacity .18s}
  .tm-box:hover .tm-mag,.tm-box:focus-visible .tm-mag{opacity:1}
  .tm-play{position:absolute;left:10px;bottom:10px;font-family:var(--mono);font-size:10px;
    letter-spacing:.14em;text-transform:uppercase;color:var(--ink);cursor:pointer;
    background:rgba(14,11,17,.82);border:1px solid rgba(184,106,220,.45);border-radius:2px;padding:6px 10px}
  .tm-play:hover{border-color:var(--flare)}
  .tm-cap{margin-top:10px;font-size:13.5px;color:var(--ink-3);line-height:1.6;max-width:68ch}

  .tm-ph{aspect-ratio:var(--tm-w)/var(--tm-h);border:1px dashed rgba(184,106,220,.3);
    border-radius:3px;background:var(--wall);display:flex;flex-direction:column;
    align-items:center;justify-content:center;gap:8px;padding:24px;text-align:center}
  .tm-ph-k{font-family:var(--mono);font-weight:600;font-size:11px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--glow-ink)}
  .tm-ph-f{font-family:var(--mono);font-size:11px;color:var(--ink-3)}
  .tm-ph-a{font-size:13.5px;color:var(--ink-3);max-width:44ch;line-height:1.55}

  /* ── Plans table ── */
  .tut-tablewrap{overflow-x:auto;margin-top:22px;-webkit-overflow-scrolling:touch}
  .tut-table{border-collapse:collapse;width:100%;min-width:460px;font-size:14.5px}
  .tut-table th,.tut-table td{padding:12px 14px;text-align:left;border-bottom:1px solid var(--hair)}
  .tut-table thead th{font-family:var(--mono);font-weight:600;font-size:11px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--ink-3)}
  .tut-table tbody th{font-weight:500;color:var(--ink-2)}
  .tut-table td{font-family:var(--mono);color:var(--ink)}
  .tut-table tr td:last-child{color:var(--glow-ink)}

  /* ── FAQ (same primitives as the landing page) ── */
  .faq-list{max-width:780px;margin:26px 0 0;border-top:1px solid var(--hair)}
  .faq-item{border-bottom:1px solid var(--hair)}
  .faq-item summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:16px;
    padding:19px 2px;font-size:16px;font-weight:600;letter-spacing:-.01em;
    -webkit-tap-highlight-color:transparent;transition:color .16s}
  .faq-item summary::-webkit-details-marker{display:none}
  .faq-item summary:hover{color:var(--glow-ink)}
  .faq-q{flex:1;min-width:0}
  .faq-c{flex-shrink:0;font-family:var(--mono);font-size:15px;color:var(--ink-3);transition:transform .25s,color .25s}
  .faq-item[open] .faq-c{transform:rotate(45deg);color:var(--flare)}
  .faq-a{padding:0 2px 20px;font-size:14.5px;color:var(--ink-2);line-height:1.72;max-width:70ch}
  .faq-a b{color:var(--ink);font-weight:600}
  .faq-a a{color:var(--glow-ink);border-bottom:1px solid rgba(184,106,220,.4)}

  /* ── Closing ── */
  .tut-cta{position:relative;text-align:center;padding:60px 0 30px;border-top:1px solid var(--hair);margin-top:34px}
  .tut-cta h2{font-family:'Lobster',Georgia,serif;font-weight:400;
    font-size:clamp(30px,4.6vw,46px);line-height:1.08;margin-bottom:16px}
  .tut-cta p{font-size:16.5px;color:var(--ink-2);max-width:520px;margin:0 auto 26px;line-height:1.6}
  .tut-support{max-width:640px;margin:34px auto 0;padding-top:22px;border-top:1px solid var(--hair);
    font-size:14.5px;color:var(--ink-2);line-height:1.7}
  .tut-support h3{font-family:var(--mono);font-weight:600;font-size:11px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--ink-3);margin-bottom:8px}
  .tut-support a{color:var(--glow-ink);border-bottom:1px solid rgba(184,106,220,.4)}
  .tut-support b{color:var(--ink);font-weight:600}

  .footer{border-top:1px solid var(--hair);padding:34px 26px;text-align:center;
    font-size:12px;color:var(--ink-3);line-height:1.8}
  .footer a{color:var(--ink-3);border-bottom:1px solid transparent}
  .footer a:hover{color:var(--ink-2);border-bottom-color:rgba(242,234,247,.2)}
  .footer .fl{margin-bottom:6px}

  /* ── Lightbox ── */
  .lb{border:none;padding:0;background:transparent;max-width:96vw;max-height:96vh}
  .lb::backdrop{background:rgba(8,6,10,.9)}
  .lb img{display:block;max-width:96vw;max-height:88vh;width:auto;height:auto;border-radius:3px}
  .lb-x{position:absolute;top:-40px;right:0;font-family:var(--mono);font-size:11px;
    letter-spacing:.14em;text-transform:uppercase;color:var(--ink-2);cursor:pointer;
    background:transparent;border:1px solid var(--hair);border-radius:2px;padding:7px 12px}
  .lb-x:hover{color:var(--ink);border-color:var(--ink-3)}
  .lb-wrap{position:relative}

  /* ── Desktop: sticky scroll-spy rail ── */
  @media(min-width:960px){
    .tut-grid{display:grid;grid-template-columns:212px 1fr;gap:56px;align-items:start}
    .tut-toc{display:block;position:sticky;top:86px;padding-bottom:40px}
    .tut-toc-m{display:none}
    .tut-toc-k{font-family:var(--mono);font-weight:600;font-size:10px;letter-spacing:.16em;
      text-transform:uppercase;color:var(--ink-3);margin-bottom:14px}
    .tut-toc ol{list-style:none}
    .tut-toc a{display:block;padding:7px 0 7px 14px;font-size:13.5px;color:var(--ink-3);
      border-left:1px solid var(--hair);transition:color .16s,border-color .16s}
    .tut-toc a:hover{color:var(--ink-2)}
    .tut-toc a.on{color:var(--glow-ink);border-left-color:var(--flare)}
  }
  @media(max-width:700px){
    .nav-links{display:none}
    .nav-logo span{display:none}
    .tut-hero{padding:34px 0 22px}
  }
  @media(prefers-reduced-motion:reduce){
    html{scroll-behavior:auto}
    *{animation-duration:.01ms !important;transition-duration:.01ms !important}
  }
"""


# ── JS (plain string: braces and no backslashes) ─────────────────────────────

_JS = """
(function(){
  // Scroll-spy. IntersectionObserver rather than a scroll handler so it costs
  // nothing while idle; rootMargin biases the "current" section toward the top
  // of the viewport, which is where a reader's eye actually is.
  var links = [].slice.call(document.querySelectorAll('[data-spy]'));
  var byId = {};
  links.forEach(function(a){ byId[a.getAttribute('data-spy')] = a; });
  var targets = Object.keys(byId).map(function(id){ return document.getElementById(id); })
                      .filter(Boolean);
  if (window.IntersectionObserver && targets.length) {
    var seen = {};
    var obs = new IntersectionObserver(function(entries){
      entries.forEach(function(e){ seen[e.target.id] = e.isIntersecting; });
      var current = null;
      targets.forEach(function(t){ if (seen[t.id] && !current) current = t.id; });
      links.forEach(function(a){
        a.classList.toggle('on', a.getAttribute('data-spy') === current);
      });
    }, { rootMargin: '-88px 0px -70% 0px', threshold: 0 });
    targets.forEach(function(t){ obs.observe(t); });
  }

  // Mobile TOC closes on pick, otherwise it covers the thing you jumped to.
  var mtoc = document.getElementById('toc-m');
  if (mtoc) {
    mtoc.addEventListener('click', function(ev){
      if (ev.target.tagName === 'A') mtoc.removeAttribute('open');
    });
  }

  // Reduced motion: never autoplay. The poster stays up and the reader opts in.
  var mq = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  if (mq && mq.matches) {
    [].forEach.call(document.querySelectorAll('video.tm-v'), function(v){
      v.removeAttribute('autoplay');
      v.autoplay = false;
      try { v.pause(); } catch (err) {}
    });
  }

  // Click a video (or its button) to get real controls and sound.
  function promote(v){
    v.controls = true;
    v.loop = false;
    v.muted = false;
    try { v.play(); } catch (err) {}
  }
  [].forEach.call(document.querySelectorAll('.tm-play'), function(btn){
    btn.addEventListener('click', function(){
      var v = btn.parentNode.querySelector('video');
      if (v) { promote(v); btn.style.display = 'none'; }
    });
  });
  [].forEach.call(document.querySelectorAll('video.tm-v'), function(v){
    v.addEventListener('click', function(){
      if (!v.controls) {
        promote(v);
        var b = v.parentNode.querySelector('.tm-play');
        if (b) b.style.display = 'none';
      }
    });
  });

  // Lightbox on a native <dialog>: Escape, focus return and the backdrop all
  // come free, which is most of what makes a hand-rolled modal inaccessible.
  var dlg = document.getElementById('lightbox');
  var dimg = document.getElementById('lightbox-img');
  if (dlg && dimg && dlg.showModal) {
    [].forEach.call(document.querySelectorAll('.tm-zoom'), function(b){
      b.addEventListener('click', function(){
        dimg.src = b.getAttribute('data-full');
        dimg.alt = b.getAttribute('data-alt') || '';
        dlg.showModal();
      });
    });
    var close = document.getElementById('lightbox-x');
    if (close) close.addEventListener('click', function(){ dlg.close(); });
    dlg.addEventListener('click', function(ev){ if (ev.target === dlg) dlg.close(); });
    dlg.addEventListener('close', function(){ dimg.removeAttribute('src'); });
  }
})();
"""


# ── page ─────────────────────────────────────────────────────────────────────

_TITLE = "How to use Highlightz — full walkthrough & setup guide"
_DESC = ("Step-by-step guide to Highlightz: connect Twitch, monitor a live channel, "
         "review and approve automatic clips, scan past VODs, and manage your plan. "
         "Free to start, no card required.")


def render() -> str:
    toc = _toc_entries()
    toc_links = "".join('<li><a href="#' + i + '" data-spy="' + i + '">'
                        + escape(lbl) + "</a></li>" for i, lbl in toc)

    quickstart = "".join(_section(s, level=3) for s in C.QUICKSTART)
    features = "".join(_section(s, level=2) for s in C.FEATURES)

    return (
"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>""" + escape(_TITLE) + """</title>
<meta name="description" content=\"""" + escape(_DESC) + """\">
<link rel="icon" type="image/png" href="/static/icon.png">
<link rel="canonical" href="https://highlightz.app/tutorial">
<link rel="preload" href="/static/fonts/sora-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/plexmono-600.woff2" as="font" type="font/woff2" crossorigin>
<meta property="og:type" content="article">
<meta property="og:site_name" content="Highlightz">
<meta property="og:url" content="https://highlightz.app/tutorial">
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
    <a href="/tutorial" class="nav-link on">Tutorial</a>
    <a href="/#features" class="nav-link">Features</a>
    <a href="/#pricing" class="nav-link">Pricing</a>
    <a href="/#faq" class="nav-link">FAQ</a>
  </div>
  <div class="nav-right">
    <a href="/login" class="nav-link">Sign in</a>
    <a href="/login" class="btn btn-key" style="padding:10px 18px;font-size:13.5px">Get started</a>
  </div>
</nav>

<header class="wrap tut-hero" id="overview">
  <div class="kicker">Walkthrough</div>
  <h1>""" + escape(C.HERO_TITLE) + """</h1>
  <p class="lead">""" + escape(C.HERO_LEAD) + """</p>
  """ + media_html(C.HERO_MEDIA) + """
</header>

<div class="wrap">
  <div class="tut-grid">
    <nav class="tut-toc" aria-label="On this page">
      <div class="tut-toc-k">On this page</div>
      <ol>""" + toc_links + """</ol>
    </nav>

    <details class="tut-toc-m" id="toc-m">
      <summary>On this page<span class="cx">+</span></summary>
      <ol>""" + toc_links + """</ol>
    </details>

    <main class="tut-main">
      <section class="tut-sec" id="get-started">
        <h2 class="tut-h">""" + escape(C.QUICKSTART_TITLE) + """</h2>
        <p class="tut-body">""" + escape(C.QUICKSTART_LEAD) + """</p>
        """ + quickstart + """
      </section>

      """ + features + """

      <section class="tut-sec" id="plans">
        <h2 class="tut-h">""" + escape(C.PLANS_TITLE) + """</h2>
        """ + _plans_table() + """
      </section>

      <section class="tut-sec" id="questions">
        <h2 class="tut-h">""" + escape(C.FAQ_TITLE) + """</h2>
        <p class="tut-body">""" + escape(C.FAQ_LEAD) + """</p>
        """ + _faq() + """
      </section>

      <section class="tut-cta">
        <h2>""" + escape(C.CTA_TITLE) + """</h2>
        <p>""" + escape(C.CTA_BODY) + """</p>
        <a href="/login" class="btn btn-key btn-lg">""" + escape(C.CTA_BUTTON) + """</a>
        <div class="tut-support">
          <h3>""" + escape(C.SUPPORT_TITLE) + """</h3>
          <p>""" + C.SUPPORT_BODY + """</p>
        </div>
      </section>
    </main>
  </div>
</div>

<dialog class="lb" id="lightbox" aria-label="Enlarged screenshot">
  <div class="lb-wrap">
    <button class="lb-x" id="lightbox-x" type="button">Close</button>
    <img id="lightbox-img" alt="">
  </div>
</dialog>

<footer class="footer">
  <div class="fl">&copy; 2026 ANTI Technology LLC &mdash; All rights reserved.</div>
  <a href="/tutorial">Tutorial</a> &middot; <a href="/tos">Terms of Service</a> &middot; <a href="/privacy">Privacy Policy</a> &middot; <a href="/cookies">Cookie Policy</a> &middot; <a href="/opt-out">Streamer Opt-Out</a>
</footer>

<script>""" + _JS + """</script>
</body>
</html>""")
