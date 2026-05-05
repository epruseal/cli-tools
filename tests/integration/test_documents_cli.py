"""Integration tests for admrules/ordinances CLI commands."""

from __future__ import annotations

import json

import httpx
from typer.testing import CliRunner

from legalize_cli.__main__ import app
from legalize_cli.http import GitHubClient

from .conftest import build_mock, install_client_factory

TREE_PAYLOAD = {
    "tree": [
        {
            "path": "행정안전부/_본부/고시/공공데이터 관리지침/본문.md",
            "type": "blob",
            "sha": "admrule-1",
        },
        {
            "path": "서울특별시/_본청/조례/서울특별시 테스트 조례/본문.md",
            "type": "blob",
            "sha": "ordinance-1",
        },
    ]
}

ADMRULE_BODY = "---\n행정규칙명: '공공데이터 관리지침'\n---\n\n행정규칙 본문".encode()
ORDINANCE_BODY = "---\n자치법규명: '서울특별시 테스트 조례'\n---\n\n자치법규 본문".encode()


def test_admrules_list_json(monkeypatch) -> None:
    mock = build_mock({"/git/trees/": (200, TREE_PAYLOAD)})
    install_client_factory(
        monkeypatch,
        lambda opts: (
            GitHubClient(transport=mock.transport(), token=None, token_source="none"),
            None,
        ),
    )

    result = CliRunner().invoke(app, ["admrules", "list", "--type", "고시", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["kind"] == "admrules.list"
    assert payload["items"][0]["name"] == "공공데이터 관리지침"


def test_ordinances_get_json(monkeypatch) -> None:
    def contents_response(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "ordinance-kr" in url:
            return httpx.Response(200, content=ORDINANCE_BODY)
        return httpx.Response(200, content=ADMRULE_BODY)

    mock = build_mock({
        "/git/trees/": (200, TREE_PAYLOAD),
        "/contents/": contents_response,
    })
    install_client_factory(
        monkeypatch,
        lambda opts: (
            GitHubClient(transport=mock.transport(), token=None, token_source="none"),
            None,
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "ordinances",
            "get",
            "서울특별시 테스트 조례",
            "--type",
            "조례",
            "--jurisdiction",
            "서울특별시",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["kind"] == "ordinances.get"
    assert payload["path"] == "서울특별시/_본청/조례/서울특별시 테스트 조례/본문.md"
    assert payload["body"] == "\n자치법규 본문"
