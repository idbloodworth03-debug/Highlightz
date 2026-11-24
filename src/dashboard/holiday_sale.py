"""The holiday sale, all over the landing page (owner, 2026-11-24: "promote
that we are doing a 50% off deal for the holidays when you use code CLIPPER at
checkout put it all over and make it like holiday themed as well").

THE PAGE ONLY ADVERTISES IT. The discount is the Stripe promotion code of that
name (a Coupon, 50% off, Duration "Once"), and every subscription Checkout
already shows Stripe's code box (`allow_promotion_codes`, stripe_billing.py).
`scripts/promo_check.py` asks Stripe whether the code really is what this page
says.

WHERE IT SHOWS, per request, so it starts and stops with no deploy:
  * a fixed ribbon above the nav, with a string of lights;
  * a pill above the headline and a gift tag hanging in the first screen;
  * a sale card at the top of Pricing (id="holiday", where every link to the
    sale goes), with a countdown and a button straight to Pro's checkout;
  * the first-month price on the Pro and Studio columns;
  * the first question in the FAQ, open;
  * a line in the closing frame, and one in the footer;
  * snow falling in the two black frames (proof and end).

WHEN. Until the end of HOLIDAY_SALE_LAST_DAY in US Pacific time, then the
server leaves it out, and a tab left open takes it down at the same moment.
HOLIDAY_SALE_CODE set to nothing takes it down now.

Everything here is a div or an aside, never a <section>: the page is held to
its eight sections (tests/test_landing_not_a_template.py).
"""
from __future__ import annotations

import html as _html
from datetime import date, datetime, timedelta, timezone

from config.settings import settings

# The sale ends in winter, when the Pacific coast is on standard time (UTC-8).
# A fixed offset rather than zoneinfo: the server need not carry tzdata.
PACIFIC = timezone(timedelta(hours=-8))


def ends_at(last_day: str) -> datetime | None:
    """The moment the sale is over: midnight Pacific after its last day. None
    when the day cannot be read, so a typo never advertises a sale forever."""
    try:
        d = date.fromisoformat(str(last_day or "").strip())
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=PACIFIC) + timedelta(days=1)


def _money(x: float) -> str:
    return "$" + (f"{x:,.0f}" if float(x).is_integer() else f"{x:,.2f}")


def current(now: datetime | None = None) -> dict | None:
    """The sale as the page shows it, or None when there is none to show."""
    code = (settings.holiday_sale_code or "").strip().upper()
    pct = int(settings.holiday_sale_percent or 0)
    end = ends_at(settings.holiday_sale_last_day)
    if not code or not 0 < pct < 100 or end is None:
        return None
    now = now or datetime.now(timezone.utc)
    if now >= end:
        return None
    from src.billing.plans import PLAN_LIMITS, SELLABLE_PLANS, period
    # Which plans the code works on (owner, 2026-11-24: "make CLIPPER work
    # on Pro only"). The page names only these; scripts/promo_restrict.py
    # makes the Stripe coupon match.
    wanted = sale_plans()
    plans, left_out = [], []
    for key in SELLABLE_PLANS:
        lim = PLAN_LIMITS.get(key)
        if key not in wanted:
            if lim:
                left_out.append(lim["label"])
            continue
        if lim and lim.get("price"):
            price = float(lim["price"])
            plans.append({"key": key, "label": lim["label"], "price": _money(price),
                          "sale": _money(round(price * (100 - pct) / 100, 2)),
                          # A "once" coupon is the first payment: a month of
                          # Pro, a YEAR of Studio once it is yearly (2026-11-24).
                          "per": period(key)})
    if not plans:
        return None
    last = (end - timedelta(days=1)).date()
    return {"code": code, "pct": pct, "end": end, "plans": plans, "left_out": left_out,
            "left_s": int((end - now).total_seconds()),
            "last_day": f"{last:%B} {last.day}"}


def sale_plans() -> tuple[str, ...]:
    """The plans HOLIDAY_SALE_PLANS names, in the pricing's order."""
    from src.billing.plans import SELLABLE_PLANS
    asked = {p.strip().lower() for p in str(settings.holiday_sale_plans or "").split(",")}
    return tuple(p for p in SELLABLE_PLANS if p in asked)


def _off(sale: dict) -> str:
    """"50% off your first month", and "of Pro" when the code is not for
    every plan on sale."""
    plans = sale["plans"]
    return (f"{sale['pct']}% off your {_first(plans)}"
            + (f" of {_names(plans)}" if sale.get("left_out") else ""))


def _short(sale: dict) -> str:
    """The ribbon on a phone: "Pro: 50% off first month"."""
    plans = sale["plans"]
    return ((f"{_names(plans)}: " if sale.get("left_out") else "")
            + f"{sale['pct']}% off {_first(plans)}")


def _only(sale: dict) -> str:
    """" The code is for Pro only; Studio is not part of the sale." or ""."""
    out = sale.get("left_out") or []
    if not out:
        return ""
    them = out[0] if len(out) == 1 else ", ".join(out[:-1]) + " and " + out[-1]
    return (f" The code is for {_names(sale['plans'])} only; {them} "
            f"{'is' if len(out) == 1 else 'are'} not part of the sale.")


def _names(plans: list[dict]) -> str:
    names = [p["label"] for p in plans]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def _prices(plans: list[dict]) -> str:
    """"Pro is $12.50 for your first month instead of $25, Studio $125 for
    your first year instead of $250." """
    parts = [f"<b>{p['sale']}</b> for your first {p['per']} instead of {p['price']}"
             for p in plans]
    first = f"{plans[0]['label']} is {parts[0]}"
    rest = [f"{p['label']} {part}" for p, part in zip(plans[1:], parts[1:])]
    return ", ".join([first] + rest) + "."


def _first(plans: list[dict]) -> str:
    """What the discount is on, in a phrase: "first month" while every plan
    is monthly, "first payment" once one is yearly (it is a month of one and a
    year of the other)."""
    return "first month" if all(p["per"] == "month" for p in plans) else "first payment"


def _firsts(plans: list[dict]) -> str:
    """"your first month of Pro or Studio", or "your first month of Pro or
    first year of Studio"."""
    if all(p["per"] == plans[0]["per"] for p in plans):
        return f"your first {plans[0]['per']} of {_names(plans)}"
    parts = [f"first {p['per']} of {p['label']}" for p in plans]
    return "your " + (parts[0] if len(parts) == 1 else
                      ", ".join(parts[:-1]) + " or " + parts[-1])


def _clock(left_s: int) -> str:
    left_s = max(0, left_s)
    units = (("d", left_s // 86400, "days"), ("h", left_s % 86400 // 3600, "hours"),
             ("m", left_s % 3600 // 60, "min"), ("s", left_s % 60, "sec"))
    return ('<div class="hz-clock" role="timer" aria-label="Time left in the holiday sale">'
            + "".join(f'<div><b data-u="{u}">{n:02d}</b><span>{label}</span></div>'
                      for u, n, label in units)
            + "</div>")


def _days_left(left_s: int) -> str:
    d = left_s // 86400
    if d >= 1:
        return f" &middot; {d} day{'s' if d != 1 else ''} left"
    return f" &middot; {left_s // 3600}h {left_s % 3600 // 60:02d}m left"


SNOWFLAKE = ('<svg class="hz-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M12 2v20'
             'M3.3 7l17.4 10M3.3 17L20.7 7M9.5 4.5L12 7l2.5-2.5M9.5 19.5L12 17l2.5 2.5'
             'M4.2 10.4l3.4-.9-.9-3.4M19.8 13.6l-3.4.9.9 3.4M4.2 13.6l3.4.9-.9 3.4'
             'M19.8 10.4l-3.4-.9.9-3.4"/></svg>')

BOW = ('<svg class="hz-bow" viewBox="0 0 80 56" aria-hidden="true">'
       '<path d="M36 30L26 54l8-4 4 6 2-24z" fill="#7A1420"/>'
       '<path d="M44 30l10 24-8-4-4 6-2-24z" fill="#7A1420"/>'
       '<path d="M40 28C30 8 6 2 8 18c1 10 22 12 32 10z" fill="#C1272D"/>'
       '<path d="M40 28C50 8 74 2 72 18c-1 10-22 12-32 10z" fill="#C1272D"/>'
       '<path d="M38 27C31 15 16 10 14 18" stroke="#7A1420" stroke-width="2" fill="none"/>'
       '<path d="M42 27C49 15 64 10 66 18" stroke="#7A1420" stroke-width="2" fill="none"/>'
       '<circle cx="40" cy="28" r="7" fill="#9E1B26"/></svg>')

HOLLY = ('<svg class="hz-holly" viewBox="0 0 40 24" aria-hidden="true">'
         '<path d="M19 14C13 3 3 4 1 6c3 1 3 4 1 6 4-1 5 2 5 4 2-2 5-2 6 0 1-2 4-3 6-2z" fill="#16553A"/>'
         '<path d="M21 14c6-11 16-10 18-8-3 1-3 4-1 6-4-1-5 2-5 4-2-2-5-2-6 0-1-2-4-3-6-2z" fill="#1F7A52"/>'
         '<circle cx="17" cy="16" r="3" fill="#C1272D"/><circle cx="23" cy="16" r="3" fill="#C1272D"/>'
         '<circle cx="20" cy="20" r="3" fill="#9E1B26"/></svg>')


def _copy_button(code: str, label: str, cls: str = "hz-copy") -> str:
    return (f'<button type="button" class="{cls}" data-hz-copy="{code}" '
            f'aria-label="Copy the code {code}">{label}</button>')


def pieces(sale: dict | None) -> dict[str, str]:
    """Placeholder -> markup. With no sale every placeholder goes to "", so
    the page is exactly the page it was."""
    keys = ("<!--SALE_HERO-->", "<!--SALE_TAG-->", "<!--SALE_PRICING-->",
            "<!--SALE_PLAN_free-->", "<!--SALE_PLAN_pro-->", "<!--SALE_PLAN_studio-->",
            "<!--SALE_FAQ-->", "<!--SALE_END_K-->", "<!--SALE_END-->", "<!--SALE_FOOT-->",
            "<!--SALE_SCRIPT-->")
    out = dict.fromkeys(keys, "")
    if not sale:
        return out
    code = _html.escape(sale["code"])
    pct = sale["pct"]
    plans = sale["plans"]
    names = _names(plans)
    first = plans[0]
    off = _off(sale)

    out["<!--SALE_HERO-->"] = (
        '<a class="proof-new hz-new hz-only" href="#holiday"><b>Holiday sale </b><span>'
        + off + '<span class="proof-new-k"> with code ' + code + '</span></span> '
        '<span aria-hidden="true">&rarr;</span></a>')

    out["<!--SALE_TAG-->"] = (
        '<aside class="hz-tag hz-only" aria-label="Holiday sale code">'
        '<div class="hz-tag-card"><span class="hz-tag-hole" aria-hidden="true"></span>'
        '<p class="hz-tag-k">' + SNOWFLAKE + 'Holiday sale</p>'
        f'<p class="hz-tag-big">{pct}% off</p>'
        f'<p class="hz-tag-sub">{_firsts(plans)}</p>'
        f'<div class="hz-tag-code"><span>Use code</span><b>{code}</b></div>'
        + _copy_button(code, "Copy code") + '</div></aside>')

    flakes = "".join(
        f'<span class="hz-flake" style="{pos}">{SNOWFLAKE}</span>'
        for pos in ("top:var(--s-5);right:var(--s-7)", "bottom:var(--s-6);right:30%",
                    "top:40%;right:var(--s-5)"))
    out["<!--SALE_PRICING-->"] = (
        '<div class="hz-deal hz-only" id="holiday">' + BOW + flakes
        + '<div class="hz-deal-in">'
        '<p class="hz-deal-script">' + HOLLY + 'Happy holidays</p>'
        f'<h3 class="hz-deal-h">{off}.</h3>'
        f'<p class="hz-deal-p">Pick {names} and type <b>{code}</b> into the promotion code box '
        f'at checkout. {_prices(plans)} After that it is the usual price, and you can cancel '
        f'from the Settings tab whenever you like.{_only(sale)}</p>'
        '<div class="hz-deal-row">'
        f'<div class="hz-deal-code"><span>Code</span><b>{code}</b>'
        + _copy_button(code, "Copy") + '</div>'
        f'<a class="btn btn-go btn-lg" href="/login?next={first["key"]}">'
        f'Get {first["label"]} for {first["sale"]}</a></div>'
        f'<p class="hz-deal-fine">Ends {sale["last_day"]}. For a new '
        + (f'{names} ' if sale.get("left_out") else '')
        + f'subscription: the code takes {pct}% off its first payment.</p>'
        '</div>' + _clock(sale["left_s"]) + '</div>')

    for p in plans:
        out[f"<!--SALE_PLAN_{p['key']}-->"] = (
            '<p class="hz-plan hz-only"><span class="hz-plan-k">Holiday</span>'
            f'<span><b>{p["sale"]}</b> your first {p["per"]} with code <b>{code}</b></span></p>')

    out["<!--SALE_FAQ-->"] = (
        '    <div class="faq-group hz-faq hz-only">\n'
        '      <h3 class="faq-h">Holiday sale</h3>\n'
        '      <details class="faq-item" open>\n'
        '        <summary class="faq-q">How does the holiday sale work?</summary>\n'
        f'        <p class="faq-a">Pick {names} and enter <b>{code}</b> in the promotion code '
        f'box at checkout. Your {_first(plans)} is {pct}% off: {_prices(plans)} After that it '
        'renews at the usual price, and you can cancel from the Settings tab any time. '
        f'The sale ends {sale["last_day"]}.{_only(sale)}</p>\n'
        '      </details>\n'
        '    </div>\n')

    out["<!--SALE_END_K-->"] = '<p class="hz-end-k hz-only">Happy holidays</p>'
    out["<!--SALE_END-->"] = (
        f'<p class="hz-end-line hz-only"><span>{off} with code</span>'
        + _copy_button(code, code, "hz-chip")
        + f'<span>until {sale["last_day"]}</span></p>')
    out["<!--SALE_FOOT-->"] = '<span class="hz-foot hz-only">Happy holidays from Highlightz</span>'
    out["<!--SALE_SCRIPT-->"] = SCRIPT.replace("__END_MS__", str(int(sale["end"].timestamp() * 1000)))
    return out


def ribbon(sale: dict) -> str:
    code = _html.escape(sale["code"])
    return (
        '<div class="hz-ribbon hz-only">'
        '<a class="hz-ribbon-in" href="#holiday">' + SNOWFLAKE
        + '<b class="hz-r-k">Holiday sale</b>'
        f'<span class="hz-r-long">{_off(sale)} with code</span>'
        f'<span class="hz-r-short">{_short(sale)}</span>'
        f'<span class="hz-code">{code}</span>'
        f'<span class="hz-r-more">Ends {sale["last_day"]}<span data-hz-days>'
        f'{_days_left(sale["left_s"])}</span></span>'
        '<span class="hz-r-go" aria-hidden="true">&rarr;</span></a>'
        '<div class="hz-lights" aria-hidden="true">' + "<i></i>" * 96 + '</div>'
        '<span class="hz-sr" aria-live="polite" data-hz-said></span>'
        '</div>')


def apply(html: str, now: datetime | None = None) -> str:
    """The landing page with the sale in it, or with its placeholders
    removed when there is no sale."""
    sale = current(now)
    for k, v in pieces(sale).items():
        html = html.replace(k, v)
    if sale:
        html = html.replace("</head>\n<body>",
                            STYLE + "\n</head>\n<body class=\"hz-sale\">\n" + ribbon(sale), 1)
    return html


# Every value on the spacing scale, sizes in whole pixels, transitions on the
# duration tokens and the one curve: the rules tests/test_design_tokens.py
# holds the rest of the page to, checked on this sheet by
# tests/test_holiday_sale.py.
STYLE = """<style>
  /* ══ THE HOLIDAY SALE (src/dashboard/holiday_sale.py). On the page only
     while the sale runs. Holly red and pine for the season, the cream for
     anything that has to be read on them, the page's orange for its one
     button. White on --holly 5.84:1, on --holly-2 7.96:1; the cream on
     --holly-3 8.61:1; --holly-ink on the paper 6.22:1. ══ */
  :root{--holly:#C1272D;--holly-2:#9E1B26;--holly-3:#7A1420;--holly-ink:#B01E2A;
    --pine:#16553A;--pine-3:#0F2A1E;--cream:#FFE3A3;--sale-h:40px}
  /* The ribbon sits above the bar; the bar, the first screen and every
     anchor clear both (the nav-height block measures the two together). */
  .hz-sale .nav{top:var(--sale-h)}
  .hz-sale .proof-in{padding-top:calc(clamp(112px,18vh,180px) + var(--sale-h))}
  #holiday{scroll-margin-top:calc(var(--nav-h) + 48px)}

  .hz-ribbon{position:fixed;top:0;left:0;right:0;z-index:61;color:#fff;
    background:repeating-linear-gradient(135deg,var(--holly-2) 0 16px,#8C1822 16px 32px)}
  .hz-ribbon-in{position:relative;z-index:1;display:flex;align-items:center;justify-content:center;
    flex-wrap:wrap;gap:var(--s-1) var(--s-3);min-height:40px;padding:var(--s-2) var(--s-4);
    font-family:var(--mono);font-size:12px;letter-spacing:.12em;text-transform:uppercase;
    line-height:1.4;text-align:center;color:#fff}
  .hz-ribbon-in b{color:var(--cream);font-weight:600}
  .hz-ico{width:16px;height:16px;flex:none;color:var(--cream)}
  .hz-code{padding:0 var(--s-2);border:1px dashed var(--cream);border-radius:3px;
    background:rgba(0,0,0,.28);color:var(--cream);font-weight:600;letter-spacing:.18em}
  .hz-r-go{display:inline-block;transition:transform var(--dur-fast) var(--ease)}
  .hz-ribbon-in:hover .hz-r-go{transform:translateX(4px)}
  .hz-ribbon-in:focus-visible{outline:2px solid var(--cream);outline-offset:-4px}
  /* The lights: a wire along the ribbon's bottom edge and the bulbs hanging
     off it, inside the bar's own top padding so they cover nothing. */
  .hz-lights{position:absolute;left:0;right:0;top:100%;height:12px;display:flex;
    gap:var(--s-5);padding:0 var(--s-3);overflow:hidden;pointer-events:none}
  .hz-lights::before{content:'';position:absolute;left:0;right:0;top:0;height:1px;
    background:rgba(0,0,0,.6)}
  .hz-lights i{flex:none;width:6px;height:9px;margin-top:4px;border-radius:50%;
    background:var(--c);box-shadow:0 0 8px var(--c),0 0 2px var(--c);
    animation:hz-twinkle 2.6s ease-in-out infinite}
  .hz-lights i:nth-child(4n+1){--c:#FF5A5F}
  .hz-lights i:nth-child(4n+2){--c:#FFD25A;animation-delay:-.7s}
  .hz-lights i:nth-child(4n+3){--c:#4BE08F;animation-delay:-1.4s}
  .hz-lights i:nth-child(4n){--c:#7CC8FF;animation-delay:-2s}
  .hz-lights i:nth-child(3n){animation-duration:1.9s}
  @keyframes hz-twinkle{0%,100%{opacity:1}50%{opacity:.3}}
  .hz-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
  /* One line at every width: the end date goes first, then the words
     "Holiday sale", then the long sentence for a short one. */
  .hz-r-short{display:none}
  @media(max-width:1000px){ .hz-r-more{display:none} }
  @media(max-width:700px){
    .hz-r-k{display:none}
    .hz-ribbon-in{letter-spacing:.06em;gap:var(--s-1) var(--s-2);padding:var(--s-2) var(--s-3)}
  }
  @media(max-width:520px){
    .hz-r-long{display:none}
    .hz-r-short{display:inline}
  }
  @media(max-width:400px){ .hz-r-go{display:none} }

  /* The pill above the headline, in the season's red. */
  .proof-new.hz-new{background:rgba(122,20,32,.78);border-color:rgba(255,227,163,.5);color:#fff}
  .proof-new.hz-new b{color:var(--cream)}
  .proof-new.hz-new:hover{color:#fff;border-color:var(--cream)}
  /* A phone has the ribbon above and the tag under the button already. */
  @media(max-width:520px){ .proof-new.hz-new{display:none} }

  /* The gift tag. Under the button on a phone; hung on a string beside the
     headline where there is room, swinging a little. */
  .hz-tag{position:relative;z-index:3;margin-top:var(--s-6);width:min(100%,320px)}
  .hz-tag-card{position:relative;z-index:1;padding:var(--s-6) var(--s-5) var(--s-5);border-radius:8px;
    background:linear-gradient(160deg,var(--holly) 0%,var(--holly-2) 55%,var(--holly-3) 100%);
    color:#fff;box-shadow:0 24px 48px rgba(0,0,0,.55)}
  .hz-tag-card::before{content:'';position:absolute;inset:8px;border:1px dashed rgba(255,227,163,.55);
    border-radius:4px;pointer-events:none}
  .hz-tag-hole{position:absolute;top:12px;left:50%;width:12px;height:12px;transform:translateX(-50%);
    border-radius:50%;background:#000;box-shadow:0 0 0 2px var(--cream)}
  .hz-tag-k{display:flex;align-items:center;gap:var(--s-2);margin:0;font-family:var(--mono);
    font-size:12px;font-weight:600;letter-spacing:.16em;text-transform:uppercase;color:var(--cream)}
  .hz-tag-big{margin:var(--s-2) 0 0;font-family:var(--sans);font-weight:800;font-size:56px;
    line-height:1;letter-spacing:-.04em;color:#fff}
  .hz-tag-sub{margin:var(--s-2) 0 0;font-size:14px;line-height:1.4;color:rgba(255,255,255,.92)}
  .hz-tag-code{display:flex;align-items:center;justify-content:space-between;gap:var(--s-3);
    margin-top:var(--s-4);padding:var(--s-2) var(--s-3);border-radius:4px;background:var(--cream);
    color:var(--holly-3)}
  .hz-tag-code span{font-family:var(--mono);font-size:12px;letter-spacing:.12em;text-transform:uppercase}
  .hz-tag-code b{font-family:var(--mono);font-weight:600;font-size:20px;letter-spacing:.16em}
  .hz-tag .hz-copy{width:100%;margin-top:var(--s-3)}
  @media(min-width:1100px){
    .hz-tag{position:absolute;right:clamp(20px,4.5vw,72px);margin:0;width:288px;
      top:calc(clamp(112px,18vh,180px) + var(--sale-h) + var(--s-5));
      transform-origin:50% -160px;animation:hz-swing 6s ease-in-out infinite}
    .hz-tag::before{content:'';position:absolute;z-index:2;left:50%;bottom:calc(100% - 18px);
      width:1px;height:240px;background:linear-gradient(180deg,rgba(255,227,163,0),var(--cream))}
  }
  @keyframes hz-swing{0%,100%{transform:rotate(-2deg)}50%{transform:rotate(2deg)}}

  .hz-copy{display:inline-flex;align-items:center;justify-content:center;cursor:pointer;
    padding:var(--s-2) var(--s-4);border:0;border-radius:3px;background:#fff;color:var(--holly-3);
    font-family:var(--sans);font-weight:700;font-size:14px;transition:background var(--dur-fast)}
  .hz-copy:hover{background:var(--cream)}
  .hz-copy:active,.hz-chip:active{transform:translateY(1px)}
  .hz-chip{display:inline-flex;align-items:center;cursor:pointer;padding:var(--s-1) var(--s-2);
    border:1px dashed var(--cream);border-radius:3px;background:rgba(0,0,0,.35);color:var(--cream);
    font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.16em;
    transition:background var(--dur-fast)}
  .hz-chip:hover{background:rgba(122,20,32,.85)}

  /* The sale card at the top of Pricing: a present, wrapped. Pine, a red
     ribbon down its left side and the bow on top. */
  .hz-deal{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;
    gap:var(--s-6) var(--s-7);margin:var(--s-7) 0 var(--s-8);
    padding:var(--s-7) var(--s-7) var(--s-7) var(--s-9);border-radius:8px;background:var(--pine-3);
    color:rgba(255,255,255,.88);box-shadow:0 24px 64px rgba(10,10,12,.22)}
  .hz-deal::before{content:'';position:absolute;inset:12px;border:1px dashed rgba(255,227,163,.32);
    border-radius:4px;pointer-events:none}
  .hz-deal::after{content:'';position:absolute;top:0;bottom:0;left:var(--s-6);width:32px;
    background:linear-gradient(90deg,var(--holly-3),var(--holly) 35%,var(--holly) 65%,var(--holly-3))}
  .hz-bow{position:absolute;z-index:1;top:-24px;left:8px;width:80px;height:56px}
  .hz-flake{position:absolute;color:rgba(255,227,163,.18);pointer-events:none}
  .hz-flake .hz-ico{width:48px;height:48px;color:inherit;animation:hz-spin 40s linear infinite}
  @keyframes hz-spin{to{transform:rotate(360deg)}}
  .hz-deal-in{position:relative;z-index:1;min-width:0}
  .hz-deal-script{display:flex;align-items:center;gap:var(--s-2);margin:0;font-family:var(--display);
    font-weight:400;font-size:clamp(28px,3vw,40px);line-height:1.1;color:var(--cream)}
  .hz-holly{width:40px;height:24px;flex:none}
  .light .hz-deal .hz-deal-h{margin:var(--s-2) 0 0;font-family:var(--sans);font-weight:800;
    font-size:clamp(32px,4.4vw,56px);line-height:1;letter-spacing:-.04em;color:#fff}
  .hz-deal-p{margin:var(--s-4) 0 0;max-width:var(--measure);font-size:16px;line-height:1.55;
    color:rgba(255,255,255,.86)}
  .hz-deal-p b{color:var(--cream);font-weight:700}
  .hz-deal-row{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s-4);margin-top:var(--s-5)}
  .hz-deal-code{display:flex;align-items:center;gap:var(--s-3);padding:var(--s-2) var(--s-2) var(--s-2) var(--s-4);
    border:1px dashed var(--cream);border-radius:4px;background:rgba(0,0,0,.25)}
  .hz-deal-code span{font-family:var(--mono);font-size:12px;letter-spacing:.12em;
    text-transform:uppercase;color:rgba(255,227,163,.85)}
  .hz-deal-code b{font-family:var(--mono);font-weight:600;font-size:24px;letter-spacing:.16em;color:var(--cream)}
  .hz-deal-fine{margin:var(--s-4) 0 0;font-family:var(--mono);font-size:12px;letter-spacing:.06em;
    color:rgba(255,255,255,.66)}
  .hz-clock{position:relative;z-index:1;display:flex;gap:var(--s-3)}
  .hz-clock div{display:flex;flex-direction:column;align-items:center;gap:var(--s-2)}
  .hz-clock b{display:block;min-width:72px;padding:var(--s-3) var(--s-2);border-radius:6px;
    background:var(--holly-2);font-family:var(--mono);font-weight:600;font-size:40px;line-height:1;
    text-align:center;color:#fff;font-variant-numeric:tabular-nums;box-shadow:inset 0 -4px 0 rgba(0,0,0,.25)}
  .hz-clock span{font-family:var(--mono);font-size:12px;letter-spacing:.14em;text-transform:uppercase;
    color:var(--cream)}
  @media(max-width:1000px){ .hz-deal{grid-template-columns:minmax(0,1fr)} }
  @media(max-width:700px){
    .hz-deal{padding:var(--s-6) var(--s-5) var(--s-6) var(--s-8)}
    .hz-deal::after{left:var(--s-4);width:24px}
    .hz-bow{left:-4px;width:64px;height:45px;top:-20px}
    .hz-deal-code{padding-left:var(--s-3);gap:var(--s-2)}
    .hz-deal-code b{font-size:20px;letter-spacing:.08em}
    .hz-clock{gap:var(--s-2)}
    .hz-clock b{min-width:48px;padding:var(--s-2) var(--s-1);font-size:24px}
  }

  /* The first-month price on the paid columns. */
  .hz-plan{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s-2);margin:var(--s-3) 0 0;
    font-size:14px;line-height:1.4;color:var(--paper-ink-2)}
  .hz-plan b{color:var(--holly-ink);font-weight:700}
  .hz-plan-k{padding:var(--s-1) var(--s-2);border-radius:3px;background:var(--holly);color:#fff;
    font-family:var(--mono);font-size:12px;font-weight:600;letter-spacing:.12em;text-transform:uppercase}

  .hz-faq .faq-h{color:var(--holly-ink)}

  .hz-end-k{margin:0 0 var(--s-3);font-family:var(--display);font-weight:400;
    font-size:clamp(32px,4vw,56px);line-height:1.1;color:var(--cream)}
  .hz-end-line{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s-2);margin:var(--s-5) 0 0;
    font-family:var(--mono);font-size:12px;letter-spacing:.08em;text-transform:uppercase;
    color:rgba(255,255,255,.8)}
  .hz-foot{color:var(--cream)}
  .hz-snow{position:absolute;inset:0;z-index:1;width:100%;height:100%;pointer-events:none}
</style>"""


# No backslashes anywhere in this script: it is a Python string, and an
# escape that means something to JavaScript means something else here.
SCRIPT = """<script>
/* ── The holiday sale (src/dashboard/holiday_sale.py) ─────────────────────
   Copy buttons, the countdown, snow in the two black frames, and the end:
   a tab still open when the sale ends takes every piece of it down at once,
   the way the server stops sending it. */
(function(){
  var END = __END_MS__, body = document.body, timer = 0, storms = [];
  var said = document.querySelector('[data-hz-said]');

  function fallback(text){
    var a = document.createElement('textarea');
    a.value = text; a.setAttribute('readonly', '');
    a.style.position = 'fixed'; a.style.opacity = '0';
    body.appendChild(a); a.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) {}
    a.remove();
    return ok;
  }
  function copied(btn, ok){
    var was = btn.getAttribute('data-hz-was') || btn.textContent;
    btn.setAttribute('data-hz-was', was);
    btn.textContent = ok ? 'Copied!' : btn.getAttribute('data-hz-copy');
    if (said) said.textContent = ok ? 'Code copied' : '';
    clearTimeout(btn._hzT);
    btn._hzT = setTimeout(function(){ btn.textContent = was; }, 1800);
  }
  document.addEventListener('click', function(e){
    var btn = e.target.closest && e.target.closest('[data-hz-copy]');
    if (!btn) return;
    e.preventDefault();
    var code = btn.getAttribute('data-hz-copy');
    if (navigator.clipboard && window.isSecureContext){
      navigator.clipboard.writeText(code).then(function(){ copied(btn, true); },
        function(){ copied(btn, fallback(code)); });
    } else {
      copied(btn, fallback(code));
    }
  });

  function pad(n){ return (n < 10 ? '0' : '') + n; }
  function over(){
    clearInterval(timer);
    storms.forEach(function(s){ s.stop(); });
    document.querySelectorAll('.hz-only').forEach(function(el){ el.remove(); });
    body.classList.remove('hz-sale');
    window.dispatchEvent(new Event('resize'));
  }
  function tick(){
    var ms = END - Date.now();
    if (ms <= 0){ over(); return; }
    var s = Math.floor(ms / 1000), d = Math.floor(s / 86400),
        h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    var vals = { d: d, h: h, m: m, s: x };
    document.querySelectorAll('.hz-clock b[data-u]').forEach(function(b){
      var v = pad(vals[b.getAttribute('data-u')]);
      if (b.textContent !== v) b.textContent = v;
    });
    var left = d >= 1 ? ' · ' + d + (d === 1 ? ' day' : ' days') + ' left'
                      : ' · ' + h + 'h ' + pad(m) + 'm left';
    document.querySelectorAll('[data-hz-days]').forEach(function(el){
      if (el.textContent !== left) el.textContent = left;
    });
  }
  tick();
  timer = setInterval(tick, 1000);

  /* Snow. A canvas in each black frame, under its words. It only falls
     while the frame is on screen and the tab is in front; under reduced
     motion it is drawn once and stays still. */
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function storm(host){
    var c = document.createElement('canvas');
    c.className = 'hz-snow hz-only'; c.setAttribute('aria-hidden', 'true');
    host.insertBefore(c, host.querySelector('.proof-in, .end-in'));
    var ctx = c.getContext && c.getContext('2d');
    if (!ctx) return null;
    var w = 0, h = 0, dpr = Math.min(window.devicePixelRatio || 1, 2), flakes = [],
        on = false, seen = false, raf = 0, t0 = 0;
    function flake(anywhere){
      var r = Math.random() * 2.2 + 0.8;
      return { x: Math.random() * w, y: anywhere ? Math.random() * h : -4, r: r,
               v: 12 + r * 16, a: 0.35 + Math.random() * 0.5,
               p: Math.random() * 6.283, s: 0.4 + Math.random() };
    }
    function draw(){
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = '#fff';
      for (var i = 0; i < flakes.length; i++){
        var f = flakes[i];
        ctx.globalAlpha = f.a;
        ctx.beginPath();
        ctx.arc(f.x + Math.sin(f.p) * 8, f.y, f.r, 0, 6.283);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }
    function size(){
      var r = host.getBoundingClientRect();
      w = Math.round(r.width); h = Math.round(r.height);
      if (!w || !h) return;
      c.width = w * dpr; c.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var n = Math.min(160, Math.round(w * h / 9000));
      while (flakes.length < n) flakes.push(flake(true));
      flakes.length = n;
      draw();
    }
    function step(t){
      var dt = t0 ? Math.min(0.05, (t - t0) / 1000) : 0;
      t0 = t;
      for (var i = 0; i < flakes.length; i++){
        var f = flakes[i];
        f.y += f.v * dt; f.p += f.s * dt;
        if (f.y > h + 4){ flakes[i] = flake(false); }
      }
      draw();
      raf = requestAnimationFrame(step);
    }
    function run(){
      var want = !still && seen && !document.hidden;
      if (want && !on){ on = true; t0 = 0; raf = requestAnimationFrame(step); }
      else if (!want && on){ on = false; cancelAnimationFrame(raf); }
    }
    size();
    if ('ResizeObserver' in window) new ResizeObserver(size).observe(host);
    else window.addEventListener('resize', size, { passive: true });
    if ('IntersectionObserver' in window){
      new IntersectionObserver(function(es){ seen = es[0].isIntersecting; run(); }).observe(host);
    } else { seen = true; run(); }
    document.addEventListener('visibilitychange', run);
    return { stop: function(){ still = true; run(); } };
  }
  ['.proof', '.end'].forEach(function(sel){
    var host = document.querySelector(sel);
    var s = host && storm(host);
    if (s) storms.push(s);
  });
})();
</script>"""
