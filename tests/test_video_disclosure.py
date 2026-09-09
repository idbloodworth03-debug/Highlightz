"""
What the public documents say about holding video, checked against what the
code actually does.

WHY THIS FILE EXISTS. Until 2026-09-08 the product held no stream video at
all, and five user-facing surfaces said so in plain words. That changed
deliberately: src/ingestion/clip_recorder.py records a rolling buffer of the
live broadcast so a clip can also be a file. The promises were rewritten to
match — and a rewritten promise is exactly the kind of thing that gets quietly
reverted by a copy-paste from an older draft, or contradicted by a setting
somebody tunes six months from now without opening the Terms.

So this pins the two failure modes that matter, in the RENDERED pages rather
than the source, because the rendered page is what a user and a regulator
read:

  * a denial that is no longer true must not reappear anywhere public;
  * the retention period promised in the legal documents must be the one the
    code enforces, not a number that was true when it was typed.

These are deliberately about honesty rather than marketing. A page may say as
little as it likes about the capture; it may not say the opposite of the truth.
"""

import html
import re

import pytest
from starlette.testclient import TestClient

from config.settings import settings
from src.dashboard import api


PUBLIC_PAGES = ("/", "/tos", "/privacy", "/cookies", "/tutorial", "/compare",
                "/llms.txt", "/llms-full.txt")


@pytest.fixture()
def anon():
    return TestClient(api.app)


def _text(client, path: str) -> str:
    """A page as a reader sees it: tags stripped, entities resolved."""
    r = client.get(path)
    assert r.status_code == 200, f"{path} returned {r.status_code}"
    body = html.unescape(re.sub(r"<[^>]+>", " ", r.text))
    return re.sub(r"\s+", " ", body).lower()


# Denials that were true once and are not any more. Written as whole phrases:
# "never recorded" on its own is still fine and still used, because an
# opted-out channel genuinely is never recorded.
RETIRED_CLAIMS = (
    "never records",
    "never record your",
    "does not record, copy, download",
    "nothing is recorded",
    "we do not store any stream video",
    "no second copy of anyone's video",
    "no second copy of your stream",
    "nothing is downloaded, re-encoded or stored",
    "never records, downloads or re-hosts",
    "never records, stores, or re-hosts",
)


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_no_public_page_still_denies_that_video_is_held(anon, path):
    """The regression this catches is a paste from an older draft.

    Every one of these sentences shipped on this site and read well. That is
    what makes them dangerous now: they are the phrasings somebody reaches for
    when rewording this section, and each one is a denial of data collection
    that stopped being true.
    """
    body = _text(anon, path)
    found = [c for c in RETIRED_CLAIMS if c in body]
    assert not found, f"{path} still claims: {found}"


@pytest.mark.parametrize("path", ("/tos", "/privacy"))
def test_the_legal_pages_disclose_the_recording(anon, path):
    """Silence would be the other way to get this wrong.

    Dropping the denial is only half the job — a Terms that simply stopped
    mentioning video would leave a user with no way to learn that a rolling
    buffer of their broadcast sits on our disk.
    """
    body = _text(anon, path)
    assert "record" in body, f"{path} does not mention recording at all"
    assert "rolling" in body or "overwritten" in body, \
        f"{path} discloses recording without saying it is a short rolling buffer"
    assert "opted out" in body or "opt out" in body, \
        f"{path} does not say an opted-out channel is exempt"


@pytest.mark.parametrize("path", ("/tos", "/privacy"))
def test_the_promised_retention_is_the_one_the_code_enforces(anon, path):
    """THE DRIFT THIS EXISTS TO CATCH. Both documents state a retention
    ceiling; one setting enforces it (clip_file_max_age_days, read by
    src/clips/files.sweep). A policy promising 30 days over code that keeps 90
    is not a stale comment — it is a false statement about data retention in a
    published privacy policy, and nothing else in the suite would notice.

    Hence the placeholder substitution in api.py rather than a typed number.
    """
    body = _text(anon, path)
    assert "clipdays" not in body, \
        f"{path} shipped with the retention placeholder unsubstituted"
    expected = f"up to {settings.clip_file_max_age_days} days"
    assert expected in body, \
        f"{path} does not state the enforced retention ({expected!r})"


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_the_claim_that_is_still_true_is_not_overstated(anon, path):
    """We do not fetch video out of Twitch — no CDN scraping, no undocumented
    endpoints, no yt-dlp. That is a real and narrower claim than the one it
    replaced, and it is worth keeping accurate in the other direction too: no
    page may promise that Twitch's own hosted video is somehow re-served by us.
    """
    body = _text(anon, path)
    assert "we re-host" not in body and "we host the video" not in body, \
        f"{path} claims we host stream video, which we do not"
