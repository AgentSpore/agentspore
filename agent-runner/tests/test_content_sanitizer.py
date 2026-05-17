"""Tests for content_sanitizer module."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from content_sanitizer import (
    _BIDI_CHARS,
    normalize_mirrored,
    risk_score,
    sanitize_for_agent_context,
    strip_bidi_overrides,
)


class TestStripBidiOverrides:
    def test_removes_lre(self):
        # U+202A LEFT-TO-RIGHT EMBEDDING
        text = "hello‪world"
        assert "‪" not in strip_bidi_overrides(text)
        assert "helloworld" == strip_bidi_overrides(text)

    def test_removes_rle(self):
        # U+202B RIGHT-TO-LEFT EMBEDDING
        text = "foo‫bar"
        assert "foobar" == strip_bidi_overrides(text)

    def test_removes_rlo(self):
        # U+202E RIGHT-TO-LEFT OVERRIDE (classic text-reversal trick)
        text = "click ‮cilk etis.goy//:sptth‬"
        result = strip_bidi_overrides(text)
        assert "‮" not in result
        assert "‬" not in result

    def test_removes_lrm_rlm(self):
        text = "‎hello‏"
        assert "hello" == strip_bidi_overrides(text)

    def test_removes_alm(self):
        # U+061C ARABIC LETTER MARK
        text = "data؜value"
        assert "datavalue" == strip_bidi_overrides(text)

    def test_removes_fsi(self):
        # U+2066 LEFT-TO-RIGHT ISOLATE
        text = "test⁦inject"
        assert "testinject" == strip_bidi_overrides(text)

    def test_plain_ascii_unchanged(self):
        text = "hello world 123"
        assert text == strip_bidi_overrides(text)

    def test_empty_string(self):
        assert "" == strip_bidi_overrides("")

    def test_all_bidi_chars_stripped(self):
        bidi_text = "".join(chr(c) for c in _BIDI_CHARS)
        assert "" == strip_bidi_overrides(bidi_text)


class TestNormalizeMirrored:
    def test_capital_e_mirror(self):
        assert "E" == normalize_mirrored("Ǝ")

    def test_mixed_mirror_and_ascii(self):
        # "Ǝ9%A8" → "E9%A8"
        assert "E9%A8" == normalize_mirrored("Ǝ9%A8")

    def test_r_mirror(self):
        assert "R" == normalize_mirrored("Я")

    def test_c_mirror_oc(self):
        assert "c" == normalize_mirrored("ɔ")

    def test_c_mirror_semicolon(self):
        assert "c" == normalize_mirrored("ͻ")

    def test_s_mirror(self):
        assert "s" == normalize_mirrored("ƨ")

    def test_t_mirror(self):
        assert "t" == normalize_mirrored("ʇ")

    def test_no_mirror_chars_unchanged(self):
        assert "hello" == normalize_mirrored("hello")

    def test_empty_string(self):
        assert "" == normalize_mirrored("")

    def test_mixed_mirror_sequence(self):
        # Simulates attack: "gyo.tc" written with mirrored chars
        # ɔ → c, ʇ → t (partial, just validate mapping chain)
        result = normalize_mirrored("ɔ.ʇ")
        assert result == "c.t"


class TestSanitizeForAgentContext:
    def test_truncates_to_max_len(self):
        text = "a" * 3000
        result = sanitize_for_agent_context(text, max_len=2000)
        assert len(result) == 2000

    def test_default_max_len_2000(self):
        text = "x" * 5000
        result = sanitize_for_agent_context(text)
        assert len(result) == 2000

    def test_strips_bidi_and_truncates(self):
        text = "‮" + "a" * 2001
        result = sanitize_for_agent_context(text, max_len=2000)
        assert "‮" not in result
        assert len(result) == 2000

    def test_normalizes_mirror_chars(self):
        text = "ǝntrY"  # 'ǝ' is not in our map but 'Ǝ' is; use known mapped
        text = "ǢBĆ"  # use chars NOT in our map → unchanged
        result = sanitize_for_agent_context("Ǝ hello", max_len=100)
        assert result.startswith("E hello")

    def test_short_text_unchanged(self):
        text = "plain safe text"
        assert text == sanitize_for_agent_context(text)

    def test_empty_string(self):
        assert "" == sanitize_for_agent_context("")

    def test_custom_max_len(self):
        result = sanitize_for_agent_context("hello world", max_len=5)
        assert result == "hello"


class TestRiskScore:
    def test_bidi_char_scores_high(self):
        text = "click ‮here"
        assert risk_score(text) > 50

    def test_plain_text_low_score(self):
        assert risk_score("hello world") == 0

    def test_heavy_non_ascii_scores_high(self):
        # >20% non-ascii (code points > 0x2FF) in text longer than 20 chars
        text = "аБвГдЕёЖзИйКлМнОпрСтУфХцЧшЩъыЬэЮяabc"
        score = risk_score(text)
        assert score > 0

    def test_max_score_capped_at_100(self):
        # bidi + heavy non-ascii = 60 + 30 = 90, capped at 100
        bidi = "‮" * 5
        non_ascii = "аБвГдЕёЖзИйКлМнОпрСтУфХцЧшЩъыЬэЮя" * 2
        text = bidi + non_ascii
        assert risk_score(text) <= 100

    def test_score_zero_for_empty(self):
        assert risk_score("") == 0

    def test_short_non_ascii_text_no_ratio_penalty(self):
        # len <= 20 chars: ratio check is skipped
        text = "αβγ"
        # no bidi, len <= 20 → score 0
        assert risk_score(text) == 0
