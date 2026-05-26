"""Unit tests for _restore_markdown_newlines in blog_service.

Imports the function via sys.modules patching so that the heavy app.core.database
import chain (which calls get_settings() at module level and reads .env) is bypassed.
The function itself has no dependencies — this isolation is valid.
"""

import re
import sys
import types
from unittest.mock import MagicMock

import pytest


def _load_restore_fn():
    """Import _restore_markdown_newlines without triggering app bootstrap."""
    # Stub out modules that load settings at import time.
    for mod in [
        "app.core.config",
        "app.core.database",
        "app.repositories.blog_repo",
    ]:
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

    # Ensure fastapi.Depends is present (light dep, usually installed).
    import importlib

    if "app.services.blog_service" in sys.modules:
        del sys.modules["app.services.blog_service"]

    blog_svc = importlib.import_module("app.services.blog_service")
    return blog_svc._restore_markdown_newlines


_restore_markdown_newlines = _load_restore_fn()


def test_restore_markdown_newlines_flat() -> None:
    flat = (
        "# Title*Date: 2026-05-23*Some text."
        "## Section headingMore text.- bullet one- bullet two"
    )
    result = _restore_markdown_newlines(flat)
    assert result.count("\n") >= 5
    assert "# Title" in result
    assert "\n\n## Section" in result


def test_restore_markdown_newlines_already_formatted() -> None:
    good = "# Title\n\nParagraph.\n\n## Section\n\nMore."
    result = _restore_markdown_newlines(good)
    assert result == good


def test_restore_markdown_newlines_numbered_list() -> None:
    flat = "Intro.1. First item2. Second item3. Third item"
    result = _restore_markdown_newlines(flat)
    assert "\n\n1." in result
    assert "\n\n2." in result


def test_restore_markdown_newlines_bold_section_headers() -> None:
    flat = "Intro.**Summary:** some text.**Details:** more text."
    result = _restore_markdown_newlines(flat)
    assert "\n\n**Summary:**" in result
    assert "\n\n**Details:**" in result


def test_restore_markdown_newlines_strips_excess_blank_lines() -> None:
    text = "# A\n\n\n\n## B"
    result = _restore_markdown_newlines(text)
    assert "\n\n\n" not in result
