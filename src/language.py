"""The account's language: what captions are transcribed in, and what the
titles burned onto its edits are written in.

Owner, 2026-10-21: "Do the language to per-account setting. Change the hooks
to match the account language setting." From luclips' ticket: a Pro account
clipping Spanish-speaking Kick streamers got English subtitles (the caption
model was English-only, base.en, so Spanish audio came out as English) and an
English title on every Autopilot video (the detector's own labels, "westcol —
Hype Moment").

ONE SETTING DECIDES BOTH. It lives in the account's prefs (`prefs.language`),
so PUT /prefs, the prefs_changed broadcast and /me carry it with no new
plumbing. Default English: every existing account keeps exactly what it had.

  * Captions — src/captions/transcribe.py transcribes in this language.
    English keeps base.en; any other uses the multilingual model with the
    language forced, never guessed (a guess on loud game audio is a coin flip).
  * Hook titles — `localize_title` rewrites the detector's generated titles.
    A clip is titled in its account's language when it is caught (the stream
    worker); a clip caught before the switch is rewritten where its title
    leaves the product: the burned-in title and the post caption.
  * The AI edit (LLM_PROVIDER) is told the language for its title and text.

The codes are Whisper's (ISO 639-1). Adding a language is a row in LANGUAGES
and _NAMES and a column in _LABELS and _CLIP_FROM; tests/test_account_language.py
fails until every label has it.
"""

from __future__ import annotations

LANGUAGES = {
    "en": "English",
    "es": "Español",
    "pt": "Português",
    "fr": "Français",
    "de": "Deutsch",
    "it": "Italiano",
}
DEFAULT = "en"

# How the AI editor's brief names each one. In English, because the brief is.
_NAMES = {"en": "English", "es": "Spanish", "pt": "Portuguese",
          "fr": "French", "de": "German", "it": "Italian"}


def normalize(code) -> str:
    c = str(code or "").strip().lower()
    return c if c in LANGUAGES else DEFAULT


def of(user: dict | None) -> str:
    """The language on this account; English when unset."""
    return normalize(((user or {}).get("prefs") or {}).get("language"))


def for_user_id(user_id: str) -> str:
    from src.auth import users as user_store
    return of(user_store.get_by_id(user_id) if user_id else None)


def english_name(code: str) -> str:
    return _NAMES[normalize(code)]


def choices() -> list[list[str]]:
    """[code, name in that language] for the pickers, in display order."""
    return [[k, v] for k, v in LANGUAGES.items()]


# ── hook titles ──────────────────────────────────────────────────────────────
# The detector titles a clip "{channel} — {label}" or "Clip from {channel}"
# (src/trigger/engine.py, _generate_clip_title). These are its labels in each
# language, held to the same rule as the English: say what was measured, and
# no more.
_LABELS = {
    "Chat Erupts": {
        "es": "El chat explota", "pt": "O chat explode", "fr": "Le chat explose",
        "de": "Der Chat explodiert", "it": "La chat esplode"},
    "Loud Reaction": {
        "es": "Reacción a gritos", "pt": "Reação aos gritos", "fr": "Réaction bruyante",
        "de": "Laute Reaktion", "it": "Reazione urlata"},
    "Chat Calls For The Clip": {
        "es": "El chat pide el clip", "pt": "O chat pede o clipe", "fr": "Le chat réclame le clip",
        "de": "Der Chat will den Clip", "it": "La chat chiede la clip"},
    "Viewers Flood In": {
        "es": "Llegan espectadores en masa", "pt": "Espectadores chegam em massa",
        "fr": "Les spectateurs affluent", "de": "Zuschauer strömen herein",
        "it": "Arrivano spettatori in massa"},
    "Emotions Run High": {
        "es": "Emociones a flor de piel", "pt": "Emoções à flor da pele",
        "fr": "L'émotion est à son comble", "de": "Die Emotionen kochen hoch",
        "it": "Emozioni alle stelle"},
    "Silence, Then Chaos": {
        "es": "Silencio, y luego el caos", "pt": "Silêncio, depois o caos",
        "fr": "Le silence, puis le chaos", "de": "Erst Stille, dann Chaos",
        "it": "Silenzio, poi il caos"},
    "Chat Speaks As One": {
        "es": "El chat al unísono", "pt": "O chat em uníssono", "fr": "Le chat à l'unisson",
        "de": "Der Chat wie aus einem Mund", "it": "La chat all'unisono"},
    "Highlight": {
        "es": "Momento destacado", "pt": "Momento de destaque", "fr": "Moment fort",
        "de": "Highlight", "it": "Momento clou"},
    "Sub/Raid Hype": {
        "es": "Hype de subs/raid", "pt": "Hype de subs/raid", "fr": "Hype subs/raid",
        "de": "Sub/Raid-Hype", "it": "Hype da sub/raid"},
    "Everything Pops Off At Once": {
        "es": "Todo explota a la vez", "pt": "Tudo explode ao mesmo tempo",
        "fr": "Tout explose en même temps", "de": "Alles eskaliert gleichzeitig",
        "it": "Esplode tutto insieme"},
    "Hype Moment": {
        "es": "Momento de hype", "pt": "Momento de hype", "fr": "Moment de hype",
        "de": "Hype-Moment", "it": "Momento di hype"},
}
_CLIP_FROM = {"en": "Clip from {channel}", "es": "Clip de {channel}", "pt": "Clipe de {channel}",
              "fr": "Clip de {channel}", "de": "Clip von {channel}", "it": "Clip di {channel}"}
_SEP = " — "


def localize_title(title: str, channel: str, language: str) -> str:
    """A detector title in `language`; every other title unchanged.

    Only the detector's own English shapes are recognised, so a title somebody
    typed, a Twitch clip's or VOD scan's title, or one already translated
    passes through as it is. The channel must match too, when it is known."""
    lang = normalize(language)
    if lang == "en" or not title:
        return title
    ch = (channel or "").lower()
    if title.startswith("Clip from "):
        name = title[len("Clip from "):]
        if not ch or name.lower() == ch:
            return _CLIP_FROM[lang].format(channel=name)
        return title
    head, sep, label = title.partition(_SEP)
    if sep and (not ch or head.lower() == ch):
        out = (_LABELS.get(label) or {}).get(lang)
        if out:
            return head + _SEP + out
    return title
