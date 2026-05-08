"""Unit tests for the GitHub code search wrapper."""

from __future__ import annotations

import httpx
import pytest

from legalize_cli.github.search_code import SearchIncompleteError, search_code
from legalize_cli.http import GitHubClient


def _item(index: int) -> dict:
    return {
        "path": f"kr/테스트{index}/법률.md",
        "sha": f"sha-{index}",
        "name": "법률.md",
    }


def test_search_code_fetches_pages_up_to_limit() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        page = request.url.params.get("page")
        if page == "1":
            return httpx.Response(
                200,
                json={
                    "total_count": 101,
                    "incomplete_results": False,
                    "items": [_item(i) for i in range(100)],
                },
            )
        if page == "2":
            return httpx.Response(
                200,
                json={
                    "total_count": 101,
                    "incomplete_results": False,
                    "items": [_item(100)],
                },
            )
        return httpx.Response(404, json={"message": f"unexpected {url}"})

    client = GitHubClient(
        transport=httpx.MockTransport(handler),
        token="fake-token",
        token_source="flag",
    )

    matches = search_code(client, "테스트", repo="legalize-kr/legalize-kr", limit=101)

    assert len(matches) == 101
    assert matches[-1].path == "kr/테스트100/법률.md"
    assert any("page=2" in url for url in calls)


def test_search_code_raises_on_incomplete_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total_count": 10,
                "incomplete_results": True,
                "items": [_item(1)],
            },
        )

    client = GitHubClient(
        transport=httpx.MockTransport(handler),
        token="fake-token",
        token_source="flag",
    )

    with pytest.raises(SearchIncompleteError):
        search_code(client, "테스트", repo="legalize-kr/legalize-kr")
