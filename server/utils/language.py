"""Language helpers for preserving the user's request language."""

from __future__ import annotations

import re

_HAN_RE = re.compile(r"[\u4e00-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
_ARABIC_RE = re.compile(r"[\u0600-\u06ff]")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097f]")
_THAI_RE = re.compile(r"[\u0e00-\u0e7f]")
_LATIN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")

_SPANISH_RE = re.compile(
    r"[¿¡ñáéíóúü]|\b(el|la|los|las|un|una|que|por|para|con|como|necesito|quiero)\b",
    re.IGNORECASE,
)
_FRENCH_RE = re.compile(
    r"[àâçéèêëîïôûùüÿœ]|\b(le|la|les|des|une|pour|avec|dans|est|sont|bonjour)\b",
    re.IGNORECASE,
)
_GERMAN_RE = re.compile(r"[äöüß]|\b(der|die|das|und|ich|nicht|bitte)\b", re.IGNORECASE)
_PORTUGUESE_RE = re.compile(
    r"[ãõáéíóúâêôç]|\b(que|para|com|uma|preciso|quero|você|não)\b",
    re.IGNORECASE,
)


def detect_language_name(text: str) -> str:
    """Return a practical language name for prompt-level output control."""
    value = (text or "").strip()
    if not value:
        return "the same language as the user's request"

    if _HANGUL_RE.search(value):
        return "Korean"
    if _KANA_RE.search(value):
        return "Japanese"
    if _HAN_RE.search(value):
        return "Chinese"
    if _CYRILLIC_RE.search(value):
        return "Russian"
    if _ARABIC_RE.search(value):
        return "Arabic"
    if _DEVANAGARI_RE.search(value):
        return "Hindi"
    if _THAI_RE.search(value):
        return "Thai"
    if _LATIN_RE.search(value):
        if _GERMAN_RE.search(value):
            return "German"
        if _FRENCH_RE.search(value):
            return "French"
        if _PORTUGUESE_RE.search(value):
            return "Portuguese"
        if _SPANISH_RE.search(value):
            return "Spanish"
        return "English"
    return "the same language as the user's request"


def response_language_instruction(source_text: str, *, subject: str = "all user-facing content") -> str:
    """Build a reusable prompt clause that keeps task output in the user's language."""
    language = detect_language_name(source_text)
    return (
        "## Response Language Policy\n"
        f"The user's primary request language is: **{language}**.\n"
        f"Write {subject} in **{language}**. This includes plans, clarification questions, "
        "employee work orders, deliverables, blockers, annotation answers, result-chat replies, "
        "and final synthesis.\n"
        "Keep tool names, code identifiers, file paths, commands, JSON keys, URLs, and quoted source "
        "material in their original language when needed."
    )


def is_chinese(text: str) -> bool:
    return detect_language_name(text) == "Chinese"
