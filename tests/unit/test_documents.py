"""Unit tests for tree-based administrative-rule and ordinance helpers."""

from __future__ import annotations

import httpx

from legalize_cli.documents import (
    enumerate_documents,
    fetch_document_by_name_or_path,
    filter_and_paginate_documents,
)
from legalize_cli.http import GitHubClient


TREE_PAYLOAD = {
    "tree": [
        {"path": "README.md", "type": "blob", "sha": "readme"},
        {
            "path": "행정안전부/_본부/고시/공공데이터 관리지침/본문.md",
            "type": "blob",
            "sha": "admrule-1",
        },
        {
            "path": "행정안전부/_본부/훈령/정보공개 운영규정/본문.md",
            "type": "blob",
            "sha": "admrule-2",
        },
        {
            "path": "서울특별시/_본청/조례/서울특별시 테스트 조례/본문.md",
            "type": "blob",
            "sha": "ordinance-1",
        },
    ]
}

SAMPLE_BODY = "---\n행정규칙명: '공공데이터 관리지침'\n---\n\nbody".encode()


def _client() -> GitHubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/git/trees/" in url:
            return httpx.Response(200, json=TREE_PAYLOAD)
        if "/contents/" in url:
            return httpx.Response(200, content=SAMPLE_BODY)
        return httpx.Response(404, json={"message": "unexpected"})

    return GitHubClient(
        transport=httpx.MockTransport(handler),
        token=None,
        token_source="none",
    )


def test_enumerate_documents_parses_terminal_body_paths() -> None:
    client = _client()
    try:
        entries = enumerate_documents(client, repo="admrule-kr")
    finally:
        client.close()

    by_name = {entry.name: entry for entry in entries}
    entry = by_name["공공데이터 관리지침"]
    assert entry.category == "고시"
    assert entry.parents == ["행정안전부", "_본부"]
    assert "README.md" not in {item.path for item in entries}


def test_filter_and_paginate_documents_filters_parent_and_category() -> None:
    client = _client()
    try:
        entries = enumerate_documents(client, repo="admrule-kr")
    finally:
        client.close()

    total, window, next_page = filter_and_paginate_documents(
        entries,
        category="고시",
        parent_contains="행정안전부",
        page=1,
        page_size=1,
    )

    assert total == 1
    assert window[0].name == "공공데이터 관리지침"
    assert next_page is None


def test_fetch_document_by_name_or_path_fetches_exact_name() -> None:
    client = _client()
    try:
        path, body = fetch_document_by_name_or_path(
            client,
            None,
            "공공데이터 관리지침",
            repo="admrule-kr",
            category="고시",
        )
    finally:
        client.close()

    assert path == "행정안전부/_본부/고시/공공데이터 관리지침/본문.md"
    assert body == SAMPLE_BODY
