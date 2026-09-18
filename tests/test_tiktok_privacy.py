"""What the TikTok provider is allowed to ASK FOR, which is not what
creator_info offers.

THE BUG THIS FILE EXISTS TO PREVENT COMING BACK. The provider chose the most
public privacy level in creator_info's `privacy_level_options`, on the
assumption that an unaudited app would only ever be offered SELF_ONLY.
Measured against the real API on production 2026-09-18, with a sandbox client
key, creator_info offered:

    ['FOLLOWER_OF_CREATOR', 'MUTUAL_FOLLOW_FRIENDS', 'SELF_ONLY']

the provider asked for MUTUAL_FOLLOW_FRIENDS, and `video/init` refused the
entire post with "Please review our integration guidelines" — a message that
names nothing, on a call that had otherwise worked. The offer describes the
ACCOUNT's settings; whether the APP may use a level is a separate question,
and `TIKTOK_AUDITED` is the only thing that knows the answer.

The same response also carries the creator's own comment/duet/stitch
switches, and post_info must agree with them rather than hardcoding False.
"""

import asyncio
import copy

import pytest

from config.settings import settings
from src.publish.providers import _http
from src.publish.providers import tiktok as tk


CREATOR_INFO_FROM_PRODUCTION = {
    "data": {
        "privacy_level_options": ["FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS",
                                  "SELF_ONLY"],
        "max_video_post_duration_sec": 3600,
        "comment_disabled": False,
        "duet_disabled": True,
        "stitch_disabled": True,
    }
}


class Conn:
    access_token = "tok"
    refresh_token = "r"
    extra = {"username": "highlightz"}


@pytest.fixture
def api(monkeypatch, tmp_path):
    """Stand in front of TikTok and record what we send it."""
    sent = {}
    # A COPY. These tests edit the response to describe different accounts,
    # and sharing one module-level dict lets the first edit decide what every
    # later test sees.
    creator = {"body": copy.deepcopy(CREATOR_INFO_FROM_PRODUCTION)}

    async def _request(method, url, *, headers=None, params=None, data=None,
                       json=None, content=None, timeout=None):
        if url == tk._CREATOR:
            return _http.Response(status=200, _json=creator["body"])
        if url == tk._INIT:
            sent["init"] = json
            return _http.Response(status=200, _json={
                "data": {"publish_id": "pub1", "upload_url": "https://up.example/1"}})
        if url == tk._STATUS:
            return _http.Response(status=200, _json={
                "data": {"status": "PUBLISH_COMPLETE",
                         "publicaly_available_post_id": ["999"]}})
        if url.startswith("https://up.example/"):
            return _http.Response(status=200)
        raise AssertionError(f"unexpected call to {url}")

    async def _chunks(path, start, end, block=1 << 20):
        yield b"x" * (end - start + 1)

    async def _sleep(_s):
        return None

    monkeypatch.setattr(_http, "request", _request)
    monkeypatch.setattr(tk._http, "request", _request)
    monkeypatch.setattr(tk._http, "file_chunks", _chunks)
    monkeypatch.setattr(tk._http, "sleep", _sleep)

    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x" * 64)

    def post():
        return asyncio.run(tk.PROVIDER.post(Conn(), str(f), 64, "a caption", "mp4", 30.0))

    return type("A", (), {"post": staticmethod(post), "sent": sent,
                          "creator": creator})


# ── the privacy level ────────────────────────────────────────────────────────

def test_unaudited_asks_for_self_only_even_when_offered_more(api, monkeypatch):
    """The exact production response. Asking for MUTUAL_FOLLOW_FRIENDS here is
    what killed the post."""
    monkeypatch.setattr(settings, "tiktok_audited", False)
    res = api.post()
    assert api.sent["init"]["post_info"]["privacy_level"] == "SELF_ONLY"
    assert "private" in res.note.lower(), "the card must say the post is private"


def test_unaudited_never_asks_for_public_even_if_offered(api, monkeypatch):
    monkeypatch.setattr(settings, "tiktok_audited", False)
    api.creator["body"]["data"]["privacy_level_options"] = [
        "PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]
    api.post()
    assert api.sent["init"]["post_info"]["privacy_level"] == "SELF_ONLY"


def test_unaudited_falls_back_to_the_least_public_on_offer(api, monkeypatch):
    """SELF_ONLY should always be there. If it somehow is not, take the
    quietest thing available rather than the loudest."""
    monkeypatch.setattr(settings, "tiktok_audited", False)
    api.creator["body"]["data"]["privacy_level_options"] = [
        "PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS"]
    api.post()
    assert api.sent["init"]["post_info"]["privacy_level"] == "MUTUAL_FOLLOW_FRIENDS"


def test_once_audited_it_takes_the_most_public_level_offered(api, monkeypatch):
    """The flag has to actually do something, or the audit changes nothing."""
    monkeypatch.setattr(settings, "tiktok_audited", True)
    api.creator["body"]["data"]["privacy_level_options"] = [
        "PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]
    res = api.post()
    assert api.sent["init"]["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert res.note == "", "a public post must not carry the private-post warning"


def test_audited_still_respects_an_account_that_offers_no_public_level(api, monkeypatch):
    """A private TikTok account is not made public by our audit passing."""
    monkeypatch.setattr(settings, "tiktok_audited", True)
    api.post()
    assert api.sent["init"]["post_info"]["privacy_level"] == "FOLLOWER_OF_CREATOR"


def test_the_default_is_off_so_a_fresh_install_cannot_break_posting():
    from config.settings import Settings
    assert Settings().tiktok_audited is False


# ── the creator's own switches ───────────────────────────────────────────────

def test_duet_and_stitch_follow_the_creators_settings(api, monkeypatch):
    """Production had duets and stitches off. Sending False told TikTok to
    turn them back on, which is an invalid request, not a preference."""
    monkeypatch.setattr(settings, "tiktok_audited", False)
    api.post()
    info = api.sent["init"]["post_info"]
    assert info["disable_duet"] is True
    assert info["disable_stitch"] is True
    assert info["disable_comment"] is False


def test_an_account_with_everything_enabled_disables_nothing(api, monkeypatch):
    monkeypatch.setattr(settings, "tiktok_audited", False)
    api.creator["body"]["data"].update(duet_disabled=False, stitch_disabled=False,
                                       comment_disabled=False)
    api.post()
    info = api.sent["init"]["post_info"]
    assert (info["disable_duet"], info["disable_stitch"], info["disable_comment"]) == \
           (False, False, False)


def test_a_creator_info_that_omits_the_switches_disables_nothing(api, monkeypatch):
    """Missing must not read as True, or a sparse response silently turns off
    comments on every post."""
    monkeypatch.setattr(settings, "tiktok_audited", False)
    api.creator["body"]["data"] = {"privacy_level_options": ["SELF_ONLY"]}
    api.post()
    info = api.sent["init"]["post_info"]
    assert (info["disable_duet"], info["disable_stitch"], info["disable_comment"]) == \
           (False, False, False)


# ── the rest of the init payload still holds ─────────────────────────────────

def test_the_upload_is_still_a_file_push_not_a_url_pull(api, monkeypatch):
    """TikTok's "Verify domains" block is for pull_by_url. If this ever flips
    to PULL_FROM_URL the console needs a setting nobody made."""
    monkeypatch.setattr(settings, "tiktok_audited", False)
    api.post()
    assert api.sent["init"]["source_info"]["source"] == "FILE_UPLOAD"


def test_a_video_longer_than_the_account_allows_is_refused_before_upload(api, monkeypatch):
    monkeypatch.setattr(settings, "tiktok_audited", False)
    api.creator["body"]["data"]["max_video_post_duration_sec"] = 10
    with pytest.raises(tk.ProviderError) as e:
        asyncio.run(tk.PROVIDER.post(Conn(), "x", 64, "c", "mp4", 30.0))
    assert "10s" in str(e.value)
