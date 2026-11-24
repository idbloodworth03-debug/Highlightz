"""The holiday sale on the landing page (owner, 2026-11-24: "promote that we
are doing a 50% off deal for the holidays when you use code CLIPPER at
checkout put it all over and make it like holiday themed as well").

What these defend:
  1. while it runs it is all over the page: the ribbon, the pill and the tag
     on the first screen, the card in Pricing, the paid columns, the FAQ, the
     closing frame and the footer, and the script that runs the clock;
  2. it is true: every "50% off" says it is the first month, the prices are
     the plans' own halved, and "Get Pro" goes through sign-in to Checkout;
  3. it ends by itself (server and open tab), and without it the page is the
     page it was, placeholders and all gone;
  4. it keeps the page's shape (eight sections, every anchor resolves) and
     its sheet keeps the design rules the rest of the page is held to;
  5. scripts/promo_check.py says when Stripe's code is not what the page says.
"""

import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from src.dashboard import api, holiday_sale as hs

UTC = timezone.utc


@pytest.fixture
def sale(monkeypatch):
    def set_(code="CLIPPER", pct=50, last_day="2099-12-31"):
        monkeypatch.setattr(api.settings, "holiday_sale_code", code)
        monkeypatch.setattr(api.settings, "holiday_sale_percent", pct)
        monkeypatch.setattr(api.settings, "holiday_sale_last_day", last_day)
        return api.render_landing()
    return set_


def _visible(html: str) -> str:
    html = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    html = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


# ── 1. all over the page ─────────────────────────────────────────────────────

def test_it_is_all_over_the_page(sale):
    html = sale()
    assert '<body class="hz-sale">' in html
    for piece in ('class="hz-ribbon', 'class="proof-new hz-new', 'class="hz-tag',
                  'class="hz-deal hz-only" id="holiday"', 'class="hz-plan',
                  'class="faq-group hz-faq', 'class="hz-end-k', 'class="hz-end-line',
                  'class="hz-foot', "The holiday sale (src/dashboard/holiday_sale.py)"):
        assert piece in html, piece
    vis = _visible(html)
    assert vis.count("CLIPPER") >= 8, "the code is not all over the page"
    assert "Happy holidays" in vis


def test_the_pieces_sit_where_they_are_meant_to(sale):
    html = sale()
    proof = re.search(r'<section class="proof".*?</section>', html, re.S).group(0)
    assert "hz-new" in proof and "hz-tag" in proof
    assert proof.index("hz-new") < proof.index('id="proof-h"'), "the pill is not above the headline"
    pricing = re.search(r'<section[^>]*id="pricing".*?</section>', html, re.S).group(0)
    assert pricing.index('id="holiday"') < pricing.index('class="plans"'), "the card is not first"
    faq = re.search(r'<section[^>]*id="faq".*?</section>', html, re.S).group(0)
    assert faq.index("How does the holiday sale work?") < faq.index("What does Highlightz actually do?")
    assert '<details class="faq-item" open>' in faq
    end = re.search(r'<section class="end".*?</section>', html, re.S).group(0)
    assert "hz-end-k" in end and "hz-end-line" in end
    ribbon = html.index('class="hz-ribbon')
    assert html.index("<body") < ribbon < html.index('<nav class="nav">')


def test_every_link_to_the_sale_lands_on_the_card(sale):
    html = sale()
    assert html.count('href="#holiday"') >= 2           # the ribbon and the pill
    assert html.count('id="holiday"') == 1


# ── 2. true ──────────────────────────────────────────────────────────────────

def test_every_percentage_says_it_is_the_first_month(sale):
    vis = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _visible(sale())))
    hits = list(re.finditer(r"50% off", vis))
    assert len(hits) >= 6
    for m in hits:
        # "50% off your first month", or the FAQ's "Your first month is 50% off".
        near = vis[max(0, m.start() - 24):m.end() + 30]
        assert "first month" in near, near


def test_the_prices_are_the_plans_own_halved(sale):
    from src.billing.plans import PLAN_LIMITS
    html = sale()
    pro, studio = PLAN_LIMITS["pro"]["price"], PLAN_LIMITS["studio"]["price"]
    want_pro, want_studio = hs._money(pro / 2), hs._money(studio / 2)
    assert want_pro == "$12.50" and want_studio == "$125"     # $25 and $250 today
    cols = re.findall(r'<h3 class="plan-name">(\w+)</h3>.*?(?=<ul class="plan-facts">)', html, re.S)
    assert cols == ["Free", "Pro", "Studio"]
    blocks = re.findall(r'<div class="plan[^"]*">.*?<ul class="plan-facts">', html, re.S)
    assert "hz-plan" not in blocks[0], "Free got a sale price"
    assert f"<b>{want_pro}</b> your first month with code <b>CLIPPER</b>" in blocks[1]
    assert f"<b>{want_studio}</b> your first month with code <b>CLIPPER</b>" in blocks[2]
    assert f"Pro is <b>{want_pro}</b> instead of ${pro} for your first month" in html


def test_the_percentage_and_code_follow_the_settings(sale):
    html = sale(code="snow25", pct=25)
    vis = _visible(html)
    assert "SNOW25" in vis and "CLIPPER" not in vis
    assert "25% off your first month" in vis and "$18.75" in vis


def test_get_pro_signs_in_then_opens_checkout(sale):
    html = sale()
    card = re.search(r'id="holiday".*?</div></div>', html, re.S).group(0)
    assert 'href="/login?next=pro"' in card and "Get Pro for $12.50" in card
    assert api._after_sign_in("pro") == "/billing/checkout?plan=pro"
    assert api._after_sign_in("studio") == "/billing/checkout?plan=studio"


def test_checkout_still_shows_the_code_box():
    """The page tells people to type the code at checkout: that box has to be
    there on every ordinary subscription Checkout."""
    import inspect
    from src.billing import stripe_billing
    src = inspect.getsource(stripe_billing.create_checkout_url)
    assert '"allow_promotion_codes": True' in src


# ── 3. it ends ───────────────────────────────────────────────────────────────

def test_it_ends_at_midnight_pacific_after_the_last_day(monkeypatch):
    monkeypatch.setattr(api.settings, "holiday_sale_code", "CLIPPER")
    monkeypatch.setattr(api.settings, "holiday_sale_percent", 50)
    monkeypatch.setattr(api.settings, "holiday_sale_last_day", "2027-01-01")
    end = hs.ends_at("2027-01-01")
    assert end == datetime(2027, 1, 2, 8, 0, tzinfo=UTC)
    assert hs.current(end - timedelta(seconds=1)) is not None
    assert hs.current(end) is None
    s = hs.current(datetime(2026, 11, 24, 12, tzinfo=UTC))
    assert s["last_day"] == "January 1" and s["left_s"] == int((end - datetime(2026, 11, 24, 12, tzinfo=UTC)).total_seconds())


@pytest.mark.parametrize("kw", [dict(last_day="2000-01-01"), dict(code=""), dict(code="  "),
                                dict(last_day="next tuesday"), dict(pct=0), dict(pct=100)])
def test_without_a_sale_the_page_is_the_page_it_was(sale, kw):
    html = sale(**kw)
    # (The bar's measuring script looks for a ribbon either way.)
    assert 'class="hz-' not in html and ".hz-" not in html.replace("'.hz-ribbon'", ""), \
        "a piece of the sale is still on the page"
    assert not re.findall(r"<!--SALE_[A-Za-z_]+-->", html), "a placeholder was left in"
    assert "<body>" in html and "Happy holidays" not in html


def test_an_open_tab_takes_it_down_at_the_same_moment(sale):
    html = sale(last_day="2099-12-31")
    js = hs.SCRIPT
    assert "END = __END_MS__" in js and "__END_MS__" not in html
    assert "END = " + str(int(hs.ends_at("2099-12-31").timestamp() * 1000)) + "," in html
    body = js[js.index("function over()"):js.index("function tick()")]
    assert ".hz-only" in body and "classList.remove('hz-sale')" in body
    assert "dispatchEvent(new Event('resize'))" in body, "the bar keeps the ribbon's gap"


def test_the_bar_and_anchors_clear_the_ribbon():
    page = api.LANDING_HTML
    m = re.search(r"var nav = document\.querySelector\('\.nav'\).*?\}\)\(\);", page, re.S).group(0)
    assert "querySelector('.hz-ribbon')" in m and "setProperty('--sale-h'" in m
    assert "+ s;" in m, "--nav-h no longer counts the ribbon"
    assert ".hz-sale .nav{top:var(--sale-h)}" in hs.STYLE


# ── 4. the page's shape and rules ────────────────────────────────────────────

def test_the_page_keeps_its_shape(sale):
    html = sale()
    assert html.count("<section") == 8
    ids = set(re.findall(r'id="([^"]+)"', html))
    for href in set(re.findall(r'href="#([^"]+)"', html)):
        assert href in ids, href
    s = hs.current()
    for name, piece in list(hs.pieces(s).items()) + [("ribbon", hs.ribbon(s))]:
        if name != "<!--SALE_SCRIPT-->":
            assert "\u2014" not in piece and "&mdash;" not in piece, name


SCALE = {0, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128}


def test_the_sheet_keeps_the_design_rules():
    css = hs.STYLE
    off = []
    for m in re.finditer(r"(?:margin|padding|gap|row-gap|column-gap)(?:-(?:top|right|bottom|left))?"
                         r"\s*:\s*([^;}\n]+)", css):
        for tok in m.group(1).split():
            mm = re.fullmatch(r"(-?\d+(?:\.\d+)?)px", tok)
            if mm and abs(float(mm.group(1))) not in SCALE:
                off.append(tok)
    assert not off, off
    assert not re.findall(r"font-size\s*:\s*\d+\.\d+px", css)
    assert {w for w in re.findall(r"font-weight\s*:\s*(\d+)", css)} <= {"400", "600", "700", "800"}
    assert "cubic-bezier" not in css
    for m in re.finditer(r"transition:([^;}\n]+)", css):
        assert not re.search(r"(?<![\w.])\d*\.?\d+m?s\b", m.group(1)), m.group(0)
    # Lobster is a script face: one weight, never uppercase.
    for rule in re.findall(r"\{[^}]*var\(--display\)[^}]*\}", css):
        assert "font-weight:400" in rule and "uppercase" not in rule


def test_no_backslashes_in_the_embedded_sheet_or_script():
    assert "\\" not in hs.STYLE and "\\" not in hs.SCRIPT


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_script_parses(tmp_path):
    f = tmp_path / "sale.js"
    js = hs.SCRIPT.replace("<script>", "").replace("</script>", "").replace("__END_MS__", "0")
    f.write_text(js)
    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_landing_route_serves_it(sale):
    from fastapi.testclient import TestClient
    sale()
    html = TestClient(api.app, base_url="https://testserver").get("/").text
    assert 'id="holiday"' in html and 'class="hz-ribbon' in html


# ── 5. the Stripe check ──────────────────────────────────────────────────────

def _check():
    import importlib.util
    import pathlib
    p = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "promo_check.py"
    spec = importlib.util.spec_from_file_location("promo_check", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GOOD_PROMO = {"id": "promo_1AAAAAA", "active": True, "code": "CLIPPER", "times_redeemed": 2,
              "max_redemptions": None, "expires_at": None, "restrictions": {},
              "coupon": {"id": "cpn_1BBBBBB", "valid": True, "percent_off": 50.0,
                         "duration": "once", "applies_to": {"products": []}}}
NOW = datetime(2026, 11, 24, tzinfo=UTC).timestamp()


def test_a_code_that_matches_the_page_passes():
    pc = _check()
    assert pc.problems(GOOD_PROMO, GOOD_PROMO["coupon"], 50, {"Pro": "prod_p", "Studio": "prod_s"}, NOW) == []


@pytest.mark.parametrize("change,word", [
    (dict(active=False), "switched off"),
    (dict(expires_at=int(NOW) - 60), "expired"),
    (dict(max_redemptions=5, times_redeemed=5), "used up"),
])
def test_a_code_that_cannot_be_used_is_reported(change, word):
    pc = _check()
    promo = dict(GOOD_PROMO, **change)
    out = pc.problems(promo, promo["coupon"], 50, {}, NOW)
    assert any(word in p for p in out), out


@pytest.mark.parametrize("change,word", [
    (dict(percent_off=25.0), "25% off, the page says 50%"),
    (dict(percent_off=None, amount_off=500), "fixed amount"),
    (dict(duration="forever"), "forever"),
    (dict(duration="repeating", duration_in_months=3), "for 3 months"),
    (dict(applies_to={"products": ["prod_p"]}), "not Studio"),
])
def test_a_coupon_unlike_the_page_is_reported(change, word):
    pc = _check()
    coupon = dict(GOOD_PROMO["coupon"], **change)
    out = pc.problems(GOOD_PROMO, coupon, 50, {"Pro": "prod_p", "Studio": "prod_s"}, NOW)
    assert any(word in p for p in out), out


class _Fake:
    def __init__(self, promos):
        outer = self

        class PromotionCodes:
            def list(self, params):
                outer.asked = params
                return {"data": promos}

        class Coupons:
            def retrieve(self, cid, params=None):
                return dict(GOOD_PROMO["coupon"])

        class Prices:
            def retrieve(self, pid):
                return {"id": pid, "product": "prod_" + pid}

        self.promotion_codes, self.coupons, self.prices = PromotionCodes(), Coupons(), Prices()


def test_the_check_reads_stripe_and_says_ok(monkeypatch, capsys):
    pc = _check()
    monkeypatch.setattr(api.settings, "holiday_sale_code", "CLIPPER")
    monkeypatch.setattr(api.settings, "holiday_sale_percent", 50)
    monkeypatch.setattr(api.settings, "stripe_price_id_pro", "price_pro")
    monkeypatch.setattr(api.settings, "stripe_price_id_studio", "price_studio")
    fake = _Fake([GOOD_PROMO])
    assert pc.main([], client=fake, now=NOW) == 0
    out = capsys.readouterr().out
    assert fake.asked["code"] == "CLIPPER"
    assert "OK: CLIPPER takes 50% off the first month of Pro and Studio" in out
    assert "promo_1AAAAAA" not in out and "...AAAAAA" in out, "a whole Stripe id was printed"


def test_the_check_says_when_stripe_has_no_such_code(monkeypatch, capsys):
    pc = _check()
    monkeypatch.setattr(api.settings, "holiday_sale_code", "CLIPPER")
    assert pc.main([], client=_Fake([]), now=NOW) == 1
    assert "Stripe has no promotion code called CLIPPER" in capsys.readouterr().out
