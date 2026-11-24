"""The dashboard as it is served: compiled ahead of time when it can be.

Audit, 2026-11-24: every dashboard load downloaded Babel (about 3 MB) and
compiled about 600 KB of JSX in the browser before anything showed, about five
seconds even on a fast machine and longer on a phone. scripts/build_dashboard.js
compiles the same JSX with the same Babel and the same settings into
static/dashboard.js; this swaps the page over to that file.

ONLY WHEN IT MATCHES. The file carries the sha256 of the JSX it was built from.
If that is not the JSX in aurora_html.py right now (somebody changed the JSX
and did not rebuild), the page is served exactly as before, compiling in the
browser, so a stale file can never show an old dashboard.
tests/test_dashboard_bundle.py fails in that state, so it does not ship.

DASHBOARD_HTML itself is left alone: the JSX in it is the source, and the
tests read it.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import structlog

from src.dashboard.aurora_html import DASHBOARD_HTML

log = structlog.get_logger()

OPEN = '<script type="text/babel">'
BUNDLE = Path(__file__).parent / "static" / "dashboard.js"
_BABEL_TAG = re.compile(r'<script src="https://unpkg\.com/@babel/standalone@[^"]+"[^>]*></script>\n?')


def jsx_of(html: str) -> str:
    i = html.index(OPEN) + len(OPEN)
    return html[i:html.index("</script>", i)]


def source_hash(html: str = DASHBOARD_HTML) -> str:
    return hashlib.sha256(jsx_of(html).encode("utf-8")).hexdigest()


def bundle_hash(path: Path = BUNDLE) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r"source-sha256: ([0-9a-f]{64})", f.read(600))
        return m.group(1) if m else None
    except OSError:
        return None


def served(html: str = DASHBOARD_HTML, path: Path = BUNDLE) -> str:
    """The page with the compiled file in place of Babel and the JSX, or the
    page as it is when the file is missing or was built from other JSX."""
    want = source_hash(html)
    have = bundle_hash(path)
    if have != want:
        log.warning("dashboard_bundle_stale", built_from=(have or "none")[:12], source=want[:12])
        return html
    tag = f'<script defer src="/static/dashboard.js?v={want[:12]}"></script>'
    i = html.index(OPEN)
    j = html.index("</script>", i) + len("</script>")
    return _BABEL_TAG.sub("", html[:i]) + tag + html[j:]


SERVED_HTML = served()
