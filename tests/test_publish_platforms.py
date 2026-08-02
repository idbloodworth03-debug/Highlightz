"""Publishing targets — specs, and the fit-check that exists to catch a
rejection BEFORE the user uploads.

The point of this module is that we do NOT post for the user. That removes the
entire app-review/OAuth/quota problem, and it means the only thing we owe them
is a file shaped correctly for where it is going, plus an honest warning when
it is not.
"""

import re
from pathlib import Path

import pytest

from src.publish import platforms as plat

FRONTEND = Path(__file__).resolve().parent.parent / "src/dashboard/aurora_html.py"
SRC = FRONTEND.read_text()


def test_a_normal_twitch_clip_fits_everywhere():
    """A Twitch clip is ~30s and the editor defaults to 9:16. If the common
    case warns, every warning becomes noise and users stop reading them."""
    for p in plat.PLATFORMS:
        assert plat.check_fit(p.id, 30.0, "9:16", "great clip") == [], \
            f"{p.id} warns about an ordinary 30s vertical clip"


def test_over_the_hard_limit_says_it_will_be_rejected():
    issues = plat.check_fit("instagram", 999.0, "9:16")
    assert issues and "rejected" in issues[0]


def test_the_shorts_cutoff_is_reported_as_losing_the_format_not_as_a_failure():
    """Over 60s YouTube still ACCEPTS the video — it just isn't a Short any
    more. Calling that a rejection would send users trimming for no reason."""
    issues = plat.check_fit("youtube", 90.0, "9:16")
    assert issues and "not a Short" in issues[0]
    assert "rejected" not in issues[0]


def test_ideal_and_hard_limits_are_distinct_for_every_platform():
    """Conflating them either scares users off valid clips or lets through
    ones that quietly lose their format."""
    for p in plat.PLATFORMS:
        assert p.ideal_max_s < p.hard_max_s, f"{p.id} has no room between them"


def test_a_wrong_aspect_ratio_is_flagged():
    assert any("crop" in i for i in plat.check_fit("tiktok", 20.0, "16:9"))


def test_an_over_long_caption_is_flagged():
    assert any("Caption is" in i for i in
               plat.check_fit("youtube", 20.0, "9:16", "x" * 300))


def test_an_unknown_platform_does_not_silently_pass():
    """Returning [] for a typo'd id would render as 'Fits' — a green tick for
    a platform that does not exist."""
    assert plat.check_fit("myspace", 10.0, "9:16") != []


def test_specs_cross_json_as_plain_data():
    import json
    json.dumps(plat.public_specs())


# ── the two halves must not drift ────────────────────────────────────────────

def test_the_javascript_fit_check_only_uses_fields_the_server_actually_sends():
    """The comparison logic is duplicated in JS (it has to run per keystroke),
    but the LIMITS must come from the server. If the JS reads a field the
    dataclass does not have, every clip silently reads as 'Fits'."""
    js = SRC[SRC.index("function fitIssues("):SRC.index("function ClipEditor(")]
    used = set(re.findall(r"pf\.(\w+)", js))
    available = set(plat.public_specs()[0])
    missing = used - available
    assert not missing, f"JS reads {missing}, which the server never sends"


def test_the_javascript_checks_every_rule_the_server_does():
    js = SRC[SRC.index("function fitIssues("):SRC.index("function ClipEditor(")]
    for field in ("hard_max_s", "ideal_max_s", "preferred_ratio", "caption_max"):
        assert field in js, f"JS fit-check ignores {field}"


def test_the_share_path_is_a_capability_check_not_a_browser_sniff():
    """navigator.share with files is genuinely missing on most desktop
    browsers. Sniffing user-agent instead would hide the button from browsers
    that support it and show a dead one to browsers that do not."""
    assert "navigator.canShare" in SRC and "navigator.share" in SRC
    assert not re.search(r"userAgent\s*\)?\s*\.\s*(match|indexOf|includes)\s*\(\s*['\"](?:iPhone|Android)",
                         SRC), "browser sniffing crept into the share path"


def test_backing_out_of_the_share_sheet_is_not_reported_as_an_error():
    """AbortError is the user tapping cancel. Showing 'sharing failed' for a
    deliberate cancel trains people to distrust the error line."""
    assert "AbortError" in SRC


def test_the_export_blob_is_kept_so_sharing_does_not_need_a_re_export():
    assert "setOutFile(" in SRC, "exported file discarded after download"
