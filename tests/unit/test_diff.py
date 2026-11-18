"""Unit tests for :mod:`legalize_cli.laws.diff`."""

from __future__ import annotations

from legalize_cli.laws.diff import (
    RENAME_SIMILARITY_THRESHOLD,
    diff_laws,
)
from legalize_cli.laws.model import Article, ArticleNo


def _a(jo: str, ui: str | None, content: str) -> Article:
    return Article(
        article_no=ArticleNo.model_validate({"조": jo, "의": ui}),
        heading_level=5,
        heading_text=f"##### 제{jo}조" + (f"의{ui}" if ui else ""),
        content=content,
        annotations=[],
        status="active",
        parent_structure=[],
    )


def test_added_and_removed() -> None:
    a = [_a("1", None, "AAA")]
    b = [_a("2", None, "BBB")]
    result = diff_laws(a, b, mode="article")
    statuses = sorted((c.status, c.article_no.jo) for c in result.changes)
    # 1 removed + 2 added (no rename since similarity between AAA/BBB is low).
    assert ("removed", "1") in statuses
    assert ("added", "2") in statuses


def test_modified_unchanged_omitted() -> None:
    a = [_a("1", None, "old")]
    b = [_a("1", None, "new")]
    result = diff_laws(a, b, mode="article")
    assert len(result.changes) == 1
    assert result.changes[0].status == "modified"
    assert result.changes[0].hunk  # unified diff present


def test_unchanged_hidden_by_default() -> None:
    a = [_a("1", None, "same")]
    b = [_a("1", None, "same")]
    result = diff_laws(a, b, mode="article")
    assert result.changes == []


def test_unchanged_shown_with_flag() -> None:
    a = [_a("1", None, "same")]
    b = [_a("1", None, "same")]
    result = diff_laws(a, b, mode="article", show_unchanged=True)
    assert len(result.changes) == 1
    assert result.changes[0].status == "unchanged"


def test_whitespace_only_change() -> None:
    a = [_a("1", None, "hello world")]
    b = [_a("1", None, "hello   world\n")]
    result = diff_laws(a, b, mode="article")
    assert len(result.changes) == 1
    assert result.changes[0].status == "whitespace-only"


def test_rename_above_threshold() -> None:
    common = "X" * 100 + "한국어 텍스트 예시"  # long matching payload
    a = [_a("1", None, common)]
    b = [_a("2", None, common + "Y")]  # >0.8 ratio
    result = diff_laws(a, b, mode="article")
    statuses = [(c.status, c.similarity) for c in result.changes]
    assert any(s[0] == "renamed" for s in statuses)
    ratio = next(s[1] for s in statuses if s[0] == "renamed")
    assert ratio is not None
    assert ratio >= RENAME_SIMILARITY_THRESHOLD


def test_rename_below_threshold_splits() -> None:
    a = [_a("1", None, "totally different content A")]
    b = [_a("2", None, "entirely unrelated body XYZ")]
    result = diff_laws(a, b, mode="article")
    statuses = {c.status for c in result.changes}
    assert statuses == {"added", "removed"}


def test_unified_mode_returns_text() -> None:
    result = diff_laws([], [], a_body="foo\n", b_body="bar\n", mode="unified")
    assert result.mode == "unified"
    assert result.text is not None
    assert "+bar" in result.text


def test_same_number_with_different_spelling_pairs_as_one_article() -> None:
    for spelling in ("01", "１"):
        result = diff_laws([_a(spelling, None, "old")], [_a("1", None, "new")], mode="article")
        assert [c.status for c in result.changes] == ["modified"], spelling


def _numbers(changes, status: str) -> list[tuple[str, str | None]]:
    return [(c.article_no.jo, c.article_no.ui) for c in changes if c.status == status]


def test_shared_articles_sort_by_number() -> None:
    numbers = [("10", None), ("2", None), ("839", "10"), ("839", "2"), ("839", None), ("839", "0")]
    articles = [_a(jo, ui, f"same {jo} {ui}") for jo, ui in numbers]
    result = diff_laws(articles, list(articles), mode="article", show_unchanged=True)
    assert _numbers(result.changes, "unchanged") == [
        ("2", None), ("10", None), ("839", None), ("839", "0"), ("839", "2"), ("839", "10"),
    ]


def test_removed_and_added_sort_by_number() -> None:
    a = [_a("10", None, "가가가"), _a("839", "10", "나나나"), _a("9", None, "다다다"), _a("839", "2", "라라라")]
    b = [_a("100", None, "xxx"), _a("840", "10", "yyy"), _a("20", None, "zzz"), _a("840", "2", "www")]
    result = diff_laws(a, b, mode="article")
    assert _numbers(result.changes, "removed") == [("9", None), ("10", None), ("839", "2"), ("839", "10")]
    assert _numbers(result.changes, "added") == [("20", None), ("100", None), ("840", "2"), ("840", "10")]
