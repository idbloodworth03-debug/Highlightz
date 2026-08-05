"""The Clip Library is the archive: approved clips only.

An undecided clip belongs in Clip Review and nowhere else. When it appeared in
both, people approved from the library and then found the review queue still had
work in it, and the clips they had actually kept were buried under ones they had
not looked at yet.

These are source-level guards rather than browser tests because the failure mode
is a quiet one — someone re-adds a status filter, or drops the `approved` check
while refactoring, and the screen goes back to listing everything without
throwing anything a JSX check or a smoke test would notice.
"""

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "src/dashboard/aurora_html.py"
SRC = FRONTEND.read_text()


def _library_screen() -> str:
    """The body of LibraryScreen, comments stripped.

    Stripping matters: this module's own explanatory comments in the component
    describe the removed filters by name, and an assertion that only searched
    raw text would happily match the comment saying they are gone.
    """
    m = re.search(r"function LibraryScreen\(.*?\n\}\n", SRC, re.S)
    assert m, "LibraryScreen not found"
    return re.sub(r"//.*", "", m.group(0))


def test_library_lists_only_approved_clips():
    body = _library_screen()
    assert "c.status==='approved'" in body, (
        "LibraryScreen must filter to approved clips — without it the archive "
        "lists pending clips that belong in Clip Review."
    )


def test_library_has_no_status_filter_row():
    """Pending is excluded by definition and a rejected clip is DELETED
    server-side, so 'All', 'Pending', 'Approved' and 'Rejected' were one live
    button, two duplicates of it, and one that could never match anything."""
    body = _library_screen()
    assert "'pending','approved','rejected'" not in body.replace(" ", ""), \
        "the dead status-filter row is back in the library"


def test_library_does_not_offer_approve_or_reject():
    """Judging a clip happens in one place. A second set of Approve/Reject
    buttons in the archive is how the two screens got out of step."""
    body = _library_screen()
    for prop in ("onApprove", "onReject"):
        assert prop not in body, f"{prop} is wired into LibraryScreen again"


def test_library_is_mounted_without_approve_or_reject_handlers():
    """The guard above is only real if the call site stopped passing them."""
    m = re.search(r"route==='library'\) screen=<LibraryScreen[^;]*;", SRC)
    assert m, "library route dispatch not found"
    mount = m.group(0)
    assert "onApprove" not in mount and "onReject" not in mount, \
        f"library mount still passes judging handlers: {mount}"
    assert "onGoReview" in mount, \
        "library must be able to send the user to Clip Review for pending clips"


def test_review_screen_still_judges_clips():
    """The other half of the split: moving pending out of the library is only
    correct while Clip Review still has the buttons."""
    m = re.search(r"route==='review'\) screen=<ReviewScreen[^;]*;", SRC)
    assert m, "review route dispatch not found"
    assert "onApprove:approveClip" in m.group(0)
    assert "onReject:rejectClip" in m.group(0)


def test_clip_card_can_grow_to_fit_its_actions():
    """The card's thumbnail is a share of the COLUMN width, so a card in a wide
    column is taller than one in a narrow column. A fixed height fitted the
    narrowest grid column and cut the action row — the 'Open on Twitch' button —
    off the bottom at wider ones (37px of it at a 1100px viewport).

    Both halves matter: min-height lets the card grow, and aspect-ratio is what
    makes the growth reach the content. Percentage padding resolves to zero
    while a grid row is being intrinsically sized, so the old
    `height:0;padding-bottom:56.25%` hack sized the row as if the thumbnail
    were not there.
    """
    m = re.search(r"\n\.rd-clip\{([^}]*)\}", SRC)
    assert m, ".rd-clip rule not found"
    rule = m.group(1)
    assert "min-height:360px" in rule, ".rd-clip must not pin a fixed height"
    assert not re.search(r"(?<!min-)height:\d", rule), \
        f".rd-clip has a fixed height again: {rule}"

    media = re.search(r"\n\.rd-media\{([^}]*)\}", SRC)
    assert media, ".rd-media rule not found"
    assert "aspect-ratio:16/9" in media.group(1)
    assert "padding-bottom" not in media.group(1), \
        "the percentage-padding aspect hack is back; it breaks intrinsic row sizing"
