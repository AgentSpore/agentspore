"""Sanitize user-supplied text before injecting into agent LLM context.

Defenses implemented:
1. Strip Unicode bidirectional override characters (used for visual spoofing).
2. Normalize common mirror/confusable Unicode chars to ASCII equivalents
   (used in the gyo.tc/thecolony.cc prompt-injection attack).
3. Truncate to a configurable max length.
"""

import re
import unicodedata

# Bidirectional override characters — strip before injecting into agent context.
# Ranges: U+202A–U+202E (LRE/RLE/PDF/LRO/RLO), U+2066–U+2069 (FSI/LRI/RLI/PDI),
# U+200E (LRM), U+200F (RLM), U+061C (ALM).
_BIDI_CHARS: frozenset[int] = (
    frozenset(range(0x202A, 0x202F))
    | frozenset(range(0x2066, 0x206A))
    | {0x200E, 0x200F, 0x061C}
)

# Map of common "mirrored" Unicode characters used for text-reversal obfuscation
# → ASCII equivalent. Covers characters seen in the actual gyo.tc attack.
_MIRROR_MAP: dict[str, str] = {
    "Ǝ": "E", "ɘ": "e", "ᗡ": "D", "ᗺ": "B", "ꟻ": "F",
    "Я": "R", "ͻ": "c", "ɔ": "c", "ƨ": "s", "ʇ": "t",
    "ɟ": "f", "ƃ": "g", "ʌ": "v", "ʍ": "w", "ʞ": "k",
    "ɯ": "m", "ɹ": "r", "ı": "i", "ʎ": "y", "ɐ": "a",
    "ƍ": "d", "ʃ": "l", "Ɩ": "l",
    # Mathematical/special chars that appear in the attack
    "ꓨ": "Y", "Ⓞ": "O",
}


def strip_bidi_overrides(text: str) -> str:
    """Remove Unicode bidirectional override characters from text."""
    return "".join(ch for ch in text if ord(ch) not in _BIDI_CHARS)


def normalize_mirrored(text: str) -> str:
    """Replace known mirror/confusable Unicode chars with ASCII equivalents."""
    return "".join(_MIRROR_MAP.get(ch, ch) for ch in text)


def sanitize_for_agent_context(text: str, max_len: int = 2000) -> str:
    """Sanitize user-supplied text before injecting into agent LLM context.

    Steps:
    1. Strip bidirectional override characters.
    2. Normalize common mirror/confusable chars to ASCII.
    3. Truncate to max_len.

    Args:
        text: Raw user-supplied string from platform event or heartbeat context.
        max_len: Maximum allowed length after sanitization (default 2000).

    Returns:
        Sanitized string safe for inclusion in a SystemPromptPart.
    """
    text = strip_bidi_overrides(text)
    text = normalize_mirrored(text)
    return text[:max_len]


def risk_score(text: str) -> int:
    """Quick heuristic risk score 0–100 for logging purposes.

    Not a security gate — used only for warning logs to surface suspicious
    content for investigation.

    Scoring:
    - +60 if any bidirectional override char is present.
    - +30 if >20% of characters (in texts longer than 20 chars) are non-ASCII
      beyond the basic Latin supplement (code point > U+02FF), indicating heavy
      use of Unicode confusables.
    """
    score = 0
    if any(ord(c) in _BIDI_CHARS for c in text):
        score += 60
    if len(text) > 20:
        non_ascii = sum(1 for c in text if ord(c) > 0x2FF)
        if non_ascii / len(text) > 0.2:
            score += 30
    return min(score, 100)
