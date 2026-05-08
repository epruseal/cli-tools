"""Integration tests for tree-based precedent search (no metadata.json)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from legalize_cli.http import GitHubClient
from legalize_cli.search.tree_filter import tree_filter_items

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _tree() -> dict:
    return json.loads((FIXTURES / "precedents" / "tree_precedents.json").read_text())


def _client(tree_payload: dict) -> GitHubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/git/trees/" in str(request.url):
            return httpx.Response(200, json=tree_payload)
        return httpx.Response(404, json={"message": "unexpected"})

    return GitHubClient(
        transport=httpx.MockTransport(handler), token=None, token_source="none"
    )


def test_search_by_사건번호_in_path() -> None:
    client = _client(_tree())
    try:
        items = tree_filter_items(client, None, "2018도11111", repo="precedent-kr", source="precedents")
    finally:
        client.close()

    assert len(items) == 1
    assert items[0]["path"] == "형사/대법원/2018도11111.md"
    assert items[0]["source"] == "precedents"


def test_search_by_사건종류_in_path() -> None:
    client = _client(_tree())
    try:
        items = tree_filter_items(client, None, "가사", repo="precedent-kr", source="precedents")
    finally:
        client.close()

    assert len(items) >= 1
    assert all("가사" in item["path"] for item in items)


def test_search_no_match() -> None:
    client = _client(_tree())
    try:
        items = tree_filter_items(client, None, "없는키워드XYZ99999", repo="precedent-kr", source="precedents")
    finally:
        client.close()

    assert items == []


def test_search_handles_truncated_root_tree() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/git/trees/HEAD?recursive=1"):
            return httpx.Response(
                200,
                json={
                    "truncated": True,
                    "tree": [
                        {
                            "path": "민사",
                            "type": "tree",
                            "sha": "sha-civil",
                        }
                    ],
                },
            )
        if url.endswith("/git/trees/HEAD"):
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {
                            "path": "민사",
                            "type": "tree",
                            "sha": "sha-civil",
                        },
                        {
                            "path": "특허",
                            "type": "tree",
                            "sha": "sha-patent",
                        },
                    ]
                },
            )
        if url.endswith("/git/trees/sha-civil?recursive=1"):
            return httpx.Response(200, json={"tree": []})
        if url.endswith("/git/trees/sha-patent?recursive=1"):
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {
                            "path": "대법원/대법원_2026-01-15_2022후10401.md",
                            "type": "blob",
                            "sha": "sha-doc",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"message": f"unexpected {url}"})

    client = GitHubClient(
        transport=httpx.MockTransport(handler),
        token=None,
        token_source="none",
    )
    try:
        items = tree_filter_items(
            client,
            None,
            "2022후10401",
            repo="precedent-kr",
            source="precedents",
        )
    finally:
        client.close()

    assert items == [
        {
            "source": "precedents",
            "path": "특허/대법원/대법원_2026-01-15_2022후10401.md",
            "match_type": "title",
        }
    ]
