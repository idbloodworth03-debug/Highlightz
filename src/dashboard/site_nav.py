"""The Campaigns tab on every public page (owner, 2026-10-23: "I need this to
all be explained on a new tab in the landing page").

Every public page carries its own copy of the bar and the footer (landing, the
legal shell, /tutorial, /compare, /blog, /promote). Each copy has two markers
where the tab goes, and the page's handler fills them per request: the tab
appears once CAMPAIGN_REQUESTS_ENABLED is on, so a deploy never advertises a
page that answers 404. A copy served without going through here keeps the
markers, which are HTML comments and render as nothing.
"""

from __future__ import annotations

from config.settings import settings

NAV_MARK = "<!--NAV_CAMPAIGNS-->"
FOOT_MARK = "<!--FOOT_CAMPAIGNS-->"
NAV_TAB = '<a href="/promote" class="nav-link">Campaigns</a>'
NAV_TAB_ON = '<a href="/promote" class="nav-link on" aria-current="page">Campaigns</a>'
FOOT_LINK = '<a href="/promote">Campaigns</a>'


def campaigns_tab(html: str, *, current: bool = False, show: bool | None = None) -> str:
    """`html` with the Campaigns tab in its bar and footer, or without it.
    `current` marks it as the page being read (only /promote); `show` forces
    it either way (an admin previewing /promote before launch)."""
    show = settings.campaign_requests_enabled if show is None else show
    nav = (NAV_TAB_ON if current else NAV_TAB) if show else ""
    return html.replace(NAV_MARK, nav).replace(FOOT_MARK, FOOT_LINK if show else "")
