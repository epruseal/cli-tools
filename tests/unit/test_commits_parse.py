"""Parse a stored GitHub commits API response fixture."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from legalize_cli.github.commits import list_commits
from legalize_cli.http import GitHubClient

FIXTURE = Path(__file__).parent.parent / "fixtures" / "github" / "commits_mingbeop.json"


def _transport(payload: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def test_list_commits_parses_fixture_and_normalizes_to_kst() -> None:
    payload = json.loads(FIXTURE.read_text())
    client = GitHubClient(transport=_transport(payload), token=None, token_source="none")

    commits = list_commits(client, "legalize-kr", "legalize-kr", "kr/민법/법률.md")

    assert len(commits) == 3
    first = commits[0]
    # SHA round-trips verbatim.
    assert first.sha.startswith("ca7d5c5")
    # GitHub commit dates are normalized to KST for date-based law lookups.
    author_offset = first.author_date.utcoffset()
    committer_offset = first.committer_date.utcoffset()
    assert author_offset is not None
    assert committer_offset is not None
    assert author_offset.total_seconds() == 9 * 3600
    assert committer_offset.total_seconds() == 9 * 3600
    assert first.committer_date.hour == 12


def test_list_commits_converts_github_utc_author_date_to_kst() -> None:
    payload = [
        {
            "sha": "abc123",
            "commit": {
                "author": {"date": "2026-04-30T03:00:00Z"},
                "committer": {"date": "2026-04-30T04:30:00Z"},
                "message": "UTC from GitHub",
            },
        }
    ]
    client = GitHubClient(transport=_transport(payload), token=None, token_source="none")

    commit = list_commits(client, "legalize-kr", "legalize-kr", "kr/민법/법률.md")[0]

    assert commit.author_date.isoformat() == "2026-04-30T12:00:00+09:00"
    assert commit.committer_date.isoformat() == "2026-04-30T13:30:00+09:00"


def test_list_commits_message_preserved() -> None:
    payload = json.loads(FIXTURE.read_text())
    client = GitHubClient(transport=_transport(payload), token=None, token_source="none")

    commits = list_commits(client, "legalize-kr", "legalize-kr", "kr/민법/법률.md")

    assert "민법" in commits[0].message


def test_list_commits_fetches_all_pages() -> None:
    calls: list[str] = []

    def item(sha: str) -> dict:
        return {
            "sha": sha,
            "commit": {
                "author": {"date": "2026-04-30T03:00:00Z"},
                "committer": {"date": "2026-04-30T04:30:00Z"},
                "message": sha,
            },
        }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "page=1" in url:
            return httpx.Response(200, json=[item("sha-1"), item("sha-2")])
        if "page=2" in url:
            return httpx.Response(200, json=[item("sha-3")])
        return httpx.Response(404, json={"message": f"unexpected {url}"})

    client = GitHubClient(
        transport=httpx.MockTransport(handler),
        token=None,
        token_source="none",
    )

    commits = list_commits(
        client,
        "legalize-kr",
        "legalize-kr",
        "kr/민법/법률.md",
        per_page=2,
    )

    assert [commit.sha for commit in commits] == ["sha-1", "sha-2", "sha-3"]
    assert any("page=2" in url for url in calls)
