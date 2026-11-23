"""Clips can be approved and rejected on the Live Streams tab (owner,
2026-11-23: "make it so you can accept or reject clips in the live streams
tab").

The tab's "Recent clips" row always drew Approve and Reject on a waiting
clip, but StreamsScreen handed the cards empty functions, so both buttons
did nothing, and neither did opening the clip. These pin that the cards get
the App's own approveClip / rejectClip / setModalClip (the ones Clip Review
uses, so the two screens cannot disagree), that waiting clips are listed
first, and that the row points to Clip Review when more are waiting than it
shows. The server side (clip_updated / clip_removed reach every tab) is the
existing approve and reject routes, unchanged.
"""

import re
from pathlib import Path

SRC = Path("src/dashboard/aurora_html.py").read_text()
JS = SRC.split('<script type="text/babel">')[1]


def _streams_screen():
    m = re.search(r"\nfunction StreamsScreen\(.*?\n\}\n", JS, re.S)
    assert m, "StreamsScreen moved"
    return m.group(0)


def test_the_cards_get_real_actions_not_empty_ones():
    s = _streams_screen()
    assert "onApprove={()=>{}}" not in s and "onReject={()=>{}}" not in s and "onOpen={()=>{}}" not in s
    assert ("<RdClip key={c.id} clip={c} onOpen={onOpen} onApprove={onApprove} "
            "onReject={onReject} onEdit={onEdit} libraryMode/>") in s


def test_the_app_hands_the_tab_the_same_actions_as_clip_review():
    line = JS[JS.index("else if(view==='streams') screen=<StreamsScreen"):][:700]
    assert "onApprove:approveClip,onReject:rejectClip,onOpen:setModalClip,onEdit:onEditClip" in line
    assert "onGoReview:()=>setRoute('review')" in line
    review = JS[JS.index("else if(view==='review') screen=<ReviewScreen"):][:400]
    assert "onApprove:approveClip,onReject:rejectClip,onOpen:setModalClip,onEdit:onEditClip" in review


def test_waiting_clips_come_first_and_the_rest_points_to_review():
    s = _streams_screen()
    assert "const waiting = mine.filter(c=>c.status==='pending').sort(newest);" in s
    assert "const recent = [...waiting, ...mine.filter(c=>c.status!=='pending').sort(newest)].slice(0, RECENT_SHOWN);" in s
    assert "{waiting.length} waiting for you" in s
    assert "{moreWaiting} more in Clip Review" in s and "onGoReview()" in s


def test_the_card_still_draws_approve_and_reject_for_a_waiting_clip():
    m = re.search(r"\nfunction RdClip\(.*?\n\}\n", JS, re.S)
    card = m.group(0)
    assert "clip.status==='pending' ?" in card
    assert "onApprove&&onApprove(clip.id)" in card and "onReject&&onReject(clip.id)" in card
