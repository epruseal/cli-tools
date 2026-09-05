"""Resolve ``as-of date D`` to a specific law revision.

Same-date tiebreaker (§5 of the plan):

1. Newest ``author.date`` at-or-before the target.
2. Among ties on ``author.date``, newest ``committer.date``.
3. Among ties on both, lexicographically smallest SHA.

Git author dates are the source ``공포일자`` except for dates before the Unix
epoch, which are stored as ``1970-01-01``. ``시행일자`` always comes from YAML
frontmatter. Call :func:`resolve_as_of_with_frontmatter` when a caller needs
either of those frontmatter-aware semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, List, Literal, Optional

from ..github.commits import CommitInfo
from .model import Frontmatter

#: +09:00 KST — used when building the end-of-day upper bound for as-of scans.
_KST = timezone(timedelta(hours=9))
_GIT_EPOCH = date(1970, 1, 1)

Semantic = Literal["공포일자", "시행일자"]


@dataclass(frozen=True)
class ResolvedAsOf:
    """A selected revision and the frontmatter date used to select it."""

    commit: CommitInfo
    frontmatter: Frontmatter
    semantic_date: date


def resolve_as_of(
    commits: List[CommitInfo],
    target_date: date,
    semantic: Semantic = "공포일자",
) -> Optional[CommitInfo]:
    """Return the Git-author-date revision for ``공포일자``, else ``None``.

    ``시행일자`` cannot be inferred from Git timestamps. Reject it explicitly
    instead of silently returning a 공포일자 result.
    """
    if not commits:
        return None

    if semantic != "공포일자":
        raise ValueError("시행일자 선택에는 resolve_as_of_with_frontmatter()를 사용해야 합니다")

    return _choose_by_author_date(commits, target_date)


def candidates_for_semantic(
    commits: List[CommitInfo],
    target_date: date,
) -> List[CommitInfo]:
    """Return semantic-selection candidates in deterministic newest-first order.

    Before 1970 Git timestamps cannot distinguish actual 공포일자, so every
    revision must be considered and its frontmatter inspected. From 1970 on,
    no revision promulgated after the target can already be in force; limiting
    candidates by author date is safe and avoids needless file fetches.
    """
    if target_date < _GIT_EPOCH:
        candidates = list(commits)
    else:
        target_dt = _end_of_day_kst(target_date)
        candidates = [c for c in commits if c.author_date <= target_dt]
    return _sort_newest_first(candidates)


def resolve_as_of_with_frontmatter(
    commits: List[CommitInfo],
    target_date: date,
    semantic: Semantic,
    get_frontmatter: Callable[[CommitInfo], Frontmatter],
) -> Optional[ResolvedAsOf]:
    """Select the revision whose chosen frontmatter date is at or before target.

    ``공포일자`` normally uses the Git-author-date index. When the selected
    range contains the Unix-epoch clamp, it reads the affected frontmatters so
    pre-1970 dates are resolved from their real source values. ``시행일자``
    reads each possible revision's frontmatter and never falls back to
    ``공포일자``.

    The result is file-level only. A law may have a later 시행일 for an
    individual article in its 부칙, which this selection intentionally does
    not attempt to infer.
    """
    if not commits:
        return None

    if semantic == "공포일자":
        author_choice = _choose_by_author_date(commits, target_date)
        if author_choice is not None and author_choice.author_date.date() != _GIT_EPOCH:
            fm = get_frontmatter(author_choice)
            if fm.promulgation_date is not None and fm.promulgation_date <= target_date:
                return ResolvedAsOf(author_choice, fm, fm.promulgation_date)

        candidates = candidates_for_semantic(commits, target_date)
        if (
            target_date >= _GIT_EPOCH
            and author_choice is not None
            and author_choice.author_date.date() == _GIT_EPOCH
        ):
            candidates = [c for c in candidates if c.author_date.date() == _GIT_EPOCH]
    else:
        candidates = candidates_for_semantic(commits, target_date)

    return _choose_by_frontmatter_date(candidates, target_date, semantic, get_frontmatter)


# ---- internals --------------------------------------------------------


def _choose_by_author_date(commits: List[CommitInfo], target_date: date) -> Optional[CommitInfo]:
    target_dt = _end_of_day_kst(target_date)
    candidates = [c for c in commits if c.author_date <= target_dt]
    if not candidates:
        return None

    # Step 1: newest author_date.
    newest_author = max(c.author_date for c in candidates)
    first_pass = [c for c in candidates if c.author_date == newest_author]
    if len(first_pass) == 1:
        return first_pass[0]

    # Step 2: newest committer_date.
    newest_committer = max(c.committer_date for c in first_pass)
    second_pass = [c for c in first_pass if c.committer_date == newest_committer]
    if len(second_pass) == 1:
        return second_pass[0]

    # Step 3: lexicographically smallest SHA.
    return min(second_pass, key=lambda c: c.sha)


def _choose_by_frontmatter_date(
    candidates: List[CommitInfo],
    target_date: date,
    semantic: Semantic,
    get_frontmatter: Callable[[CommitInfo], Frontmatter],
) -> Optional[ResolvedAsOf]:
    resolved: list[ResolvedAsOf] = []
    for commit in candidates:
        fm = get_frontmatter(commit)
        semantic_date = (
            fm.promulgation_date if semantic == "공포일자" else fm.enforcement_date
        )
        if semantic_date is not None and semantic_date <= target_date:
            resolved.append(ResolvedAsOf(commit, fm, semantic_date))

    if not resolved:
        return None

    newest_semantic = max(item.semantic_date for item in resolved)
    first_pass = [item for item in resolved if item.semantic_date == newest_semantic]

    newest_promulgation = max(
        item.frontmatter.promulgation_date or date.min for item in first_pass
    )
    second_pass = [
        item
        for item in first_pass
        if (item.frontmatter.promulgation_date or date.min) == newest_promulgation
    ]

    newest_author = max(item.commit.author_date for item in second_pass)
    third_pass = [item for item in second_pass if item.commit.author_date == newest_author]
    newest_committer = max(item.commit.committer_date for item in third_pass)
    finalists = [
        item for item in third_pass if item.commit.committer_date == newest_committer
    ]
    return min(finalists, key=lambda item: item.commit.sha)


def _sort_newest_first(commits: List[CommitInfo]) -> List[CommitInfo]:
    """Apply the documented author/committer/SHA ordering to every candidate."""
    by_sha = sorted(commits, key=lambda c: c.sha)
    return sorted(by_sha, key=lambda c: (c.author_date, c.committer_date), reverse=True)


def _end_of_day_kst(d: date) -> datetime:
    """``YYYY-MM-DDT23:59:59+09:00`` — the inclusive upper bound per §5."""
    return datetime.combine(d, time(23, 59, 59), tzinfo=_KST)


__all__ = [
    "ResolvedAsOf",
    "Semantic",
    "candidates_for_semantic",
    "resolve_as_of",
    "resolve_as_of_with_frontmatter",
]
