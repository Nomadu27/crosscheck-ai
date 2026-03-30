"""
crosscheck.team.language
------------------------
Auto-detect user's language and generate system prompt instructions.

Direct port from Android AI Bridge's LanguageDetector.kt.
Uses Unicode character patterns to detect 20+ languages.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Language detection patterns — ported 1:1 from LanguageDetector.kt
# Order matters: more specific patterns first (Romanian before French, etc.)
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Spanish",     re.compile(r"[áéíóúñ¿¡]", re.IGNORECASE)),
    ("Italian",     re.compile(r"[àèìòù]", re.IGNORECASE)),
    ("Romanian",    re.compile(r"[ăâîșț]", re.IGNORECASE)),
    ("French",      re.compile(r"[àâäéèêëîïôöûüç]", re.IGNORECASE)),
    ("German",      re.compile(r"[äöüß]", re.IGNORECASE)),
    ("Portuguese",  re.compile(r"[ãõ]", re.IGNORECASE)),
    ("Swedish",     re.compile(r"[åäö]", re.IGNORECASE)),
    ("Polish",      re.compile(r"[ąćęłńóśźż]", re.IGNORECASE)),
    ("Czech",       re.compile(r"[čďěňřšťůž]", re.IGNORECASE)),
    ("Arabic",      re.compile(r"[\u0600-\u06FF]")),
    ("Chinese",     re.compile(r"[\u4E00-\u9FFF]")),
    ("Japanese",    re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")),
    ("Korean",      re.compile(r"[\uAC00-\uD7AF]")),
    ("Russian",     re.compile(r"[\u0400-\u04FF]")),
    ("Greek",       re.compile(r"[\u0370-\u03FF]")),
    ("Hebrew",      re.compile(r"[\u0590-\u05FF]")),
    ("Thai",        re.compile(r"[\u0E00-\u0E7F]")),
    ("Hindi",       re.compile(r"[\u0900-\u097F]")),
    ("Vietnamese",  re.compile(r"[ơưđ]", re.IGNORECASE)),
]


class LanguageDetector:
    """Detect language from text using character patterns."""

    @staticmethod
    def detect(text: str) -> str:
        """Detect the primary language of the input text.

        Returns language name (e.g. "English", "Romanian", "Chinese").
        Defaults to "English" if no specific patterns match.
        """
        if not text or not text.strip():
            return "English"

        for language, pattern in _PATTERNS:
            if pattern.search(text):
                return language

        return "English"

    @staticmethod
    def get_instruction(language: str) -> str:
        """Get system prompt instruction for responding in the detected language.

        Ported from Android LanguageDetector.getLanguageInstruction().
        """
        if language == "English":
            return ""

        return (
            f"You are speaking to a user who asked their question in {language}. "
            f"You MUST respond 100% in {language} — natural, fluent, native-level {language}. "
            f"Never mix in English unless the user explicitly requests it. "
            f"If the user writes in {language}, your ENTIRE response must be in {language}."
        )
