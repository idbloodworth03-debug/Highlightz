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

DESIGN (2026-09-02, after the landing v4 rebuild). The walkthrough wears the
landing page's system: the bar fixed over the top, a black hero with the
display sans at its heaviest weight and a product screen hanging off the
hero's bottom edge, then the paper ground for the reading — hairline rows,
the mono for labels and numerals, the orange for the one accent, black
buttons on paper and the orange button on black — and the landing's own
one-row footer. No script face, no purple below the bar: the violet stays in
the logo. It should read as another room in the same building.

/compare imports the same sheet as BASE_CSS and lays its own rules on top.
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
        + "<" + h + ' class="tut-h disp">' + escape(section.title) + plan + "</" + h + ">"
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
    """The landing page's FAQ rows, exactly: a native <details> per question,
    the question as the summary, a sign that turns when it opens."""
    items = ""
    for q, a in C.FAQ:
        items += ('<details class="faq-item"><summary class="faq-q">'
                  + escape(q) + '</summary>'
                  '<div class="faq-a">' + a + "</div></details>")
    return '<div class="faq-list">' + items + "</div>"


# ── CSS (plain string: braces everywhere) ────────────────────────────────────

_CSS = """
  @font-face{font-family:'Sora';font-style:normal;font-weight:100 900;font-display:swap;src:url(/static/fonts/sora-var.woff2) format('woff2')}
  /* METRIC-MATCHED FALLBACK, measured on the landing page: size-adjust scales
     Arial to Sora's advance and the overrides restate Sora's ascent/descent,
     so the line box is the same height before and after the swap. */
  @font-face{font-family:'Sora Fallback';font-style:normal;font-weight:100 900;
    src:local('Arial'),local('Helvetica'),local('Liberation Sans');
    size-adjust:114.4%;ascent-override:84.8%;descent-override:25.3%;line-gap-override:0%}
  @font-face{font-family:'Plex';font-style:normal;font-weight:400;font-display:swap;src:url(/static/fonts/plexmono-400.woff2) format('woff2')}
  @font-face{font-family:'Plex';font-style:normal;font-weight:600;font-display:swap;src:url(/static/fonts/plexmono-600.woff2) format('woff2')}

  /* The landing page's tokens, the ones this page uses. Same names, same
     values, so a change there is a change here. */
  :root{
    --void:#0E0B11;
    --ink:#F2EAF7; --ink-2:#B9AEC4; --ink-3:#9C90A6;
    --hair:rgba(242,234,247,.085);
    --paper:#F4F4F2; --paper-ink:#0A0A0C; --paper-ink-2:#4B4A50; --paper-ink-3:#77767C;
    --paper-hair:rgba(10,10,12,.14);
    --white:#FFFFFF; --ember:#F7A745;
    --mono:'Plex',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Sora','Sora Fallback',system-ui,sans-serif;
    --ease:cubic-bezier(.16,1,.3,1);
    --dur-fast:150ms; --dur-slow:400ms;
    --s-1:4px; --s-2:8px; --s-3:12px; --s-4:16px; --s-5:24px;
    --s-6:32px; --s-7:48px; --s-8:64px; --s-9:96px;
    --measure:52ch;
    --nav-h:71px;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{scroll-behavior:smooth;overflow-x:clip;scroll-padding-top:96px}
  /* NO overflow-x:hidden ON BODY. It computes overflow-y to `auto`, which makes
     body a scroll container — and then `position:sticky` children stick to
     body's scrollport instead of the viewport, so they never engage at all.
     `overflow-x:clip` on <html> above stops sideways scrolling without
     creating a scroll container. */
  body{background:var(--paper);color:var(--paper-ink-2);font-family:var(--sans);font-weight:400;
    font-size:16px;line-height:1.6;
    -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}
  a{text-decoration:none;color:inherit}
  ::selection{background:rgba(247,167,69,.35);color:#fff}
  :focus-visible{outline:2px solid var(--ember);outline-offset:3px;border-radius:2px}
  a:focus-visible,button:focus-visible{outline:2px solid var(--ember);outline-offset:3px;border-radius:2px}

  .wrap{width:100%;max-width:1280px;margin:0 auto;padding-left:clamp(20px,4.5vw,72px);padding-right:clamp(20px,4.5vw,72px)}
  /* The display voice: the sans at 800, tight. */
  .disp{font-family:var(--sans);font-weight:800;letter-spacing:-.04em;line-height:.98;margin:0}
  .k{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.16em;text-transform:uppercase}

  /* ── The bar, the landing page's: fixed over the top, no lines. ── */
  .nav{position:fixed;top:0;left:0;right:0;z-index:60;
    background:linear-gradient(180deg,#09070C 0%,#09070C 70%,rgba(9,7,12,0) 100%);
    display:flex;align-items:center;gap:16px;padding:12px 24px 16px}
  .nav-logo{display:flex;align-items:center;gap:8px;flex-shrink:0}
  .nav-logo img{height:22px}
  .nav-logo span{font-family:var(--mono);font-weight:600;font-size:14px;letter-spacing:.12em;
    text-transform:uppercase;color:var(--ink)}
  .nav-links{display:flex;align-items:center;gap:4px;margin-left:12px}
  .nav-link{font-family:var(--mono);font-weight:400;font-size:12px;letter-spacing:.02em;
    color:var(--ink-3);padding:8px 12px;border-radius:3px;
    transition:color var(--dur-fast),background var(--dur-fast)}
  .nav-link:hover{color:var(--ink);background:rgba(242,234,247,.05)}
  .nav-link.on{color:var(--ink)}
  .nav-right{margin-left:auto;display:flex;align-items:center;gap:8px}
  /* Wide screens: the bar at reading size. Below 1360px the bar above is
     already as wide as the room allows (the links collapse at 1000), so the
     larger size only applies where it fits on one line. `.nav .nav-right
     .btn` rather than `.nav .btn`: that rule comes later in the sheet and
     would otherwise win. */
  @media(min-width:1360px){
    .nav{padding:16px 32px 24px;gap:24px}
    .nav-logo{gap:12px}
    .nav-logo img{height:28px}
    .nav-logo span{font-size:16px}
    .nav-links{gap:4px;margin-left:16px}
    .nav-link{font-size:15px;padding:8px 12px}
    .nav-right{gap:12px}
    .nav .nav-right .btn{font-size:16px;padding:12px 24px}
  }
  /* 1000, the landing page's number: the bar needs ~990px with its links
     shown since the Blog tab joined it. */
  @media(max-width:1000px){
    .nav-links{display:none}
  }

  /* ── Buttons, the landing page's three. ── */
  .btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;cursor:pointer;
    font-family:var(--sans);font-weight:700;font-size:15px;letter-spacing:-.005em;
    padding:12px 24px;border-radius:3px;border:1px solid transparent;color:var(--ink);
    transition:background var(--dur-fast),color var(--dur-fast),border-color var(--dur-fast);
    white-space:nowrap}
  .btn-lg{padding:16px 32px;font-size:16px}
  .btn-go{background:var(--ember);border-color:var(--ember);color:#0A0A0C}
  .btn-go:hover{background:#FFB65A;border-color:#FFB65A}
  .btn-go:active{transform:translateY(1px)}
  .btn-ghost{background:transparent;border-color:rgba(255,255,255,.35);color:var(--white)}
  .btn-ghost:hover{border-color:var(--white)}
  .btn-dark{background:var(--paper-ink);border-color:var(--paper-ink);color:var(--white)}
  .btn-dark:hover{background:#26252B;border-color:#26252B}
  .btn-dark:active{transform:translateY(1px)}
  .nav .btn{font-size:14px;padding:8px 16px}

  /* ── Hero. Black, under the bar; the kicker in the orange, the title in
     the display voice, and the dashboard hanging off the bottom edge the way
     the product screens hang off the landing page's proof section. ── */
  .tut-hero{background:#000;color:var(--white);overflow:hidden;
    padding-top:calc(var(--nav-h) + var(--s-8))}
  .tut-hero-in{display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);
    column-gap:clamp(32px,5vw,96px);align-items:end}
  .tut-hero .k{color:var(--ember)}
  .tut-hero h1{font-size:clamp(40px,5.6vw,84px);max-width:12ch;margin-top:var(--s-4);color:var(--white)}
  .tut-hero .lead{margin:var(--s-5) 0 0;font-size:clamp(16px,1.4vw,19px);line-height:1.5;
    color:rgba(255,255,255,.72);max-width:var(--measure)}
  .tut-hero-act{margin:var(--s-6) 0 var(--s-8);display:flex;gap:var(--s-4);flex-wrap:wrap}
  .tut-hero-shot .tm{margin:0;max-width:none;margin-bottom:calc(-1 * var(--s-7))}
  .tut-hero-shot .tm-box{border-radius:10px;border:1px solid rgba(255,255,255,.09);
    box-shadow:0 30px 60px -10px rgba(0,0,0,.9),0 80px 120px -40px rgba(0,0,0,.9)}

  /* ── Layout: the rail + the reading. ── */
  .tut-page{padding-top:var(--s-9);padding-bottom:var(--s-9)}
  .tut-grid{display:block}
  .tut-toc{display:none}
  .tut-main{min-width:0}

  /* Mobile TOC: a real <details> so it is keyboard-operable with no JS. */
  .tut-toc-m{position:sticky;top:0;z-index:40;margin:0 0 var(--s-6);
    background:var(--paper);border-top:1px solid var(--paper-hair);border-bottom:1px solid var(--paper-hair)}
  .tut-toc-m summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px;
    padding:12px 0;font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--paper-ink)}
  .tut-toc-m summary::-webkit-details-marker{display:none}
  .tut-toc-m .cx{margin-left:auto;font-weight:400;font-size:18px;color:var(--paper-ink-3);
    transition:transform var(--dur-fast) var(--ease)}
  .tut-toc-m[open] .cx{transform:rotate(45deg);color:var(--paper-ink)}
  .tut-toc-m ol{list-style:none;padding:4px 0 12px}
  .tut-toc-m a{display:block;padding:8px 0;font-family:var(--mono);font-size:12px;letter-spacing:.06em;
    text-transform:uppercase;color:var(--paper-ink-2);border-bottom:1px solid var(--paper-hair)}
  .tut-toc-m li:last-child a{border-bottom:none}

  .tut-sec{padding:var(--s-8) 0;border-top:1px solid var(--paper-hair)}
  .tut-sec:first-of-type{border-top:none;padding-top:0}
  .tut-h{font-size:clamp(28px,3.4vw,44px);color:var(--paper-ink);display:flex;
    align-items:center;gap:var(--s-3);flex-wrap:wrap;scroll-margin-top:calc(var(--nav-h) + var(--s-5))}
  h3.tut-h{font-size:clamp(22px,2.4vw,30px);margin-top:var(--s-7)}
  .tut-plan{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--ember);border:1px solid var(--ember);
    border-radius:3px;padding:4px 8px;letter-spacing:.12em}
  .tut-body{margin-top:var(--s-4);font-size:17px;color:var(--paper-ink-2);max-width:var(--measure);line-height:1.55}
  .tut-body b,.tut-steps b,.tut-note b,.tut-tip b{color:var(--paper-ink);font-weight:700}
  .tut-steps{margin:var(--s-5) 0 0;list-style:none;counter-reset:tstep;max-width:var(--measure)}
  /* overflow-wrap: the steps quote a full Twitch VOD URL in bold — 317px of
     text with no break opportunity, wider than the column on any phone. */
  .tut-steps li{counter-increment:tstep;position:relative;padding:12px 0 12px 48px;
    border-top:1px solid var(--paper-hair);overflow-wrap:anywhere;
    font-size:16px;color:var(--paper-ink-2);line-height:1.5}
  .tut-steps li:last-child{border-bottom:1px solid var(--paper-hair)}
  .tut-steps li::before{content:"0" counter(tstep);position:absolute;left:0;top:12px;
    font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.12em;line-height:2;
    color:var(--ember)}
  .tut-note{margin-top:var(--s-4);font-size:14px;color:var(--paper-ink-3);max-width:var(--measure);line-height:1.6}

  .tut-tip{margin-top:var(--s-5);max-width:var(--measure);border-left:2px solid var(--ember);
    padding:8px 0 8px 16px;display:flex;gap:var(--s-3);align-items:flex-start}
  .tut-tip-k{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--ember);flex-shrink:0;padding-top:4px}
  .tut-tip p{font-size:15px;color:var(--paper-ink-2);line-height:1.55}

  /* ── Media: a product screen in the landing page's frame. ── */
  .tm{margin:var(--s-5) 0 0;max-width:880px}
  .tm-box{display:block;width:100%;position:relative;padding:0;border:1px solid var(--paper-hair);
    border-radius:8px;overflow:hidden;cursor:zoom-in;background:#000;
    box-shadow:0 30px 60px -30px rgba(0,0,0,.35)}
  .tm-i,.tm-v{display:block;width:100%;height:auto;aspect-ratio:var(--tm-w)/var(--tm-h)}
  .tm-v{cursor:pointer;background:#000}
  .tm-mag{position:absolute;right:12px;bottom:12px;font-family:var(--mono);font-size:12px;
    letter-spacing:.14em;text-transform:uppercase;color:#0A0A0C;
    background:var(--ember);border-radius:3px;padding:4px 8px;opacity:0;
    transition:opacity var(--dur-fast)}
  .tm-box:hover .tm-mag,.tm-box:focus-visible .tm-mag{opacity:1}
  .tm-play{position:absolute;left:12px;bottom:12px;font-family:var(--mono);font-size:12px;
    letter-spacing:.14em;text-transform:uppercase;color:var(--ink);cursor:pointer;
    background:rgba(0,0,0,.7);border:1px solid rgba(255,255,255,.35);border-radius:3px;padding:4px 8px}
  .tm-play:hover{border-color:var(--white)}
  .tm-cap{margin-top:8px;font-size:14px;color:var(--paper-ink-3);line-height:1.6;max-width:var(--measure)}

  .tm-ph{aspect-ratio:var(--tm-w)/var(--tm-h);border:1px dashed var(--paper-hair);
    border-radius:8px;background:#fff;display:flex;flex-direction:column;
    align-items:center;justify-content:center;gap:8px;padding:24px;text-align:center}
  .tm-ph-k{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--ember)}
  .tm-ph-f{font-family:var(--mono);font-size:12px;color:var(--paper-ink-3)}
  .tm-ph-a{font-size:14px;color:var(--paper-ink-3);max-width:44ch;line-height:1.5}

  /* ── Plans: the pricing columns' rows, as one table. ── */
  .tut-tablewrap{overflow-x:auto;margin-top:var(--s-5);-webkit-overflow-scrolling:touch}
  .tut-table{border-collapse:collapse;width:100%;min-width:460px;font-size:15px}
  .tut-table th,.tut-table td{padding:12px 12px;text-align:left;border-bottom:1px solid var(--paper-hair)}
  .tut-table thead th{font-family:var(--sans);font-weight:800;font-size:20px;letter-spacing:-.02em;
    color:var(--paper-ink);border-bottom:2px solid var(--paper-ink)}
  .tut-table thead td{border-bottom:2px solid var(--paper-ink)}
  .tut-table tbody th{font-weight:400;color:var(--paper-ink-2)}
  .tut-table td{font-family:var(--mono);font-weight:600;color:var(--paper-ink);font-variant-numeric:tabular-nums}
  .tut-table th:first-child{padding-left:0}

  /* ── FAQ (the landing page's rows). ── */
  .faq-list{max-width:820px;margin:var(--s-5) 0 0}
  .faq-item{border-top:1px solid var(--paper-hair)}
  .faq-item:last-child{border-bottom:1px solid var(--paper-hair)}
  .faq-q{display:flex;align-items:baseline;justify-content:space-between;gap:var(--s-5);
    padding:var(--s-4) 0;cursor:pointer;list-style:none;
    font-family:var(--sans);font-weight:700;font-size:clamp(17px,1.4vw,20px);
    letter-spacing:-.015em;line-height:1.3;color:var(--paper-ink)}
  .faq-q::-webkit-details-marker{display:none}
  .faq-q::after{content:'+';flex:none;font-family:var(--mono);font-weight:400;font-size:22px;
    line-height:1;color:var(--paper-ink-3);transition:transform var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease)}
  .faq-item[open] .faq-q::after{transform:rotate(45deg);color:var(--paper-ink)}
  .faq-q:hover::after{color:var(--paper-ink)}
  /* overflow-wrap, because the answers quote real URLs — a full Twitch VOD
     link is 317px of text with no break opportunity in it. */
  .faq-a{margin:0;padding:0 var(--s-8) var(--s-5) 0;font-size:16px;line-height:1.55;
    color:var(--paper-ink-2);max-width:var(--measure);overflow-wrap:anywhere}
  .faq-a b{color:var(--paper-ink);font-weight:700}
  .faq-a a{color:var(--paper-ink);border-bottom:1px solid var(--paper-hair)}

  /* ── Closing: back to black, one line, one button, then the footer. ── */
  .tut-cta{background:#000;color:var(--white);padding:var(--s-9) 0}
  .tut-cta h2{font-size:clamp(32px,4.6vw,64px);max-width:14ch;color:var(--white)}
  .tut-cta p{margin:var(--s-5) 0 0;font-size:clamp(16px,1.4vw,19px);line-height:1.5;
    color:rgba(255,255,255,.72);max-width:var(--measure)}
  .tut-cta-act{margin-top:var(--s-6);display:flex;gap:var(--s-4);flex-wrap:wrap}
  .tut-support{margin-top:var(--s-8);padding-top:var(--s-5);border-top:1px solid var(--hair);
    max-width:var(--measure);font-size:15px;color:var(--ink-2);line-height:1.6}
  .tut-support h3{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--ember);margin-bottom:var(--s-2)}
  .tut-support a{color:var(--white);border-bottom:1px solid rgba(255,255,255,.35)}
  .tut-support b{color:var(--white);font-weight:700}

  .footer{background:#000;border-top:1px solid var(--hair);
    padding:var(--s-5) clamp(20px,4.5vw,72px);display:flex;align-items:center;gap:var(--s-5);
    flex-wrap:wrap;font-family:var(--mono);font-size:12px;letter-spacing:.06em;color:var(--ink-3)}
  .footer img{height:20px;width:auto;display:block}
  .footer nav{display:flex;flex-wrap:wrap;gap:var(--s-2) var(--s-4)}
  .footer a:hover{color:var(--ink)}
  .footer .fl{margin-left:auto;white-space:nowrap}

  /* ── Lightbox ── */
  .lb{border:none;padding:0;background:transparent;max-width:96vw;max-height:96vh}
  .lb::backdrop{background:rgba(0,0,0,.92)}
  .lb img{display:block;max-width:96vw;max-height:88vh;width:auto;height:auto;border-radius:8px}
  .lb-x{position:absolute;top:-48px;right:0;font-family:var(--mono);font-size:12px;
    letter-spacing:.14em;text-transform:uppercase;color:var(--ink);cursor:pointer;
    background:transparent;border:1px solid rgba(255,255,255,.35);border-radius:3px;padding:8px 12px}
  .lb-x:hover{border-color:var(--white)}
  .lb-wrap{position:relative}

  /* ── Desktop: the sticky rail. ── */
  @media(min-width:960px){
    .tut-grid{display:grid;grid-template-columns:220px minmax(0,1fr);column-gap:clamp(32px,5vw,96px);align-items:start}
    .tut-toc{display:block;position:sticky;top:calc(var(--nav-h) + var(--s-5))}
    .tut-toc-m{display:none}
    .tut-toc-k{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.16em;
      text-transform:uppercase;color:var(--paper-ink-3);margin-bottom:var(--s-3)}
    .tut-toc ol{list-style:none}
    .tut-toc a{display:block;padding:8px 0 8px 12px;font-family:var(--mono);font-size:12px;
      letter-spacing:.06em;text-transform:uppercase;color:var(--paper-ink-3);
      border-left:1px solid var(--paper-hair);
      transition:color var(--dur-fast),border-color var(--dur-fast)}
    .tut-toc a:hover{color:var(--paper-ink)}
    .tut-toc a.on{color:var(--paper-ink);border-left-color:var(--ember)}
  }
  @media(max-width:900px){
    .tut-hero-in{grid-template-columns:minmax(0,1fr);row-gap:var(--s-6)}
    .tut-hero h1{font-size:clamp(36px,9vw,56px)}
    .tut-hero-shot .tm{margin-bottom:calc(-1 * var(--s-6))}
  }
  @media(max-width:700px){
    .nav-logo span{display:none}
    .tut-page{padding-top:var(--s-8);padding-bottom:var(--s-8)}
    .tut-sec{padding:var(--s-7) 0}
    .faq-a{padding-right:0}
    .footer .fl{margin-left:0}
    /* The plan table scrolls sideways on a phone (four columns); tighter
       cells and no wrapping inside a value keep the scroll short. */
    .tut-table{min-width:400px;font-size:14px}
    .tut-table th,.tut-table td{padding:12px 8px}
    .tut-table td{white-space:nowrap}
    .tut-table thead th{font-size:17px}
  }
  @media(prefers-reduced-motion:reduce){
    html{scroll-behavior:auto}
    *,*::before,*::after{animation-duration:.001ms !important;transition-duration:.001ms !important}
  }
"""


# ── JS (plain string: braces and no backslashes) ─────────────────────────────

_JS = """
(function(){
  // The bar's real height, written back as --nav-h so the hero, the rail and
  // every anchor target clear it. Same block as the landing page.
  var nav = document.querySelector('.nav'), root = document.documentElement, last = 0;
  function measure(){
    if (!nav) return;
    var h = Math.round(nav.getBoundingClientRect().height);
    if (h && h !== last){ last = h; root.style.setProperty('--nav-h', h + 'px'); }
  }
  measure();
  window.addEventListener('resize', measure, { passive: true });
  if (nav && 'ResizeObserver' in window) new ResizeObserver(measure).observe(nav);

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

# The design system, exported so /compare lays out in the same building
# rather than inventing a second look. One definition, one place to change a
# token. /compare adds its own rules on top of this.
BASE_CSS = _CSS


_TITLE = "How to use Highlightz — full walkthrough & setup guide"
_DESC = ("Step-by-step guide to Highlightz: connect Twitch, monitor a live channel, "
         "review and approve automatic clips, scan past VODs, and manage your plan. "
         # "Free to start" survived here after the free-tier claims were cleared
         # out of the visible copy, because it is a meta description and nothing
         # was reading it. Then "no credit card required" survived the card
         # cutover in the same spot, for the same reason. A meta description is
         # exactly where a stale promise hides: invisible on the page, and the
         # first thing a search result quotes.
         "Free to start — no card, no time limit.")


def _howto_schema() -> str:
    """HowTo structured data, generated from the real QUICKSTART steps.

    WHY THIS PAGE AND THIS TYPE. The tutorial is literally a numbered
    walkthrough with named UI labels, which is exactly what HowTo describes.
    Search engines can show the steps directly, and a language model reading
    the page gets the procedure as data instead of having to infer it from
    prose — which is the difference between being summarised correctly and
    being summarised from the marketing copy.

    GENERATED, NEVER TYPED. Every step here is the same string the page
    renders. A hand-written copy would drift the first time a button is
    renamed, and structured data that disagrees with the visible page is worse
    than none: it is what search engines treat as deceptive markup.
    """
    import json
    steps = []
    for i, sec in enumerate(C.QUICKSTART, start=1):
        # The bold **labels** are real UI text; strip the markers for the
        # schema, which is read by machines rather than rendered.
        text = " ".join(st.replace("**", "") for st in sec.steps) or sec.body
        steps.append({
            "@type": "HowToStep",
            "position": i,
            "name": sec.title,
            "text": text,
            "url": "https://highlightz.app/tutorial#" + sec.id,
        })
    data = {
        "@context": "https://schema.org",
        "@type": "HowTo",
        "name": _TITLE,
        "description": _DESC,
        "totalTime": "PT5M",
        "supply": [], "tool": [],
        "step": steps,
    }
    return ('<script type="application/ld+json">'
            + json.dumps(data, ensure_ascii=False) + "</script>")


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
<meta property="og:image" content="https://highlightz.app/static/og-card-v6.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content=\"""" + escape(_TITLE) + """\">
<meta name="twitter:description" content=\"""" + escape(_DESC) + """\">
<meta name="twitter:image" content="https://highlightz.app/static/og-card-v6.png">
<link rel="alternate" type="text/markdown" href="https://highlightz.app/llms.txt" title="Highlightz for language models">
""" + _howto_schema() + """
<style>""" + _CSS + """</style>
</head>
<body>

<nav class="nav">
  <a href="/" class="nav-logo"><img src="/static/logo-mark.png" alt="Highlightz"><span>Highlightz</span></a>
  <div class="nav-links">
    <!-- The landing page's links, in the landing page's order, then this page
         and its sibling. -->
    <a href="/#catches" class="nav-link">What it catches</a>
    <a href="/#score" class="nav-link">How it scores</a>
    <a href="/#watch" class="nav-link">Channels</a>
    <a href="/#pricing" class="nav-link">Pricing</a>
    <a href="/#faq" class="nav-link">FAQ</a>
    <a href="/tutorial" class="nav-link on" aria-current="page">Tutorial</a>
    <a href="/compare" class="nav-link">Compare</a>
    <a href="/blog" class="nav-link">Blog</a>
  </div>
  <div class="nav-right">
    <a href="/login" class="nav-link">Sign in</a>
    <a href="/login" class="btn btn-go">Get started</a>
  </div>
</nav>

<header class="tut-hero" id="overview">
  <div class="wrap tut-hero-in">
    <div class="tut-hero-t">
      <div class="k">Walkthrough</div>
      <h1 class="disp">""" + escape(C.HERO_TITLE) + """</h1>
      <p class="lead">""" + escape(C.HERO_LEAD) + """</p>
      <div class="tut-hero-act">
        <a href="#get-started" class="btn btn-go btn-lg">Start with step one</a>
        <a href="/login" class="btn btn-ghost btn-lg">Open the dashboard</a>
      </div>
    </div>
    <div class="tut-hero-shot">""" + media_html(C.HERO_MEDIA) + """</div>
  </div>
</header>

<div class="wrap tut-page">
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
        <h2 class="tut-h disp">""" + escape(C.QUICKSTART_TITLE) + """</h2>
        <p class="tut-body">""" + escape(C.QUICKSTART_LEAD) + """</p>
        """ + quickstart + """
      </section>

      """ + features + """

      <section class="tut-sec" id="plans">
        <h2 class="tut-h disp">""" + escape(C.PLANS_TITLE) + """</h2>
        """ + _plans_table() + """
      </section>

      <section class="tut-sec" id="questions">
        <h2 class="tut-h disp">""" + escape(C.FAQ_TITLE) + """</h2>
        <p class="tut-body">""" + escape(C.FAQ_LEAD) + """</p>
        """ + _faq() + """
      </section>
    </main>
  </div>
</div>

<section class="tut-cta">
  <div class="wrap">
    <h2 class="disp">""" + escape(C.CTA_TITLE) + """</h2>
    <p>""" + escape(C.CTA_BODY) + """</p>
    <div class="tut-cta-act">
      <a href="/login" class="btn btn-go btn-lg">""" + escape(C.CTA_BUTTON) + """</a>
      <a href="/#faq" class="btn btn-ghost btn-lg">Read the FAQ</a>
    </div>
    <div class="tut-support">
      <h3>""" + escape(C.SUPPORT_TITLE) + """</h3>
      <p>""" + C.SUPPORT_BODY + """</p>
    </div>
  </div>
</section>

<dialog class="lb" id="lightbox" aria-label="Enlarged screenshot">
  <div class="lb-wrap">
    <button class="lb-x" id="lightbox-x" type="button">Close</button>
    <img id="lightbox-img" alt="">
  </div>
</dialog>

<footer class="footer">
  <img src="/static/logo-mark.png" alt="Highlightz" width="374" height="501">
  <nav aria-label="Site"><a href="/tutorial">Tutorial</a><a href="/compare">Compare</a><a href="/blog">Blog</a><a href="/tos">Terms of Service</a><a href="/privacy">Privacy Policy</a><a href="/cookies">Cookie Policy</a><a href="/opt-out">Streamer Opt-Out</a></nav>
  <span class="fl">&copy; 2026 ANTI Technology LLC</span>
</footer>

<script>""" + _JS + """</script>
</body>
</html>""")
