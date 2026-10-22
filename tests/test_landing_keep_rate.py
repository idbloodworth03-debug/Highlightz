"""The keep rate is NOT on the landing page (owner, 2026-10-22: "lets remove
the keep rate statistic from the landing page").

It was the share of judged clips streamers kept, published as the second big
figure under the cover and as a stat tile beside the clip count, once 50
clips had been judged. All of it is gone: the figure, the tile, the script
that refreshed it, and the two fields /landing/stats served it in, because a
public endpoint still serving the number would still be publishing it. The
per-user and per-channel keep rates in the admin console are unchanged; they
were never public.

These tests seed a ledger that the old code would have published (40 kept, 20
thrown out: 67%) and check it shows up nowhere a visitor can reach.
"""

import pytest
from fastapi.testclient import TestClient

from src.stats import stream_stats as ss


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    from src.dashboard import api
    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stream_stats.jsonl")
    n = 0
    for verdict, count in ((ss.APPROVED, 40), (ss.REJECTED, 20)):
        for _ in range(count):
            n += 1
            row = {"id": f"c{n}", "user_id": "u1", "channel": "novafps"}
            ss.record(ss.CAUGHT, row)
            ss.record(verdict, row)
    monkeypatch.setattr(api, "_clip_counter", 12345)
    yield


def _page():
    from src.dashboard import api
    return api.render_landing()


def test_the_landing_page_shows_no_keep_rate():
    html = _page()
    for gone in ("67%", "of reviewed clips kept", "kept reviewed clips",
                 'id="stat-kept"', 'id="lp-kept"', "data-kept"):
        assert gone not in html, f"the landing page still carries {gone!r}"


def test_the_three_figures_are_clips_channels_and_signals():
    """The keep rate was the second figure; "live signals in every score" was
    already the one shown whenever the rate was withheld, so it takes the
    place for good and the band still has three."""
    from src.billing.plans import PLAN_LIMITS
    html = _page()
    band = html[html.index('<div class="bignums">'):]
    band = band[:band.index("</div></div></div>") + len("</div></div></div>")]
    captions = [c.split("</div>")[0] for c in band.split('<div class="bign-k">')[1:]]
    assert captions == ["clips captured and counting",
                        "channels watched at once on Pro",
                        "live signals in every score"], captions
    assert "12,345" in band and str(PLAN_LIMITS["pro"]["max_streams"]) in band


def test_the_public_stats_endpoint_serves_the_clip_count_only():
    from src.dashboard import api
    d = TestClient(api.app).get("/landing/stats").json()
    assert d == {"clips_total": 12345}


def test_the_page_script_no_longer_asks_for_it():
    from src.dashboard.api import LANDING_HTML
    assert "kept_pct" not in LANDING_HTML
    assert "stat-kept" not in LANDING_HTML


def test_the_admin_keep_rates_are_untouched():
    """Only the public figure went. The ledger still works out each account's
    rate for the admin console."""
    t = ss.totals_by_user()["u1"]
    assert (t["approved"], t["rejected"], t["kept_pct"]) == (40, 20, 67)
