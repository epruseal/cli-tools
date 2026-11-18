"""Build the canonical article ID for an :class:`ArticleNo`.

Rule:

- ``제23조`` -> ``art_23``
- ``제23조의2`` -> ``art_23-2``

The 조 and 의 numbers are normalized to ASCII decimal with ``int()``, so
``제０２３조의２``, ``023조의02``, ``23-2`` and ``제23조의2`` all give ``art_23-2``.
Zero is kept: ``제0조`` -> ``art_0``, ``839조의0`` -> ``art_839-0``.

Exception: the parsers accept every digit that the ``regex`` module knows.
Python's ``int()`` uses the Unicode table of the interpreter, which can be
older. When ``int()`` rejects a value (an unknown digit or a value over the
integer string length limit), the source text is kept as it is. Such an ID
can contain non-ASCII digits, and a newer Python can normalize the same
digit. A query and a heading still get the same ID on the same Python.

The ID covers the article level only. An ``ArticleNo`` with 항 or 호 is
rejected.
"""

from __future__ import annotations

from typing import Optional

import regex as re

from ..laws.model import ArticleNo

_DIGITS_RE = re.compile(r"\d+")


def _number(value: str) -> str:
    if not _DIGITS_RE.fullmatch(value):
        raise ValueError(f"article number is not a decimal number: {value!r}")
    try:
        return str(int(value))
    except ValueError:
        return value


def article_id(article_no: ArticleNo) -> str:
    """Return the article ID (``art_{조}`` or ``art_{조}-{의}``).

    :raises ValueError: 조 or 의 is not a decimal number, or 항 or 호 is set.
    """
    if article_no.hang is not None or article_no.ho is not None:
        raise ValueError("article ID covers the article level only")
    ui: Optional[str] = article_no.ui or None
    base = f"art_{_number(article_no.jo)}"
    return base if ui is None else f"{base}-{_number(ui)}"


__all__ = ["article_id"]
