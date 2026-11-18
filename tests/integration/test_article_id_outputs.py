"""Article IDs in CLI JSON and MCP responses for ``laws article`` and ``laws diff``."""

from __future__ import annotations

import json
from datetime import date, datetime

import anyio
import httpx
import jsonschema
from mcp import Client
from typer.testing import CliRunner

from legalize_cli.__main__ import app
from legalize_cli.github.commits import CommitInfo
from legalize_cli.http import GitHubClient
from legalize_cli.laws.asof import ResolvedAsOf
from legalize_cli.laws.frontmatter import parse as parse_frontmatter
from legalize_cli.laws.lookup import ResolvedLawFile
from legalize_cli.mcp.server import build_server
from legalize_cli.services import laws as law_service
from legalize_cli.services.context import ServiceContext

from .conftest import install_client_factory

_FRONTMATTER = (
    "---\n제목: 예시법\n법령구분: 법률\n법령ID: '000100'\n법령MST: 101\n"
    "공포일자: 2013-04-05\n시행일자: 2013-07-01\n출처: https://example.test/law\n---\n"
)
_MOVED = "이 조문은 다른 번호로 옮겨진 긴 본문이다. " * 4
OLD_BODY = (
    f"{_FRONTMATTER}\n##### 제01조 (목적)\n\n이전 목적 규정\n\n"
    f"##### 제5조 (옮겨진 조문)\n\n{_MOVED}\n"
)
NEW_BODY = (
    f"{_FRONTMATTER}\n##### 제01조 (목적)\n\n새 목적 규정\n\n"
    f"##### 제6조 (옮겨진 조문)\n\n{_MOVED}끝.\n"
)
OLD_SHA = "ca7d5c5"  # selected for 2017-11-01 by commits_asof.json


def _install_mock(monkeypatch, commits_fixture, old: bytes, new: bytes) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/commits" in url:
            return httpx.Response(200, json=commits_fixture)
        if "/contents/" in url and request.headers.get("accept") == "application/vnd.github+json":
            return httpx.Response(200, json=[])
        if "/contents/" in url:
            return httpx.Response(200, content=old if OLD_SHA in url else new)
        return httpx.Response(404, json={"message": f"no route for {url}"})

    install_client_factory(
        monkeypatch,
        lambda opts: (
            GitHubClient(transport=httpx.MockTransport(handler), token=None, token_source="none"),
            None,
        ),
    )


def _cli_article(query: str) -> tuple[int, dict | None, str]:
    result = CliRunner().invoke(
        app, ["laws", "article", "예시법", query, "--date", "2024-01-01", "--json"]
    )
    payload = json.loads(result.output) if result.exit_code == 0 else None
    return result.exit_code, payload, result.output


def test_cli_article_id_for_non_canonical_heading(monkeypatch, commits_fixture) -> None:
    _install_mock(monkeypatch, commits_fixture, OLD_BODY.encode(), NEW_BODY.encode())
    code, payload, output = _cli_article("1")
    assert code == 0, output
    assert payload["article_no"] == {"조": "01", "의": None, "항": None, "호": None}
    assert payload["article_id"] == "art_1"
    keys = list(payload)
    assert keys.index("article_id") == keys.index("article_no") + 1


def test_cli_query_normalization_finds_article(monkeypatch, commits_fixture, mingbeop_2024_bytes) -> None:
    _install_mock(monkeypatch, commits_fixture, mingbeop_2024_bytes, mingbeop_2024_bytes)
    _, ascii_payload, _ = _cli_article("839-2")
    for query in ("８３９조의２", "0839조의2"):
        code, payload, output = _cli_article(query)
        assert code == 0, output
        assert payload["article_id"] == "art_839-2"
        assert payload["content"] == ascii_payload["content"]
    assert ascii_payload["article_no"] == {"조": "839", "의": "2", "항": None, "호": None}


def test_cli_fullwidth_heading_found_by_ascii_query(monkeypatch, commits_fixture) -> None:
    body = f"{_FRONTMATTER}\n##### 제１조 (목적)\n\n전각 번호 조문\n".encode()
    _install_mock(monkeypatch, commits_fixture, body, body)
    code, payload, output = _cli_article("1")
    assert code == 0, output
    assert payload["article_id"] == "art_1"
    assert "전각 번호 조문" in payload["content"]


def test_cli_diff_article_ids(monkeypatch, commits_fixture) -> None:
    _install_mock(monkeypatch, commits_fixture, OLD_BODY.encode(), NEW_BODY.encode())
    result = CliRunner().invoke(
        app,
        ["laws", "diff", "예시법", "예시법", "--date-a", "2017-11-01", "--date-b", "2024-01-01", "--json"],
    )
    assert result.exit_code == 0, result.output
    changes = {c["status"]: c for c in json.loads(result.output)["changes"]}
    modified = changes["modified"]
    assert modified["article_id"] == "art_1"
    assert list(modified)[:2] == ["article_no", "article_id"]
    assert "from_article_id" not in modified
    renamed = changes["renamed"]
    assert renamed["article_id"] == "art_6"
    assert renamed["from_article_id"] == "art_5"
    keys = list(renamed)
    assert keys.index("from_article_id") == keys.index("from") + 1


class _DummyClient:
    token_source = "none"

    def get_json(self, path, **kwargs):
        return []

    def close(self) -> None:
        pass


def _resolved(sha: str, promulgation: str, raw_text: str) -> ResolvedLawFile:
    raw = raw_text.replace("공포일자: 2013-04-05", f"공포일자: {promulgation}").encode()
    fm, _ = parse_frontmatter(raw.decode())
    stamp = datetime.fromisoformat(promulgation + "T12:00:00+09:00")
    commit = CommitInfo(sha=sha, author_date=stamp, committer_date=stamp, message="")
    return ResolvedLawFile(
        resolution=ResolvedAsOf(commit=commit, frontmatter=fm, semantic_date=fm.promulgation_date),
        raw=raw,
    )


def test_mcp_article_ids_and_output_schema(monkeypatch) -> None:
    old = _resolved("a" * 40, "2025-01-01", OLD_BODY)
    new = _resolved("b" * 40, "2026-03-01", NEW_BODY)

    def resolve(_client, _cache, _path, target, semantic):
        if target < date(2025, 1, 1):
            return None
        return old if target < date(2026, 3, 1) else new

    monkeypatch.setattr(law_service, "resolve_law_file_as_of", resolve)

    async def run() -> None:
        async with Client(build_server(lambda: ServiceContext(_DummyClient(), None))) as client:
            schemas = {tool.name: tool.output_schema for tool in (await client.list_tools()).tools}

            article = await client.call_tool("laws_article", {
                "law_name": "예시법", "article_no": "1", "date": "2026-04-01",
            })
            assert not article.is_error
            data = article.structured_content
            assert data["article_id"] == "art_1"
            keys = list(json.loads(article.content[0].text))
            assert keys.index("article_id") == keys.index("article_no") + 1
            jsonschema.validate(data, schemas["laws_article"])

            difference = await client.call_tool("laws_diff", {
                "law_name": "예시법", "date_a": "2025-01-01", "date_b": "2026-04-01",
            })
            assert not difference.is_error
            changes = {c["status"]: c for c in difference.structured_content["changes"]}
            assert changes["modified"]["article_id"] == "art_1"
            assert changes["modified"]["from_article_id"] is None
            assert changes["renamed"]["article_id"] == "art_6"
            assert changes["renamed"]["from_article_id"] == "art_5"
            jsonschema.validate(difference.structured_content, schemas["laws_diff"])
    anyio.run(run)
