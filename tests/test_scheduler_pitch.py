"""The Scheduler's Pro card promises only what a buyer can use (audit,
2026-10-20), and two screens no longer pan sideways on a phone.

The card told Free users "Connect YouTube and Instagram and have every clip
you export posted for you" while both were "coming soon" (PUBLIC_PLATFORMS is
"tiktok") — selling a feature they could not have, against the rule that
public copy follows the release. Now /me.features.posting lists the platforms
open to ordinary accounts and the card is written from it.

On a phone the Campaigns filter row (four pills, ~447 px) panned the whole
screen 87 px sideways at 360 px, and a long example URL did 6 px on Tutorial.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.dashboard import api

SRC = Path("src/dashboard/aurora_html.py").read_text()
JS = SRC.split('<script type="text/babel">')[1]
NODE = shutil.which("node") or "/opt/node22/bin/node"


def _pitch(open_platforms):
    m = re.search(r"\nconst PITCH_NAMES = .*?\nfunction schedulerPitch\(open\) \{.*?\n\}\n", JS, re.S)
    assert m, "the pitch helpers moved — this test is no longer testing anything"
    prog = m.group(0) + f"\nconsole.log(schedulerPitch({json.dumps(list(open_platforms))}));"
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


needs_node = pytest.mark.skipif(not Path(NODE).exists(), reason="node is not installed here")


@needs_node
def test_with_tiktok_only_it_promises_tiktok_and_says_the_rest_are_coming():
    s = _pitch(["tiktok"])
    assert "Connect YouTube" not in s and "posted for you" not in s
    assert "post it to TikTok with one press." in s
    assert "YouTube and Instagram are coming soon." in s
    assert s.endswith("Included with Pro, with the Clip Editor and the VOD scanner.")


@needs_node
def test_when_instagram_opens_it_is_promised_and_only_youtube_is_coming():
    s = _pitch(["tiktok", "instagram"])
    assert s.startswith("Connect Instagram and have every clip you export posted for you at the time you pick, "
                        "and post to TikTok with one press.")
    assert "YouTube is coming soon." in s


@needs_node
def test_when_everything_is_open_nothing_is_coming():
    s = _pitch(["youtube", "tiktok", "instagram"])
    assert s.startswith("Connect YouTube and Instagram and have every clip") and "coming soon" not in s


@needs_node
def test_with_nothing_open_it_promises_no_posting():
    s = _pitch([])
    assert "ready for when posting opens" in s and "YouTube, TikTok and Instagram are coming soon." in s


def test_the_card_is_written_from_what_is_open():
    card = JS[JS.index("Scheduler is a Pro feature"):][:600]
    assert "schedulerPitch((me.features || {}).posting || [])" in card
    assert "Connect YouTube and Instagram and have every clip" not in card


def test_me_says_which_platforms_are_open(monkeypatch):
    from src.publish import release
    monkeypatch.setattr(release.settings, "uploads_enabled", True)
    monkeypatch.setattr(release.settings, "public_platforms", "tiktok")
    assert api._release_live_platforms() == ("tiktok",)
    monkeypatch.setattr(release.settings, "public_platforms", "tiktok,instagram")
    assert api._release_live_platforms() == ("tiktok", "instagram")
    monkeypatch.setattr(release.settings, "uploads_enabled", False)
    assert api._release_live_platforms() == ()
    src = Path("src/dashboard/api.py").read_text()
    assert '"posting": list(_release_live_platforms())' in src


def test_the_campaign_filter_scrolls_in_its_own_row_on_a_phone():
    m = re.search(r"@media\(max-width:700px\)\{\.apx-tabs\{([^}]*)\}", SRC)
    assert m and "overflow-x:auto" in m.group(1) and "max-width:100%" in m.group(1)
    assert ".apx-tabs button{flex-shrink:0;white-space:nowrap}" in SRC


def test_long_words_in_the_tutorial_wrap():
    m = re.search(r"\.tut-main\{([^}]*)\}", SRC)
    assert m and "overflow-wrap:anywhere" in m.group(1)
