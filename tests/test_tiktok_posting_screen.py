"""What the TikTok provider does with the person's choices.

TikTok's Direct Post rules (developers.tiktok.com content-sharing guidelines,
checked 2026-09-29): the person picks who can view with no default; comments,
duets and stitches are off until they turn them on and are greyed out when the
creator has switched them off in TikTok; commercial content is declared with
"Your brand" and/or "Branded content"; branded content cannot be private.
"""

import asyncio
import copy

import pytest

from config.settings import settings
from src.publish import providers
from src.publish.providers import _http
from src.publish.providers import tiktok as tk

CREATOR = {"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
                    "max_video_post_duration_sec": 3600, "comment_disabled": False,
                    "duet_disabled": True, "stitch_disabled": False,
                    "creator_nickname": "Ian", "creator_username": "ian",
                    "creator_avatar_url": "https://x/a.jpg"}}


class Conn:
    access_token = "tok"; refresh_token = "r"; extra = {"username": "ian"}


@pytest.fixture
def api(monkeypatch, tmp_path):
    sent = {}
    creator = {"body": copy.deepcopy(CREATOR)}

    async def _request(method, url, *, headers=None, params=None, data=None,
                       json=None, content=None, timeout=None):
        if url == tk._CREATOR:
            return _http.Response(status=200, _json=creator["body"])
        if url == tk._INIT:
            sent["init"] = json
            return _http.Response(status=200, _json={"data": {"publish_id": "p", "upload_url": "https://up.example/1"}})
        if url == tk._STATUS:
            return _http.Response(status=200, _json={"data": {"status": "PUBLISH_COMPLETE",
                                                              "publicaly_available_post_id": ["9"]}})
        if url.startswith("https://up.example/"):
            return _http.Response(status=200)
        raise AssertionError(url)

    async def _chunks(path, start, end, block=1 << 20):
        yield b"x" * (end - start + 1)

    async def _sleep(_s): return None
    monkeypatch.setattr(tk._http, "request", _request)
    monkeypatch.setattr(tk._http, "file_chunks", _chunks)
    monkeypatch.setattr(tk._http, "sleep", _sleep)
    f = tmp_path / "c.mp4"; f.write_bytes(b"x" * 64)

    def post(options=None):
        return asyncio.run(tk.PROVIDER.post(Conn(), str(f), 64, "cap", "mp4", 30.0, options=options))
    return type("A", (), {"post": staticmethod(post), "sent": sent, "creator": creator})


def _opts(**kw):
    base = {"privacy_level": "SELF_ONLY", "allow_comment": False, "allow_duet": False,
            "allow_stitch": False, "commercial": False, "consent": True}
    base.update(kw); return base


def _info(api): return api.sent["init"]["post_info"]


def test_interactions_are_off_unless_the_person_turned_them_on(api):
    api.post(_opts())
    i = _info(api)
    assert i["disable_comment"] and i["disable_duet"] and i["disable_stitch"]


def test_an_interaction_the_person_ticked_is_on(api):
    api.post(_opts(allow_comment=True, allow_stitch=True))
    i = _info(api)
    assert not i["disable_comment"] and not i["disable_stitch"] and i["disable_duet"]


def test_one_the_creator_switched_off_in_tiktok_stays_off_even_if_ticked(api):
    api.post(_opts(allow_duet=True))          # the account has duets disabled
    assert _info(api)["disable_duet"] is True


def test_commercial_content_is_declared_only_when_the_person_said_so(api):
    api.post(_opts())
    assert "brand_content_toggle" not in _info(api) and "brand_organic_toggle" not in _info(api)
    api.post(_opts(commercial=True, your_brand=True))
    assert _info(api)["brand_organic_toggle"] is True and _info(api)["brand_content_toggle"] is False


def test_unaudited_is_private_whatever_was_picked(api, monkeypatch):
    monkeypatch.setattr(settings, "tiktok_audited", False)
    res = api.post(_opts(privacy_level="PUBLIC_TO_EVERYONE"))
    assert _info(api)["privacy_level"] == "SELF_ONLY"
    assert "private" in res.note.lower()


def test_audited_uses_exactly_what_the_person_picked(api, monkeypatch):
    monkeypatch.setattr(settings, "tiktok_audited", True)
    res = api.post(_opts(privacy_level="MUTUAL_FOLLOW_FRIENDS"))
    assert _info(api)["privacy_level"] == "MUTUAL_FOLLOW_FRIENDS"
    assert res.note == "", "a post they chose to share must not say it is private"


def test_audited_and_they_chose_private_says_so_plainly(api, monkeypatch):
    monkeypatch.setattr(settings, "tiktok_audited", True)
    res = api.post(_opts(privacy_level="SELF_ONLY"))
    assert "as you chose" in res.note.lower()


def test_a_level_the_account_no_longer_offers_is_refused_not_guessed(api, monkeypatch):
    monkeypatch.setattr(settings, "tiktok_audited", True)
    api.creator["body"]["data"]["privacy_level_options"] = ["SELF_ONLY"]
    with pytest.raises(providers.ProviderError) as e:
        api.post(_opts(privacy_level="PUBLIC_TO_EVERYONE"))
    assert "choose again" in e.value.message.lower()
    assert "init" not in api.sent


def test_without_options_it_behaves_as_before(api, monkeypatch):
    """Autopilot and older cards: the creator's own switches decide."""
    monkeypatch.setattr(settings, "tiktok_audited", False)
    api.post(None)
    i = _info(api)
    assert i["privacy_level"] == "SELF_ONLY" and i["disable_duet"] is True and i["disable_comment"] is False
    assert "brand_content_toggle" not in i


def test_creator_info_gives_the_screen_what_it_needs(monkeypatch):
    async def _request(method, url, **kw):
        return _http.Response(status=200, _json=copy.deepcopy(CREATOR))
    monkeypatch.setattr(tk._http, "request", _request)
    got = asyncio.run(tk.PROVIDER.creator_info(Conn()))
    assert got["nickname"] == "Ian" and got["username"] == "ian" and got["avatar_url"]
    assert got["privacy_level_options"] == ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]
    assert got["duet_disabled"] is True and got["comment_disabled"] is False
    assert got["max_video_post_duration_sec"] == 3600
    assert "tok" not in str(got)
