"""Unit tests for MCP server tool registration and structure."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

mcp_pkg = pytest.importorskip("mcp", reason="mcp extra not installed")


def test_mcp_server_importable():
    from legalize_cli import mcp_server

    assert hasattr(mcp_server, "mcp")
    assert hasattr(mcp_server, "main")


def test_all_tools_registered():
    from legalize_cli import mcp_server

    expected = {
        "laws_list",
        "laws_get",
        "laws_article",
        "search",
        "precedents_list",
        "precedents_get",
        "admrules_list",
        "admrules_get",
        "ordinances_list",
        "ordinances_get",
    }
    for name in expected:
        assert hasattr(mcp_server, name), f"tool '{name}' not found in mcp_server"


def test_mcp_instance_name():
    from legalize_cli.mcp_server import mcp

    assert mcp.name == "legalize-kr"


def test_mcp_cmd_importable():
    from legalize_cli.commands.mcp_cmd import mcp_app

    assert mcp_app is not None


def test_mcp_help_reachable():
    """``legalize mcp --help`` exits 0 and lists the serve subcommand."""
    from typer.testing import CliRunner
    from legalize_cli.__main__ import app

    result = CliRunner().invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "serve" in result.output


def test_laws_get_and_article_respect_semantic_and_include_metadata(monkeypatch):
    from legalize_cli import mcp_server
    from legalize_cli.github.commits import CommitInfo
    from legalize_cli.laws.asof import ResolvedAsOf
    from legalize_cli.laws.frontmatter import parse as parse_frontmatter
    from legalize_cli.laws.lookup import ResolvedLawFile

    kst = timezone(timedelta(hours=9))
    old_commit = CommitInfo(
        sha="old123",
        author_date=datetime(2025, 1, 1, 12, 0, tzinfo=kst),
        committer_date=datetime(2025, 1, 1, 12, 1, tzinfo=kst),
        message="법령 개정",
    )
    new_commit = CommitInfo(
        sha="new123",
        author_date=datetime(2026, 3, 1, 12, 0, tzinfo=kst),
        committer_date=datetime(2026, 3, 1, 12, 1, tzinfo=kst),
        message="법령 개정",
    )
    old_text = """---
제목: 예시법
법령MST: 100
법령ID: '000100'
공포일자: 2025-01-01
시행일자: 2025-01-01
출처: https://example.test/old
---
##### 제1조 (목적)

이전 규정
"""
    new_text = """---
제목: 예시법
법령MST: 101
법령ID: '000100'
공포일자: 2026-03-01
시행일자: 2026-07-01
출처: https://example.test/new
---
##### 제1조 (목적)

새 규정
"""

    class DummyClient:
        def close(self):
            pass

    def resolved(raw: str, commit: CommitInfo, semantic_date: str) -> ResolvedLawFile:
        fm, _ = parse_frontmatter(raw)
        return ResolvedLawFile(
            resolution=ResolvedAsOf(
                commit=commit,
                frontmatter=fm,
                semantic_date=datetime.fromisoformat(semantic_date).date(),
            ),
            raw=raw.encode(),
        )

    selections = {
        "공포일자": resolved(new_text, new_commit, "2026-03-01"),
        "시행일자": resolved(old_text, old_commit, "2025-01-01"),
    }

    monkeypatch.setattr(mcp_server, "_make_client", lambda: (DummyClient(), None))
    monkeypatch.setattr(
        mcp_server,
        "resolve_law_file_as_of",
        lambda _client, _cache, _path, _target, semantic: selections[semantic],
    )

    announced = json.loads(
        mcp_server.laws_get("예시법", date="2026-04-01", semantic="공포일자")
    )
    effective = json.loads(
        mcp_server.laws_article("예시법", "1", date="2026-04-01", semantic="시행일자")
    )

    assert announced["kind"] == "laws.get"
    assert announced["semantic"] == "공포일자"
    assert announced["frontmatter"]["공포일자"] == "2026-03-01"
    assert announced["frontmatter"]["시행일자"] == "2026-07-01"
    assert announced["warning"]
    assert effective["kind"] == "laws.article"
    assert effective["semantic"] == "시행일자"
    assert effective["공포일자"] == "2025-01-01"
    assert effective["시행일자"] == "2025-01-01"
    assert effective["출처"] == "https://example.test/old"
    assert effective["법령ID"] == "000100"
    assert effective["법령MST"] == 100
    assert "이전 규정" in effective["content"]
    assert effective["file_effective_date_only"] is True
