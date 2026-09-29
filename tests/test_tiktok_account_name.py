"""What name a TikTok connection is stored under.

THE BUG. The owner connected TikTok and the Account screen showed "@TikTok":
the placeholder, not their account. `_user` asked TikTok for `username`, a
field of the user.info.profile scope, and the app only requests
user.info.basic. TikTok answers an out-of-scope field with an error and NO
user, so display_name came back empty and the provider stored its own label
as the account's name. The token exchange had worked the whole time, which is
why the connection looked healthy while naming nobody.

Two rules keep it from coming back: the lookup asks only for basic-scope
fields, and when it still comes back empty the name is taken from
creator_info, which the posting scope we hold does return.
"""

import asyncio

import pytest

from src.publish.providers import _http
from src.publish.providers import tiktok as tk


@pytest.fixture
def api(monkeypatch):
    seen = {"user_params": None}
    reply = {"user": {"data": {"user": {"open_id": "oid1", "display_name": "Nova Plays"}}},
             "creator": {"data": {"creator_nickname": "Nova Plays",
                                  "creator_username": "novaplays"}}}

    async def _request(method, url, *, headers=None, params=None, data=None,
                       json=None, content=None, timeout=None):
        if url == tk._TOKEN:
            return _http.Response(status=200, _json={
                "access_token": "AT", "refresh_token": "RT", "open_id": "oid1",
                "expires_in": 86400, "scope": "user.info.basic,video.publish"})
        if url == tk._USER:
            seen["user_params"] = params
            return _http.Response(status=200, _json=reply["user"])
        if url == tk._CREATOR:
            return _http.Response(status=200, _json=reply["creator"])
        raise AssertionError(f"unexpected call to {url}")

    monkeypatch.setattr(tk._http, "request", _request)
    return type("A", (), {"seen": seen, "reply": reply,
                          "connect": staticmethod(lambda: asyncio.run(tk.PROVIDER.exchange("code", "v")))})


def test_the_lookup_asks_only_for_fields_the_basic_scope_grants(api):
    api.connect()
    fields = set(api.seen["user_params"]["fields"].split(","))
    assert fields <= {"open_id", "union_id", "avatar_url", "display_name"}, \
        f"asking for {fields} — username needs a scope we do not request"


def test_the_account_is_named_after_the_person_not_the_platform(api):
    got = api.connect()
    assert got["account_name"] == "Nova Plays"
    assert got["extra"]["username"] == "novaplays"


def test_a_refused_profile_lookup_falls_back_to_creator_info(api):
    """The exact production failure: an error and no user."""
    api.reply["user"] = {"error": {"code": "scope_not_authorized", "message": "no"}}
    got = api.connect()
    assert got["account_name"] == "Nova Plays"
    assert got["account_id"] == "oid1"


def test_when_nothing_names_the_account_the_connection_still_works(api):
    api.reply["user"] = {"error": {"code": "scope_not_authorized"}}
    api.reply["creator"] = {"error": {"code": "internal_error"}}
    got = api.connect()
    assert got["account_name"] == "TikTok" and got["access_token"] == "AT"


def test_the_handle_alone_is_enough_to_name_it(api):
    api.reply["user"] = {"error": {"code": "scope_not_authorized"}}
    api.reply["creator"] = {"data": {"creator_username": "novaplays"}}
    assert api.connect()["account_name"] == "novaplays"
