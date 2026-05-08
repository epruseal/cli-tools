"""Parse a stored GitHub tree response fixture."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from legalize_cli.github.trees import get_tree
from legalize_cli.http import GitHubClient

FIXTURE = Path(__file__).parent.parent / "fixtures" / "github" / "tree_laws_small.json"


def _transport(payload: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def test_get_tree_counts_entries() -> None:
    payload = json.loads(FIXTURE.read_text())
    client = GitHubClient(transport=_transport(payload), token=None, token_source="none")

    entries = get_tree(client, "legalize-kr", "legalize-kr", "HEAD")

    assert len(entries) == 10
    blobs = [e for e in entries if e.type == "blob"]
    trees = [e for e in entries if e.type == "tree"]
    assert len(blobs) == 8
    assert len(trees) == 2


def test_get_tree_preserves_korean_paths() -> None:
    payload = json.loads(FIXTURE.read_text())
    client = GitHubClient(transport=_transport(payload), token=None, token_source="none")

    entries = get_tree(client, "legalize-kr", "legalize-kr", "HEAD")
    paths = {e.path for e in entries}

    assert "kr/민법/법률.md" in paths
    assert "kr/주택법/시행규칙.md" in paths


def test_get_tree_falls_back_when_recursive_tree_is_truncated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/git/trees/HEAD?recursive=1"):
            return httpx.Response(
                200,
                json={
                    "truncated": True,
                    "tree": [
                        {
                            "path": "강원특별자치도",
                            "type": "tree",
                            "sha": "sha-gangwon",
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
                            "path": "강원특별자치도",
                            "type": "tree",
                            "sha": "sha-gangwon",
                        },
                        {
                            "path": "서울특별시",
                            "type": "tree",
                            "sha": "sha-seoul",
                        },
                    ]
                },
            )
        if url.endswith("/git/trees/sha-gangwon?recursive=1"):
            return httpx.Response(200, json={"tree": []})
        if url.endswith("/git/trees/sha-seoul?recursive=1"):
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {
                            "path": "송파구",
                            "type": "tree",
                            "sha": "sha-songpa",
                        },
                        {
                            "path": "송파구/조례/서울특별시 송파구 테스트 조례/본문.md",
                            "type": "blob",
                            "sha": "sha-doc",
                        },
                    ]
                },
            )
        return httpx.Response(404, json={"message": f"unexpected {url}"})

    client = GitHubClient(
        transport=httpx.MockTransport(handler),
        token=None,
        token_source="none",
    )

    entries = get_tree(client, "legalize-kr", "ordinance-kr", "HEAD")
    paths = {entry.path for entry in entries}

    assert "서울특별시" in paths
    assert "서울특별시/송파구" in paths
    assert "서울특별시/송파구/조례/서울특별시 송파구 테스트 조례/본문.md" in paths


def test_get_tree_falls_back_when_nested_recursive_tree_is_truncated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/git/trees/HEAD?recursive=1"):
            return httpx.Response(
                200,
                json={
                    "truncated": True,
                    "tree": [
                        {
                            "path": "일반행정",
                            "type": "tree",
                            "sha": "sha-admin",
                        }
                    ]
                },
            )
        if url.endswith("/git/trees/HEAD"):
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {
                            "path": "일반행정",
                            "type": "tree",
                            "sha": "sha-admin",
                        }
                    ]
                },
            )
        if url.endswith("/git/trees/sha-admin?recursive=1"):
            return httpx.Response(
                200,
                json={
                    "truncated": True,
                    "tree": [
                        {
                            "path": "대법원",
                            "type": "tree",
                            "sha": "sha-supreme",
                        }
                    ],
                },
            )
        if url.endswith("/git/trees/sha-admin"):
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {
                            "path": "대법원",
                            "type": "tree",
                            "sha": "sha-supreme",
                        },
                        {
                            "path": "하급심",
                            "type": "tree",
                            "sha": "sha-lower",
                        },
                    ]
                },
            )
        if url.endswith("/git/trees/sha-supreme?recursive=1"):
            return httpx.Response(200, json={"tree": []})
        if url.endswith("/git/trees/sha-lower?recursive=1"):
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {
                            "path": "서울행정법원_2025-01-01_2025구합1.md",
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

    entries = get_tree(client, "legalize-kr", "precedent-kr", "HEAD")
    paths = {entry.path for entry in entries}

    assert "일반행정/하급심" in paths
    assert "일반행정/하급심/서울행정법원_2025-01-01_2025구합1.md" in paths
