"""Chat is not written in English, and the detector has to hear it anyway.

TWO SEPARATE FAILURES were behind this, and the tests are grouped by which one
they guard.

  THE WORDS. Every keyword was English. A Spanish chat losing its mind scored
  the same as a silent one on the KEYWORD signal. That signal is only worth 5
  points of ~110, so on its own this is a small miss.

  THE MATCHING, which is the large one. Three distinct people asking for a clip
  forces the score to CLIP_IT_FLOOR (90) and always clips — the single
  highest-leverage path into the queue — and it was an English-only regex. A
  Japanese chat can be unanimous in asking for a clip and never reach it. Worse,
  Japanese and Chinese put no spaces between words, so `\\w+` returns ONE token
  for a whole sentence: adding CJK words to a set that is matched by membership
  would have looked like a fix and changed nothing.

WHAT THESE MUST ALSO PROTECT is the English case. Every term added for another
language is a chance to fire on an English message that deserved no score, so
the collision tests below are not decoration — they already caught "crack" (a
star player in Spanish, a fracture in English) and "brutal".
"""

import re
import time

import pytest

from src.chat.keywords import (
    CLIP_TRIGGER_PHRASES,
    HIGH_ENERGY_KEYWORDS,
    HYPE_SUBSTRING_RE,
    LANGUAGES,
    fold,
    keyword_hit,
)
from src.chat.metrics import ChatMetrics


def _hits(message: str) -> bool:
    folded = fold(message)
    return keyword_hit(folded, set(re.findall(r"\w+", folded)),
                       HIGH_ENERGY_KEYWORDS)


def _requests_a_clip(message: str) -> bool:
    return bool(CLIP_TRIGGER_PHRASES.search(fold(message)))


# ── hype vocabulary, per language ────────────────────────────────────────────

HYPE = {
    "English":    "holy that was insane",
    "Spanish":    "que locura increíble",
    "Portuguese": "que absurdo mano kkkkkk",
    "German":     "krass das war unglaublich",
    "French":     "c'est incroyable énorme",
    "Russian":    "это безумие жесть",
    "Korean":     "미쳤다 대박 ㅋㅋㅋㅋ",
    "Japanese":   "やばい今の草",
    "Chinese":    "太强了哈哈哈",
    "Italian":    "assurdo pazzesco",
    "Polish":     "niesamowite masakra",
    "Turkish":    "İNANILMAZ efsane",
}


@pytest.mark.parametrize("language,message", sorted(HYPE.items()))
def test_an_excited_chat_is_heard_in(language, message):
    assert _hits(message), f"{language} hype scores nothing"


# ── the clip request, which forces the score to 90 ───────────────────────────

REQUESTS = {
    "English":    "someone clip that",
    "Spanish":    "clipealo por favor",
    "Portuguese": "alguem clipa isso",
    "German":     "das muss geclippt werden",
    "French":     "faut clipper ça",
    "Russian":    "клипай это",
    "Korean":     "클립 떠주세요",
    "Japanese":   "今のクリップして",
    "Chinese":    "剪辑一下这个",
    "Turkish":    "kliple bunu",
    "Italian":    "clippa questo",
    "Polish":     "klipnij to",
}


@pytest.mark.parametrize("language,message", sorted(REQUESTS.items()))
def test_a_chat_asking_for_a_clip_is_understood_in(language, message):
    assert _requests_a_clip(message), \
        f"{language} cannot reach the clip-it trip-wire"


def test_the_trip_wire_actually_fires_on_a_non_english_chat():
    """End to end through ChatMetrics, not just the regex.

    The unit above proves the pattern matches. This proves the pipeline counts
    it: three DISTINCT senders inside the window is what the trip-wire needs,
    and the snapshot is what the trigger engine reads.
    """
    m = ChatMetrics()
    for author, msg in [("ana", "clipealo por favor"),
                        ("beto", "clipealo!!"),
                        ("carla", "alguien que clipee eso")]:
        m.ingest(msg, author=author)
    snap = m.snapshot()
    assert snap.clip_it_unique_senders >= 3, \
        "a unanimous Spanish chat does not reach the trip-wire"
    assert snap.keyword_hits >= 1


def test_a_japanese_sentence_is_one_token_and_still_matches():
    """The reason CJK terms are matched as substrings rather than by set
    membership. If this ever tokenises into words the design can be simplified;
    while it does not, a set-based match is silently a no-op."""
    sentence = "やばい今のクリップして"
    assert len(re.findall(r"\w+", sentence)) == 1, \
        "Japanese now tokenises — revisit the substring matcher"
    assert _hits(sentence) and _requests_a_clip(sentence)


# ── folding ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("written,folded", [
    ("increíble", "increible"),      # Spanish accents are optional in chat
    ("énorme",    "enorme"),
    ("İNANILMAZ", "inanilmaz"),      # Turkish dotted capital I
])
def test_latin_accents_are_folded_away(written, folded):
    assert fold(written) == folded


@pytest.mark.parametrize("text", ["клипай", "クリップ", "미쳤다", "ㅋㅋ", "剪辑"])
def test_non_latin_marks_are_left_alone(text):
    """THE BUG THE FIRST VERSION SHIPPED WITH, in test form.

    Stripping every combining mark looked equivalent and broke two languages:
    Cyrillic "й" decomposes to "и" + breve, so "клипай" became "клипаи"; and
    Japanese "プ" decomposes to "フ" + handakuten, so "クリップ" became
    "クリッフ". In both scripts the mark is part of the letter. Only Latin
    diacritics may be folded.
    """
    assert fold(text) == text.lower()


def test_both_sides_of_the_match_are_folded_the_same_way():
    """A list written with accents can only match text that keeps them, and
    the matcher folds text. Folding one side and not the other is a silent
    no-op, which is exactly how the Russian and Japanese cases failed."""
    for term in HIGH_ENERGY_KEYWORDS:
        assert fold(term) == term, f"{term!r} is stored unfolded"


# ── not making English worse ─────────────────────────────────────────────────

# Ordinary English chat. None of it deserves a score, and every one of these
# was a plausible collision for a term in some other language.
ENGLISH_NOISE = [
    "hey guys how is everyone doing today",
    "what game is this",
    "i have a crack in my screen",
    "that was brutal honestly",
    "ok cool", "kk sounds good", "brb", "nice one man",
    "check out www.twitch.tv/somebody",
    "my number is 5551234",
    "that was 233 damage",
    "the alter ego thing",
    "ale is a kind of beer",
    "he plays top lane",
    "i will be back in an hour",
]


@pytest.mark.parametrize("message", ENGLISH_NOISE)
def test_ordinary_english_chat_scores_nothing(message):
    assert not _hits(message), f"a foreign-language term fires on {message!r}"
    assert not _requests_a_clip(message), \
        f"{message!r} reads as a clip request"


COMMON_ENGLISH_WORDS = frozenset("""
    the be to of and a in that have it for not on with he as you do at this but
    his by from they we say her she or an will my one all would there their what
    so up out if about who get which go me when make can like time no just him
    know take people into year your good some could them see other than then now
    look only come its over think also back after use two how our work first well
    way even new want because any these give day most us man thing woman life
    child world school state family student group country problem hand part place
    case week company system program question night point home water room mother
    area money story fact month lot right study book eye job word business issue
    side kind head house service friend father power hour game line end member law
    car city community name president team minute idea kid body information back
    parent face others level office door health person art war history party
    result change morning reason research girl guy moment air teacher force
    education crack brutal alter ale corta top clap goat
""".split())


def test_no_latin_script_term_is_a_common_english_word():
    """The rule the module states, enforced.

    Twitch chat is English by default, and a term that doubles as an English
    word turns every mention of it into a score. Non-Latin scripts are exempt:
    Cyrillic and CJK cannot collide with English, which is why those lists can
    be freer.

    The English list itself is exempt from its own rule — "clip", "holy" and
    "insane" ARE English words, deliberately.
    """
    from src.chat.keywords import _HYPE_BY_LANG
    offenders = []
    for lang, words in _HYPE_BY_LANG.items():
        if lang == "en":
            continue
        for w in words:
            if w.isascii() and w in COMMON_ENGLISH_WORDS:
                offenders.append(f"{lang}:{w}")
    assert not offenders, \
        f"these fire on ordinary English chat: {sorted(offenders)}"


def test_the_collision_check_can_actually_fail():
    """Guards the guard. The test above passes trivially if the word list is
    empty or the comparison never matches, and a vacuous rule is worse than no
    rule because it reads like protection."""
    assert "crack" in COMMON_ENGLISH_WORDS and "brutal" in COMMON_ENGLISH_WORDS
    assert "crack" not in HIGH_ENERGY_KEYWORDS, \
        "crack came back — it is a fracture in English and a star player in Spanish"


# ── it has to stay cheap ─────────────────────────────────────────────────────

def test_matching_is_cheap_enough_for_live_chat():
    """ingest() runs on EVERY chat line of every monitored stream.

    A busy channel is hundreds of messages a second and the box runs many at
    once, so an expensive matcher is a capacity problem rather than a latency
    one. Deliberately loose — this catches an order-of-magnitude regression
    (a per-message re.compile, a loop over every term), not normal variance.
    """
    msgs = ["holy that was insane clip it", "que locura increible",
            "やばい今のクリップして", "hey how is everyone doing",
            "미쳤다 대박 ㅋㅋㅋ"] * 400
    t0 = time.perf_counter()
    for msg in msgs:
        folded = fold(msg)
        keyword_hit(folded, set(re.findall(r"\w+", folded)), HIGH_ENERGY_KEYWORDS)
        CLIP_TRIGGER_PHRASES.search(folded)
    per_msg_us = (time.perf_counter() - t0) / len(msgs) * 1e6
    assert per_msg_us < 200, f"{per_msg_us:.0f}us per message is too slow for live chat"


def test_english_costs_nothing_extra_for_the_substring_pass():
    """The set intersection short-circuits before the CJK scan, so English —
    the common case — never pays for scripts it does not use."""
    folded = fold("holy that was insane")
    assert keyword_hit(folded, {"holy", "that", "was", "insane"},
                       HIGH_ENERGY_KEYWORDS)
    assert not HYPE_SUBSTRING_RE.search(folded), \
        "an English message reaches the substring matcher"


def test_every_language_is_declared():
    assert set(LANGUAGES) >= {"en", "es", "pt", "de", "fr", "ru", "it", "pl", "tr"}


# ── the VOD scanner has to hear the same things ──────────────────────────────

def test_the_vod_scanner_uses_the_same_matcher_as_the_live_engine():
    """It had its own inline copy — .lower() plus a set intersection — which
    could not match CJK (one token per sentence) and did not fold accents. A
    scan of a Spanish or Japanese VOD therefore scored lower than the live
    engine did on the identical stream, which is the sort of gap nobody
    reports because both numbers look plausible on their own.
    """
    import inspect
    from src.vod import analyzer
    src = inspect.getsource(analyzer)
    src = "\n".join(l for l in src.split("\n") if not l.strip().startswith("#"))
    assert "keyword_hit(" in src, "the VOD scanner matches keywords its own way again"
    assert "t.lower()) & HIGH_ENERGY_KEYWORDS" not in src, \
        "the old English-only intersection is back"
    # Every clip-request search must go through fold(), or accents decide it.
    for call in re.findall(r"CLIP_TRIGGER_PHRASES\.search\(([^)]*)\)", src):
        assert "fold(" in call or call.strip() in ("f", "folded"), \
            f"CLIP_TRIGGER_PHRASES.search({call}) skips folding"


@pytest.mark.parametrize("language,message", sorted(REQUESTS.items()))
def test_a_vod_scan_counts_the_clip_request_in(language, message):
    """Through the analyzer's own scoring call, not the regex."""
    from src.vod import analyzer
    folded = analyzer.fold(message)
    assert analyzer.CLIP_TRIGGER_PHRASES.search(folded), \
        f"a VOD scan misses a {language} clip request"


def test_an_accented_term_in_the_list_still_matches_both_spellings():
    """The lists are written the way each language spells itself, and folded
    when the flat set is built. Mutation testing found that folding pointless
    because every entry had been pre-stripped by hand — so nothing detected the
    build-time fold being removed. Spanish "increíble" is now stored accented,
    which makes that step load-bearing."""
    assert _hits("que increíble"), "the accented spelling does not match"
    assert _hits("que increible"), "the unaccented spelling does not match"


def test_turkish_matches_however_the_keyboard_typed_it():
    """Turkish ı and i are different letters, so fold() cannot merge them and
    both spellings are held. İNANILMAZ folds to the dotted-i form."""
    for spelling in ("inanılmaz", "inanilmaz", "İNANILMAZ", "İnanılmaz"):
        assert _hits(f"bu {spelling} ya"), f"{spelling} scores nothing"


def test_ingest_folds_before_matching(monkeypatch):
    """Through ChatMetrics, not the helpers.

    Mutation testing replaced fold() with .lower() inside ingest() and every
    other test still passed, because they all used already-unaccented Spanish.
    A message typed the way a person actually types it is what catches that.
    """
    m = ChatMetrics()
    m.ingest("esto es increíble", author="ana")
    m.ingest("İNANILMAZ", author="beto")
    assert m.snapshot().keyword_hits == 2, \
        "accented or Turkish chat is not scored by the live path"
