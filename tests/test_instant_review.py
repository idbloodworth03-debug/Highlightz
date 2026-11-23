"""Approve and reject show at once (owner, 2026-11-23: "there is some
latency when clicking reject or accept I dont want that to be the case").

The card changed only when the server answered, and the server does real work
first (the clip store rewritten, the decision recorded for learning, the
channel's threshold moved, with the split by asking the worker), on the loop
that scores every live channel. Now App's approveClip / rejectClip change the
clips state BEFORE the request, and put the clip back if the server refuses.
Every screen (Clip Review, Live Streams, the player) calls these two, so all
of them are instant. Checked in Chromium with the server slowed to 1.5 s: the
card changed in under 0.1 s, and a refused reject came back with its reason.
"""

import re
from pathlib import Path

JS = Path("src/dashboard/aurora_html.py").read_text().split('<script type="text/babel">')[1]


def _fn(name):
    m = re.search(r"\n  const " + name + r" = async\(id\)=>\{.*?\n  \};\n", JS, re.S)
    assert m, name + " moved"
    return m.group(0)


def test_approve_shows_before_the_server_answers():
    f = _fn("approveClip")
    shown = f.index("status:'approved',approved_at:Date.now()/1000")
    asked = f.index("await fetch(`/clips/${id}/approve`")
    assert shown < asked


def test_reject_shows_before_the_server_answers():
    f = _fn("rejectClip")
    gone = f.index("delete n[id]")
    asked = f.index("await fetch(`/clips/${id}/reject`")
    assert gone < asked


def test_a_refused_approve_is_put_back_with_its_reason():
    f = _fn("approveClip")
    after = f[f.index("await fetch(`/clips/${id}/approve`"):]
    assert after.index("putBack();") < after.index("if(r.status===403)")      # cap, and every other refusal
    assert "catch {\n      putBack();" in f                                    # the server not reached
    assert "const putBack = ()=>{ if(shown) setClips(p=>p[id]?{...p,[id]:before}:p); };" in f


def test_a_refused_reject_is_put_back_but_a_newer_copy_is_kept():
    f = _fn("rejectClip")
    assert "if(!r.ok && r.status!==404){ putBack();" in f
    assert "catch {\n      putBack();" in f
    assert "setClips(p=>p[id]?p:{...p,[id]:before})" in f


def test_no_instant_approve_when_the_weekly_limit_is_already_reached():
    f = _fn("approveClip")
    assert "const shown = !!before && before.status !== 'approved' && !libFull();" in f
    lib = re.search(r"\n  const libFull = \(\)=>\{.*?\n  \};\n", JS, re.S).group(0)
    assert "me.plan_limits.max_library_week" in lib and "cap < 1000000000" in lib


def test_the_record_as_it_was_comes_from_the_last_render():
    assert "const clipsRef = useRef(clips);\n  clipsRef.current = clips;" in JS
    assert "const before = clipsRef.current[id];" in _fn("approveClip")
    assert "const before = clipsRef.current[id];" in _fn("rejectClip")
