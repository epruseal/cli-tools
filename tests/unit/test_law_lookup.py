"""Tests for the shared frontmatter-aware law revision lookup."""

from __future__ import annotations

from datetime import date, datetime
from typing import cast

from legalize_cli.github.commits import CommitInfo
from legalize_cli.http import GitHubClient
from legalize_cli.laws import lookup


def _commit(sha: str, stamp: str) -> CommitInfo:
    value = datetime.fromisoformat(stamp)
    return CommitInfo(sha=sha, author_date=value, committer_date=value, message="")


def _law_text(promulgation: str, enforcement: str, text: str) -> bytes:
    return (
        f"---\n공포일자: {promulgation}\n시행일자: {enforcement}\n---\n"
        f"##### 제1조\n\n{text}\n"
    ).encode()


def test_lookup_uses_effective_date_and_fetches_selected_body_once(monkeypatch) -> None:
    commits = [
        _commit("new", "2026-03-01T12:00:00+09:00"),
        _commit("old", "2025-01-01T12:00:00+09:00"),
    ]
    bodies = {
        "new": _law_text("2026-03-01", "2026-07-01", "new"),
        "old": _law_text("2025-01-01", "2025-01-01", "old"),
    }
    fetched: list[str] = []

    monkeypatch.setattr(lookup, "get_revisions", lambda *_args, **_kwargs: commits)

    def get_raw(_client, _owner, _repo, _path, ref):
        fetched.append(ref)
        return bodies[ref]

    monkeypatch.setattr(lookup, "get_file_raw", get_raw)

    resolved = lookup.resolve_law_file_as_of(
        cast(GitHubClient, object()),
        None,
        "kr/예시법/법률.md",
        date(2026, 4, 1),
        "시행일자",
    )

    assert resolved is not None
    assert resolved.resolution.commit.sha == "old"
    assert resolved.resolution.semantic_date.isoformat() == "2025-01-01"
    assert resolved.raw == bodies["old"]
    assert fetched == ["new", "old"]


def test_lookup_uses_real_pre_epoch_promulgation_date(monkeypatch) -> None:
    commits = [
        _commit("1960", "1970-01-01T12:00:00+09:00"),
        _commit("1958", "1970-01-01T12:00:00+09:00"),
    ]
    bodies = {
        "1960": _law_text("1960-01-01", "1960-01-01", "1960 version"),
        "1958": _law_text("1958-02-22", "1960-01-01", "1958 version"),
    }

    monkeypatch.setattr(lookup, "get_revisions", lambda *_args, **_kwargs: commits)
    monkeypatch.setattr(
        lookup,
        "get_file_raw",
        lambda _client, _owner, _repo, _path, ref: bodies[ref],
    )

    resolved = lookup.resolve_law_file_as_of(
        cast(GitHubClient, object()),
        None,
        "kr/예시법/법률.md",
        date(1959, 1, 1),
        "공포일자",
    )

    assert resolved is not None
    assert resolved.resolution.commit.sha == "1958"
    assert resolved.resolution.semantic_date.isoformat() == "1958-02-22"
