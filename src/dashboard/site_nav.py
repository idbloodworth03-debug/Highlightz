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


def campaigns_tab(html: str, *, current: bool = False, show: bool | None = None,
                  signed_in: bool = False) -> str:
    """`html` with the Campaigns tab in its bar and footer, or without it.
    `current` marks it as the page being read (only /promote); `show` forces
    it either way (an admin previewing /promote before launch). `signed_in`:
    the bar is the one a signed-in visitor gets (for_visitor)."""
    show = settings.campaign_requests_enabled if show is None else show
    nav = (NAV_TAB_ON if current else NAV_TAB) if show else ""
    html = html.replace(NAV_MARK, nav).replace(FOOT_MARK, FOOT_LINK if show else "")
    return for_visitor(html, signed_in)


# ── A signed-in visitor is not offered "Sign in" (audit, 2026-11-21) ─────────
# Every public page's bar said "Sign in" and "Get started" to everybody. Someone
# signed in who opened the blog, the tutorial (the dashboard links to it) or a
# legal page saw "Sign in" and no way back, read it as having been signed out,
# and signed in with Twitch again. Users were reporting exactly that. Signed in,
# the bar offers the dashboard instead. Every copy of the bar uses this markup
# (site_nav's tests hold that), and /promote keeps its own campaign button.
SIGN_IN = '<a href="/login" class="nav-link">Sign in</a>'
GET_STARTED = '<a href="/login" class="btn btn-go">Get started</a>'
DASHBOARD_BTN = '<a href="/" class="btn btn-go">Dashboard</a>'
DASHBOARD_LINK = '<a href="/" class="nav-link">Dashboard</a>'


def for_visitor(html: str, signed_in: bool) -> str:
    """The page as a signed-in visitor should get it: "Dashboard" where the
    bar says "Sign in" / "Get started". Unchanged for everyone else."""
    if not signed_in:
        return html
    if GET_STARTED in html:
        return html.replace(SIGN_IN, "").replace(GET_STARTED, DASHBOARD_BTN)
    return html.replace(SIGN_IN, DASHBOARD_LINK)
