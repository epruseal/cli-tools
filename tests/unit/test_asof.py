"""Unit tests for :mod:`legalize_cli.laws.asof`.

Pins the same-date tiebreaker contract:
1. Newest ``author_date``.
2. Newest ``committer_date``.
3. Lexicographically smallest SHA.
"""

from __future__ import annotations

from datetime import date, timedelta, timezone
from typing import List

from legalize_cli.github.commits import CommitInfo
from legalize_cli.laws.asof import (
    candidates_for_semantic,
    resolve_as_of,
    resolve_as_of_with_frontmatter,
)
from legalize_cli.laws.model import Frontmatter

_KST = timezone(timedelta(hours=9))


def _c(sha: str, author_iso: str, committer_iso: str | None = None) -> CommitInfo:
    from dateutil import parser as dateparser

    author_dt = dateparser.isoparse(author_iso)
    committer_dt = dateparser.isoparse(committer_iso or author_iso)
    return CommitInfo(
        sha=sha, author_date=author_dt, committer_date=committer_dt, message=""
    )


def test_empty_returns_none() -> None:
    assert resolve_as_of([], date(2020, 1, 1)) is None


def test_before_all_returns_none() -> None:
    commits: List[CommitInfo] = [_c("aa", "2020-01-01T12:00:00+09:00")]
    assert resolve_as_of(commits, date(2019, 12, 31)) is None


def test_basic_newest_author_date_wins() -> None:
    commits = [
        _c("aa", "2017-01-01T12:00:00+09:00"),
        _c("bb", "2020-01-01T12:00:00+09:00"),
        _c("cc", "2022-01-01T12:00:00+09:00"),
    ]
    chosen = resolve_as_of(commits, date(2021, 1, 1))
    assert chosen is not None
    assert chosen.sha == "bb"


def test_tiebreak_step2_committer_date() -> None:
    """Same author_date → newest committer_date wins."""
    commits = [
        _c("aa", "2020-01-01T12:00:00+09:00", "2020-01-02T00:00:00+00:00"),
        _c("bb", "2020-01-01T12:00:00+09:00", "2020-01-03T00:00:00+00:00"),
        _c("cc", "2020-01-01T12:00:00+09:00", "2020-01-01T23:00:00+00:00"),
    ]
    chosen = resolve_as_of(commits, date(2020, 6, 1))
    assert chosen is not None
    assert chosen.sha == "bb"  # newest committer date


def test_tiebreak_step3_lex_smallest_sha() -> None:
    """Identical author AND committer dates → lex-smallest SHA wins."""
    same_author = "2020-01-01T12:00:00+09:00"
    same_committer = "2020-01-02T00:00:00+00:00"
    commits = [
        _c("zz99", same_author, same_committer),
        _c("aa00", same_author, same_committer),
        _c("mm50", same_author, same_committer),
    ]
    chosen = resolve_as_of(commits, date(2020, 6, 1))
    assert chosen is not None
    assert chosen.sha == "aa00"


def test_pre_1970_date() -> None:
    """Dates before epoch must still resolve."""
    commits = [
        _c("aa", "1968-06-01T12:00:00+09:00"),
        _c("bb", "1972-01-01T12:00:00+09:00"),
    ]
    chosen = resolve_as_of(commits, date(1970, 1, 1))
    assert chosen is not None
    assert chosen.sha == "aa"


def test_end_of_day_kst_inclusive() -> None:
    """A commit at 23:59 KST on the target date must be included."""
    commits = [_c("aa", "2020-06-15T23:59:00+09:00")]
    chosen = resolve_as_of(commits, date(2020, 6, 15))
    assert chosen is not None and chosen.sha == "aa"


def test_candidates_for_semantic_returns_list() -> None:
    commits = [
        _c("aa", "2017-01-01T12:00:00+09:00"),
        _c("bb", "2020-01-01T12:00:00+09:00"),
        _c("cc", "2022-01-01T12:00:00+09:00"),
    ]
    result = candidates_for_semantic(commits, date(2020, 12, 31))
    assert [c.sha for c in result] == ["bb", "aa"]


def test_시행일자_requires_frontmatter_instead_of_falling_back() -> None:
    commits = [
        _c("aa", "2017-01-01T12:00:00+09:00"),
        _c("bb", "2020-01-01T12:00:00+09:00"),
    ]
    import pytest

    with pytest.raises(ValueError, match="frontmatter"):
        resolve_as_of(commits, date(2021, 1, 1), semantic="시행일자")


def test_시행일자_uses_frontmatter_not_promulgation_date() -> None:
    commits = [
        _c("new", "2026-03-01T12:00:00+09:00"),
        _c("old", "2025-01-01T12:00:00+09:00"),
    ]
    frontmatters = {
        "new": Frontmatter.model_validate(
            {"공포일자": "2026-03-01", "시행일자": "2026-07-01"}
        ),
        "old": Frontmatter.model_validate(
            {"공포일자": "2025-01-01", "시행일자": "2025-01-01"}
        ),
    }

    resolved = resolve_as_of_with_frontmatter(
        commits,
        date(2026, 4, 1),
        "시행일자",
        lambda commit: frontmatters[commit.sha],
    )

    assert resolved is not None
    assert resolved.commit.sha == "old"
    assert resolved.semantic_date == date(2025, 1, 1)


def test_공포일자_uses_frontmatter_when_git_date_and_source_date_disagree() -> None:
    commits = [
        _c("new", "2026-03-01T12:00:00+09:00"),
        _c("old", "2025-01-01T12:00:00+09:00"),
    ]
    frontmatters = {
        "new": Frontmatter.model_validate(
            {"공포일자": "2026-05-01", "시행일자": "2026-07-01"}
        ),
        "old": Frontmatter.model_validate(
            {"공포일자": "2025-01-01", "시행일자": "2025-01-01"}
        ),
    }

    resolved = resolve_as_of_with_frontmatter(
        commits,
        date(2026, 4, 1),
        "공포일자",
        lambda commit: frontmatters[commit.sha],
    )

    assert resolved is not None
    assert resolved.commit.sha == "old"


def test_pre_1970_uses_frontmatter_promulgation_date() -> None:
    commits = [
        _c("new", "1970-01-01T12:00:00+09:00"),
        _c("old", "1970-01-01T12:00:00+09:00"),
    ]
    frontmatters = {
        "new": Frontmatter.model_validate(
            {"공포일자": "1960-01-01", "시행일자": "1960-01-01"}
        ),
        "old": Frontmatter.model_validate(
            {"공포일자": "1958-02-22", "시행일자": "1960-01-01"}
        ),
    }

    resolved = resolve_as_of_with_frontmatter(
        commits,
        date(1959, 1, 1),
        "공포일자",
        lambda commit: frontmatters[commit.sha],
    )

    assert resolved is not None
    assert resolved.commit.sha == "old"
    assert resolved.semantic_date == date(1958, 2, 22)
