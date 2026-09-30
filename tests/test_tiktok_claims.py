"""No public page promises automatic TikTok posting (2026-09-30).

Highlightz never posts to TikTok by itself: TikTok's Direct Post rules put the
choices (who can view, interactions, disclosure, the music-usage agreement) on
the person for each post, and the app review says so. The landing page, the
compare page, the FAQ and the in-app Scheduler card all used to say the
Scheduler posts to "YouTube, TikTok and Instagram". Checked with posting held
back AND released, because the pages render different copy for each.
"""

import re
import subprocess
import sys

import pytest

_RENDER = """
import re, html
from fastapi.testclient import TestClient
from src.dashboard import api
c = TestClient(api.app, base_url="https://testserver")
out = []
for p in ("/", "/compare", "/tutorial", "/blog/clipping-platforms"):
    t = c.get(p).text
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    out.append(re.sub(r"\\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))))
print("\\n".join(out))
"""

AUTO_TIKTOK = re.compile(r"YouTube, TikTok (and|or) Instagram|Auto-posts to TikTok|"
                         r"switch on Autopilot to post it for you")


@pytest.mark.parametrize("flag", ["false", "true"])
def test_no_page_claims_tiktok_is_posted_for_you(flag):
    import os
    env = {**os.environ, "UPLOADS_ENABLED": flag, "PYTHONPATH": "."}
    text = subprocess.run([sys.executable, "-c", _RENDER], env=env, capture_output=True,
                          text=True, timeout=120).stdout
    assert len(text) > 5000, "the pages did not render"
    hits = [text[max(0, m.start() - 80):m.end() + 40] for m in AUTO_TIKTOK.finditer(text)]
    assert not hits, f"UPLOADS_ENABLED={flag}: {hits}"


def test_the_in_app_scheduler_card_does_not_either():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    assert "Connect YouTube, TikTok and Instagram and have every clip" not in page


def test_the_privacy_policy_says_how_tiktok_posting_works():
    """2026-09-30: the policy said the tokens "upload clips you schedule",
    which for TikTok (never scheduled, never automatic) was untrue."""
    from src.dashboard import api
    p = api.PRIVACY_HTML
    assert "upload clips you schedule" not in p
    assert "a TikTok post is made only when you press Post" in p
    assert "the picture is not stored" in p
    assert "Effective date: September 30, 2026" in p
