"""Target languages: code -> (English name, native label, per-language style rule)."""

LANGS = {
    "zh-Hans": ("Simplified Chinese", "简体中文"),
    "zh-Hant": ("Traditional Chinese", "繁體中文"),
    "en": ("English", "English"),
    "ja": ("Japanese", "日本語"),
    "ko": ("Korean", "한국어"),
    "yue": ("Cantonese", "粵語"),
    "fr": ("French", "Français"),
    "de": ("German", "Deutsch"),
    "es": ("Spanish", "Español"),
    "pt": ("Portuguese", "Português"),
    "pt-BR": ("Brazilian Portuguese", "Português (Brasil)"),
    "it": ("Italian", "Italiano"),
    "ru": ("Russian", "Русский"),
    "uk": ("Ukrainian", "Українська"),
    "pl": ("Polish", "Polski"),
    "nl": ("Dutch", "Nederlands"),
    "tr": ("Turkish", "Türkçe"),
    "ar": ("Arabic", "العربية"),
    "th": ("Thai", "ไทย"),
    "vi": ("Vietnamese", "Tiếng Việt"),
    "id": ("Indonesian", "Bahasa Indonesia"),
    "ms": ("Malay", "Bahasa Melayu"),
    "hi": ("Hindi", "हिन्दी"),
}

STYLE = {
    "zh-Hans": "Use Simplified Chinese with mainland-China wording. Write Japanese kanji names and titles "
               "in Simplified characters (宮→宫, 機→机, 長→长).",
    "zh-Hant": "Use Traditional Chinese with Taiwan wording.",
    "en": "Romanize Japanese names in Hepburn (e.g. Miyashita); keep honorifics only when they matter.",
}

# languages whose subtitles are measured in characters rather than words
CJK = {"zh-Hans", "zh-Hant", "ja", "ko", "yue"}


def name(code: str) -> str:
    return LANGS.get(code, (code,))[0]


def label(code: str) -> str:
    return LANGS.get(code, (code, code))[1]


def style(code: str) -> str:
    return STYLE.get(code, "")
