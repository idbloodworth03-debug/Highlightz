"""The Account tab merged into Settings (owner, 2026-10-05: "can we combine
the settings and account tabs and just call it settings").

What these defend:
  1. one nav item, "Settings"; no Account item and no header entry for it;
  2. the Settings screen renders the channel settings AND the account half
     (plan, billing, connected accounts, legal, delete) in one scroller, in
     that order, without nesting two scroll containers;
  3. everything that used to open the Account tab (OAuth link returns, the
     header chip) opens Settings scrolled to the account half;
  4. no copy anywhere still sends people to an "Account tab/page".
"""

import pathlib
import re

from src.dashboard.aurora_html import DASHBOARD_HTML as H


def test_one_settings_item_and_no_account_item():
    nav = re.search(r"const NAV=\[(.*?)\];", H).group(1)
    assert "{id:'settings',label:'Settings',icon:'cog'}" in nav
    assert "'account'" not in nav
    head = re.search(r"const HEAD=\{(.*?)\};", H, re.S).group(1)
    assert "account:[" not in head


def test_the_settings_screen_holds_both_halves_in_order():
    i = H.index("<SettingsScreen {...{streams,profiles,me,activePlatform}} embedded/>")
    j = H.index('<div id="settings-account"', i)
    k = H.index("<AccountScreen me={me} connections={connections} accountsOn={uploadsOn} embedded/>", j)
    assert i < j < k
    # Embedded halves drop their own scroller; the page supplies one.
    assert H.count("<div className={embedded ? undefined : 'rd-scroll'}>") == 2


def test_old_account_routes_land_on_the_account_half():
    assert "setRoute('account')" not in H
    assert H.count("openAccount()") >= 3
    assert "document.getElementById('settings-account')" in H
    assert 'title="Settings"' in H


def test_no_copy_points_at_an_account_tab():
    root = pathlib.Path(__file__).resolve().parents[1] / "src"
    for f in root.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for bad in ("Account tab", "Account page", "**Account** tab"):
            for line in text.splitlines():
                if bad in line and "became the lower half of Settings" not in line:
                    raise AssertionError(f"{f.name}: still says {bad!r}: {line.strip()[:120]}")
