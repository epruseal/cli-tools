"""Tests for :func:`legalize_cli.util.article_id.article_id`."""

from __future__ import annotations

import pytest
import regex

from legalize_cli.laws.articles import parse_articles
from legalize_cli.laws.model import ArticleNo
from legalize_cli.util import article_id as article_id_module
from legalize_cli.util.article_id import article_id
from legalize_cli.util.article_parse import parse_article_query


def _id(query: str) -> str:
    return article_id(parse_article_query(query))


def test_issue_examples() -> None:
    assert _id("제23조") == "art_23"
    assert _id("제23조의2") == "art_23-2"


def test_query_spellings_converge() -> None:
    for query in ("제839조의2", "839조의2", "839-2", "제839-2", "제 839 조 의 2"):
        assert _id(query) == "art_839-2", query


def test_heading_and_query_converge() -> None:
    body = "##### 제839조의2 (재산분할청구권)\n\n본문\n"
    (article,) = parse_articles(body)
    assert article_id(article.article_no) == _id("839-2")


def test_fullwidth_and_leading_zero_normalize() -> None:
    assert _id("８３９조의２") == "art_839-2"
    assert _id("0839조의02") == "art_839-2"
    (article,) = parse_articles("##### 제０１조 (목적)\n\n본문\n")
    assert article_id(article.article_no) == "art_1"


def test_ascii_without_leading_zero_keeps_digits() -> None:
    for jo, ui in (("1", None), ("23", None), ("839", "2"), ("1118", None), ("10", "10")):
        expected = f"art_{jo}" + (f"-{ui}" if ui else "")
        assert article_id(ArticleNo.model_validate({"조": jo, "의": ui})) == expected


def test_zero_values_are_kept() -> None:
    assert _id("제0조") == "art_0"
    assert _id("839조의0") == "art_839-0"
    assert _id("839") != _id("839조의0")


def test_empty_ui_means_no_branch() -> None:
    assert article_id(ArticleNo.model_validate({"조": "839", "의": ""})) == "art_839"


def test_digit_unknown_to_int_does_not_raise() -> None:
    char = chr(0x10D41)  # GARAY DIGIT ONE (Unicode 16.0)
    if not regex.fullmatch(r"\d", char):
        pytest.skip("installed regex does not treat U+10D41 as a digit")
    query_id = _id(char)
    (article,) = parse_articles(f"##### 제{char}조 (목적)\n\n본문\n")
    assert article_id(article.article_no) == query_id
    assert query_id in ("art_1", f"art_{char}")


def test_int_failure_falls_back_to_source_text(monkeypatch) -> None:
    def failing_int(value, *args):
        raise ValueError("forced")

    monkeypatch.setattr(article_id_module, "int", failing_int, raising=False)
    assert article_id(ArticleNo.model_validate({"조": "0839", "의": "02"})) == "art_0839-02"


@pytest.mark.parametrize("jo, ui", [("-1", None), ("839", " 2"), ("8_39", None), ("abc", None)])
def test_non_digit_values_are_rejected(jo: str, ui: str | None) -> None:
    with pytest.raises(ValueError):
        article_id(ArticleNo.model_validate({"조": jo, "의": ui}))


def test_paragraph_level_is_rejected() -> None:
    with pytest.raises(ValueError):
        article_id(ArticleNo.model_validate({"조": "839", "항": "1"}))
    with pytest.raises(ValueError):
        article_id(ArticleNo.model_validate({"조": "839", "호": "1"}))
