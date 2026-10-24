"""The admin pages' escaping, and one-line titles (quick audit, 2026-10-24).

What these defend:
  1. the admin page's esc() escapes both quotes, so text a user wrote cannot
     close an attribute value and add attributes of its own. Campaign
     requests made campaign titles and summaries streamer-written, and the
     campaign editor puts them in value="..." (reproduced in Chromium before
     the fix: an injected onmouseover ran in the admin's session);
  2. the feedback page's own esc() does the same;
  3. a campaign's or a request's title and summary are one line, and an alert
     email's subject is too (a line break made the SMTP path raise, and may
     make Resend refuse the owner's email).
"""

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

NODE = shutil.which("node") or "/opt/node22/bin/node"
needs_node = pytest.mark.skipif(not Path(NODE).exists(), reason="node is not installed here")

PAYLOADS = ['Cup" autofocus onfocus="x()', "Win' onmouseover='x()", "<b>&amp;</b>", "", None, 0, "plain"]


def _function(page: str, start: str) -> str:
    """The source of one function in a page's script, by its first line."""
    i = page.index(start)
    depth, j = 0, page.index("{", i)
    while True:
        depth += {"{": 1, "}": -1}.get(page[j], 0)
        j += 1
        if depth == 0:
            return page[i:j]


def _run(fn_src: str, name: str) -> list:
    prog = fn_src + "\nconsole.log(JSON.stringify(" + json.dumps(PAYLOADS) + ".map(" + name + ")));"
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _safe_in_attribute(s: str) -> bool:
    return not any(ch in s for ch in "\"'<>")


@needs_node
def test_the_admin_page_escapes_quotes():
    from src.dashboard.api import ADMIN_HTML
    got = _run(_function(ADMIN_HTML, "function rvEsc(t){"), "rvEsc")
    assert got[0] == "Cup&quot; autofocus onfocus=&quot;x()"
    assert got[1] == "Win&#39; onmouseover=&#39;x()"
    assert got[2] == "&lt;b&gt;&amp;amp;&lt;/b&gt;", "& must be escaped first, and only once"
    assert got[3:] == ["", "", "0", "plain"]
    assert all(_safe_in_attribute(s) for s in got)
    assert "const esc = rvEsc;" in ADMIN_HTML


@needs_node
def test_the_feedback_page_escapes_quotes():
    from src.dashboard.api import _ADMIN_FEEDBACK_HTML as page
    got = _run(_function(page, "function esc(s){"), "esc")
    assert got[0] == "Cup&quot; autofocus onfocus=&quot;x()" and all(_safe_in_attribute(s) for s in got)


def test_the_campaign_editor_fills_its_values_through_that_helper():
    """The fields a streamer writes reach the editor only through esc()."""
    from src.dashboard.api import ADMIN_HTML
    editor = ADMIN_HTML[ADMIN_HTML.index("function openCampaignEditor("):]
    editor = editor[:editor.index("\nfunction ")]
    for field in ("c.title", "c.summary", "c.judging_note", "c.caption_template", "c.rules"):
        assert "esc(" + field in editor, field
    # No second, quote-unsafe escaper hiding on the page.
    assert "d.textContent" not in _function(ADMIN_HTML, "function rvEsc(t){")
    assert ADMIN_HTML.count("function esc(") == 0 and ADMIN_HTML.count("function rvEsc(") == 1


def test_titles_and_summaries_are_one_line(tmp_path, monkeypatch):
    from src import campaigns as camp
    from src.campaigns import requests as creq
    assert camp.one_line(" A\nB\r\n\tC\x00D E  F ", 120) == "A B C D E F"
    assert camp.one_line("x" * 200, 120) == "x" * 120
    now = time.time()
    c = camp.clean({"title": "Cup\nBcc: x@example.com", "summary": "Line one\nline two", "rules": "1.\n2.",
                    "start_at": now, "end_at": now + 86400, "payouts": [10], "platforms": ["tiktok"]})
    assert (c["title"], c["summary"], c["rules"]) == ("Cup Bcc: x@example.com", "Line one line two", "1.\n2.")
    from tests.test_campaign_requests import ask, STREAMER
    monkeypatch.setattr(creq.settings, "campaign_flat_fee_usd", 0.0)
    brief = "Funny moments and clutch plays.\nKeep every clip under a minute."
    r = creq.clean(ask(title="Clip\r\nmy week", summary="Best\tmoments", brief=brief), STREAMER)
    assert (r["title"], r["summary"], r["brief"]) == ("Clip my week", "Best moments", brief)


def test_an_alert_subject_is_one_line(monkeypatch):
    from src import alerts
    sent = []

    async def fake(to, subject, text):
        sent.append(subject)
        return True, "sent"
    monkeypatch.setattr(alerts.settings, "alert_email_to", "owner@example.com")
    monkeypatch.setattr(alerts.settings, "resend_api_key", "re_test")
    monkeypatch.setattr(alerts, "_send_resend", fake)
    assert asyncio.run(alerts.send("New campaign request: Cup\nBcc: x@example.com", "body")) == (True, "sent")
    assert sent == ["New campaign request: Cup Bcc: x@example.com"]
