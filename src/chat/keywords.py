"""Hype and clip-request vocabulary, in the languages Twitch chat is written in.

WHY THIS IS ITS OWN MODULE. The lists were English-only and lived inline in
metrics.py, which was fine while the only question was "does this word appear".
Supporting other languages changes the MATCHING, not just the words — and the
two problems are different enough to deserve naming.

    1. THE WORDS. Spanish, Portuguese, German, French, Russian, Italian,
       Polish and Turkish chat all tokenise correctly today: `\\w+` is
       Unicode-aware in Python 3, so a Russian message already splits into
       Russian words. Those languages were never broken — they were simply
       absent from the list.

    2. THE MATCHING. Japanese and Chinese do not put spaces between words, so
       `re.findall(r"\\w+", "やばい今のクリップして")` returns ONE token: the
       whole sentence. Set membership can never match a CJK keyword, however
       many are added. Those need substring search, which is why SUBSTRING
       terms are held separately and compiled into one alternation.

WHERE THIS ACTUALLY MATTERS. The KEYWORD signal is worth 5 points of ~110, so
adding vocabulary barely moves a score. The clip-request trip-wire is a
different story: three distinct people asking for a clip forces the score to
CLIP_IT_FLOOR (90), which clears every threshold and always clips. That path
was English-only, so a Spanish chat spamming "clipealo" or a Japanese chat
spamming "切り抜き" could be unanimous and never fire it. That is the gap worth
closing, and it is why CLIP_REQUEST gets the same per-language treatment.

CHOOSING TERMS. Two rules, both about not making the English case worse:

  * No term short enough to collide with an English word. German "alter" and
    Polish "ale" are ordinary chat words in their own language and ordinary
    ENGLISH words too, so they are omitted rather than risk firing on English
    chat. tests/test_multilingual_keywords.py asserts no Latin-script term
    collides with a list of common English words.
  * No term that appears inside unrelated text. Japanese "www" (laughter) is
    omitted because it matches every URL; Thai "555" and Chinese "233" are
    omitted because they match ordinary numbers. The laughter markers that
    survive are the ones written in a script nothing else uses.
"""

from __future__ import annotations

import re
import unicodedata

# ── Tokenisable hype vocabulary, by language ─────────────────────────────────
# Everything here is matched by SET MEMBERSHIP against `\w+` tokens, so these
# languages must separate words with spaces. Written the way the language is
# actually spelled: every entry is passed through fold() when the flat set is
# built, and messages are folded before matching, so "increíble" here matches a
# chat that types either "increíble" or "increible".

_HYPE_BY_LANG: dict[str, frozenset[str]] = {
    "en": frozenset({
        "clip", "clipit", "pogchamp", "pog", "poggers", "omegalul", "lul",
        "holy", "insane", "wtf", "omg", "nooo", "noway", "no way", "lets go",
        "letsgo", "gg", "rip", "ez", "clutch", "monkas", "pepega", "sadge",
        "widepeeposad", "catjam", "hyperclap", "clap", "goat",
    }),
    # Spanish. "clipealo" and its variants are the request; the rest is the
    # ordinary vocabulary of a chat losing its mind.
    "es": frozenset({
        "clipealo", "clipear", "clipeen", "cortalo", "increíble", "locura",
        "tremendo", "vamos", "dios", "flipante", "bestia",
        "jaja", "jajaja", "jajajaja", "ostia", "hostia",
    }),
    # Portuguese, overwhelmingly Brazilian on Twitch.
    "pt": frozenset({
        "clipa", "clipou", "cortem", "inacreditavel", "absurdo",
        "monstro", "caraca", "nossa", "insano", "lendario", "sinistro",
    }),
    "de": frozenset({
        "krass", "wahnsinn", "unglaublich", "geil", "clippen", "geclippt",
        "endlich", "heftig", "abartig",
    }),
    "fr": frozenset({
        "incroyable", "enorme", "clippe", "clipper", "dingue", "ouf",
        "magnifique",
    }),
    # Cyrillic cannot collide with English, so this list can be freer.
    "ru": frozenset({
        "клип", "клипай", "нарезка", "режь", "безумие", "невероятно",
        "жесть", "круто", "ору", "ахах", "топ", "имба", "красава",
    }),
    "it": frozenset({
        "assurdo", "pazzesco", "clippa", "incredibile", "mostro", "esagerato",
    }),
    "pl": frozenset({
        "klip", "klipnij", "szalenstwo", "niesamowite", "masakra", "mocne",
    }),
    "tr": frozenset({
        "kliple", "klipleyin", "inanılmaz", "inanilmaz",
        "efsane", "delirdim", "helal",
    }),
}

# ── Substring vocabulary ─────────────────────────────────────────────────────
# For scripts that do not delimit words. Matched with a single compiled
# alternation rather than set membership, because tokenising these produces one
# token per message. Every entry is written in a script that Latin chat does
# not use, so substring matching cannot fire on an English message.

_HYPE_SUBSTRING: frozenset[str] = frozenset({
    # Japanese — clip requests, then hype, then 草 (grass) which is how
    # Japanese chat writes laughter.
    "クリップ", "切り抜き", "きりぬき", "やばい", "ヤバい", "すごい", "スゴい",
    "神", "草", "ワロタ", "てぇてぇ", "強すぎ",
    # Chinese, simplified and traditional.
    "剪辑", "剪輯", "太强了", "太強了", "牛逼", "卧槽", "臥槽", "哈哈哈", "精彩",
    # Korean. Stems rather than whole words, because the endings inflect:
    # 미쳤다 / 미쳤어 / 미쳤네 all share 미쳤.
    "클립", "미쳤", "대박", "레전드", "지린", "개쩐", "쩐다",
    # Korean laughter, written in Hangul jamo and repeated to taste, so the
    # length varies and only a substring catches every form.
    "ㅋㅋ", "ㅎㅎ",
    # Brazilian laughter. Three is the floor — "kk" alone is how an English
    # speaker types "ok".
    "kkk",
})

# ── Clip requests ────────────────────────────────────────────────────────────
# THE HIGH-LEVERAGE LIST. Three distinct senders matching any of these inside
# the trip-wire window forces the score to CLIP_IT_FLOOR, which always clips.
# English was the only language that could reach it.

_CLIP_REQUEST_WORDBOUND: tuple[str, ...] = (
    # English
    r"clip\s*it", r"someone\s*clip", r"clip\s*that", r"clip\s*this",
    # Spanish — clipea / clipee / clipealo / clipeen all share the stem.
    r"clipe\w*", r"corta\s*eso",
    # Portuguese
    r"clipa", r"clipem", r"corta\s*isso", r"corta\s*ai",
    # German
    r"clip\s*das", r"geclippt", r"clippt\s*das",
    # French
    r"clippe\s*ca", r"faut\s*clipper", r"quelqu\w*\s*clippe",
    # Italian
    r"clippa\s*questo", r"taglia\s*questo",
    # Polish
    r"klipnij\s*to", r"tnij\s*to",
    # Turkish
    r"kliple\s*bunu", r"klip\s*at",
    # Russian — Cyrillic, still space-delimited so \b behaves.
    r"клипай", r"нарежь", r"на\s*клип", r"клип\s*плиз",
)

# Scripts without word boundaries: \b is meaningless against CJK, so these are
# searched as bare substrings.
_CLIP_REQUEST_SUBSTRING: tuple[str, ...] = (
    "クリップして", "切り抜いて", "きりぬいて", "クリップ頼む",   # Japanese
    "剪辑一下", "剪輯一下", "剪下来", "剪下來",                    # Chinese
    "클립 떠", "클립떠", "클립 좀", "클립좀",                      # Korean
)


# ── Matching ─────────────────────────────────────────────────────────────────

def _is_latin(ch: str) -> bool:
    try:
        return unicodedata.name(ch).startswith("LATIN")
    except ValueError:            # unnamed control/private-use character
        return False


def fold(text: str) -> str:
    """Lowercase, and drop LATIN diacritics only.

    Two jobs. It lets one entry cover both spellings of "increíble" /
    "increible" and "énorme" / "enorme", which chat uses interchangeably. And
    it repairs Turkish: "İ".lower() is "i" followed by a COMBINING DOT ABOVE,
    and `\\w+` does not treat that dot as a word character — so
    "İNANILMAZ".lower() tokenises as ["i", "nanilmaz"] and no Turkish keyword
    starting with İ could ever match.

    LATIN ONLY, AND THAT RESTRICTION IS THE WHOLE POINT. The first version of
    this stripped every combining mark, which quietly broke two of the
    languages it was added to support. In Cyrillic, "й" decomposes to "и" plus
    a combining breve, so "клипай" became "клипаи" and stopped matching. In
    Japanese, "プ" decomposes to "フ" plus a combining handakuten, so
    "クリップ" became "クリッフ" — the voiced-sound marks are part of the
    letter, not decoration on it. Both were caught by running the matcher
    against real sentences rather than by reading it.

    NFD then NFC rather than NFKD: compatibility folding would also rewrite
    half-width katakana and circled characters, which are ordinary text in
    Japanese chat rather than noise to be normalised away.
    """
    out, base_is_latin = [], False
    for ch in unicodedata.normalize("NFD", text.lower()):
        if unicodedata.category(ch) == "Mn":
            if not base_is_latin:
                out.append(ch)        # part of the letter in this script
            continue
        base_is_latin = _is_latin(ch)
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def _compile_alternation(terms) -> re.Pattern:
    return re.compile("|".join(sorted((re.escape(t) for t in terms),
                                      key=len, reverse=True)))


# BOTH SIDES GO THROUGH fold(). Messages are folded before matching, so a list
# that is not folded the same way cannot match one that is — an entry written
# "increíble" would be looking for a spelling the matcher never produces. Doing
# it here means the lists above can be written the way the language is actually
# spelled.
HIGH_ENERGY_KEYWORDS: frozenset[str] = frozenset(
    fold(w) for w in frozenset().union(*_HYPE_BY_LANG.values()))

# One pass for every space-less term, rather than a loop over the set.
HYPE_SUBSTRING_RE: re.Pattern = _compile_alternation(fold(t) for t in _HYPE_SUBSTRING)

# Word-bounded alternatives first, then the bare-substring ones. Both halves in
# a single pattern so a message is scanned once.
CLIP_TRIGGER_PHRASES: re.Pattern = re.compile(
    r"\b(?:" + "|".join(fold(p) for p in _CLIP_REQUEST_WORDBOUND) + r")\b"
    + "|" + "|".join(re.escape(fold(t)) for t in _CLIP_REQUEST_SUBSTRING),
    re.IGNORECASE,
)

LANGUAGES: tuple[str, ...] = tuple(sorted(_HYPE_BY_LANG))


def keyword_hit(folded: str, tokens: set[str], keywords: frozenset[str]) -> bool:
    """Does this message contain hype vocabulary in any supported language?

    Set membership first: it is the common case, it is O(1) per token, and it
    covers every space-delimited language. The substring pass only runs when
    that misses, so an English message costs one set intersection and nothing
    more — the scan exists for scripts that cannot be tokenised, and it should
    not be charged to chat that can.
    """
    if tokens & keywords:
        return True
    return bool(HYPE_SUBSTRING_RE.search(folded))
