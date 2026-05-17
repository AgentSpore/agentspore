"""Prompt injection detection for incoming chat messages.

Detects Unicode-based obfuscation techniques used to hide malicious URLs and
instructions from human reviewers while making them readable to LLM agents:
  - Bidirectional override characters (U+202A-U+202E, U+2066-U+2069, etc.)
  - Mixed-script mirroring (reversed Latin look-alikes from Canadian syllabics,
    Mathematical Alphanumeric Symbols, Georgian, etc.)
  - Known redirect / paste domains (gyo.tc, justpaste.it, thecolony.cc, etc.)
"""

import re
import unicodedata
from urllib.parse import unquote

# Bidirectional override characters — used to hide URLs by reversing text direction
# RLO/LRO/RLE/LRE/PDF/RLI/LRI/FSI/PDI and strong directional marks
_BIDI_OVERRIDES: frozenset[int] = (
    frozenset(range(0x202A, 0x202F))  # U+202A..U+202E: LRE LRO RLE RLO PDF
    | frozenset(range(0x2066, 0x206A))  # U+2066..U+2069: LRI RLI FSI PDI
    | {0x200E, 0x200F, 0x061C}  # LRM, RLM, ALM
)

# Unicode blocks used for text mirroring in prompt injection (reversed Latin look-alikes)
# Mathematical Fraktur, Canadian syllabics used as mirrors, Modifier letters, etc.
_SUSPICIOUS_BLOCKS: list[tuple[int, int]] = [
    (0x1D400, 0x1D7FF),  # Mathematical Alphanumeric Symbols (𝐀-𝟿)
    (0xA640, 0xA69F),  # Cyrillic Supplement (includes ꟻ U+A67B etc.)
    (0x2C60, 0x2C7F),  # Latin Extended-C (Ⱡⱡ etc.)
]

_REDIRECT_DOMAINS: frozenset[str] = frozenset([
    "gyo.tc",
    "bit.ly",
    "tinyurl.com",
    "t.co",
    "ow.ly",
    "rb.gy",
    "justpaste.it",
    "paste.ee",
    "pastebin.com",
    "rentry.co",
    "thecolony.cc",
    "skillmd.ai",
])


def has_bidi_override(text: str) -> bool:
    """True if text contains Unicode bidirectional override characters.

    Args:
        text: Input string to inspect.

    Returns:
        True if any bidi override / directional mark codepoint is present.
    """
    return any(ord(c) in _BIDI_OVERRIDES for c in text)


def has_suspicious_unicode_mix(text: str) -> bool:
    """True if text mixes multiple non-Latin Unicode scripts (mirroring obfuscation).

    Counts distinct script families present. Three or more families in a single
    message is a strong signal of glyph-substitution obfuscation.

    Args:
        text: Input string to inspect.

    Returns:
        True when three or more distinct non-Latin script families are detected.
    """
    if len(text) < 20:
        return False

    scripts: set[str] = set()
    for ch in text:
        cp = ord(ch)
        if cp > 0x7F:
            cat = unicodedata.category(ch)
            name = unicodedata.name(ch, "")
            if "CANADIAN" in name or "MODIFIER LETTER" in name:
                scripts.add("CANADIAN")
            elif "MATHEMATICAL" in name:
                scripts.add("MATH")
            elif "GEORGIAN" in name:
                scripts.add("GEORGIAN")
            elif cat.startswith("L") and cp > 0x0500:
                scripts.add(unicodedata.name(ch, "UNK")[:10])

    return len(scripts) >= 3


def has_encoded_redirect(text: str) -> bool:
    """True if text contains a known redirect/paste domain in plain or %-encoded form.

    Checks both the raw text and the URL-decoded version so that obfuscations
    like ``gyo%2Etc`` are caught.

    Args:
        text: Input string to inspect.

    Returns:
        True when any known redirect / paste domain is found.
    """
    lower = text.lower()

    for domain in _REDIRECT_DOMAINS:
        if domain in lower:
            return True

    decoded = unquote(lower)
    for domain in _REDIRECT_DOMAINS:
        if domain in decoded:
            return True

    return False


def score_message_risk(text: str) -> int:
    """Return risk score 0–100 for prompt injection likelihood.

    Scoring breakdown:
    - BiDi override characters: +60 (very high signal, rarely legitimate)
    - Suspicious Unicode script mix: +40 (strong signal)
    - Known redirect/paste domain: +30 (moderate, may be legitimate link sharing)
    - High non-ASCII density (>25% in messages >50 chars): +20

    Args:
        text: Input string to score.

    Returns:
        Integer score in range [0, 100]. Values ≥ 50 are considered suspicious.
    """
    score = 0

    if has_bidi_override(text):
        score += 60

    if has_suspicious_unicode_mix(text):
        score += 40

    if has_encoded_redirect(text):
        score += 30

    if len(text) > 50:
        non_ascii = sum(1 for c in text if ord(c) > 0x2FF)
        if non_ascii / len(text) > 0.25:
            score += 20

    return min(score, 100)


def is_likely_prompt_injection(text: str, threshold: int = 50) -> bool:
    """Return True if the message risk score meets or exceeds the threshold.

    Args:
        text: Input string to evaluate.
        threshold: Risk score threshold (default 50). Lower = more sensitive.

    Returns:
        True when ``score_message_risk(text) >= threshold``.
    """
    return score_message_risk(text) >= threshold
