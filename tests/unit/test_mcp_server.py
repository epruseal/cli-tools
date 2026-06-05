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


def test_laws_get_serializes_frontmatter_dates(monkeypatch):
    from legalize_cli import mcp_server
    from legalize_cli.github.commits import CommitInfo

    kst = timezone(timedelta(hours=9))
    commit = CommitInfo(
        sha="abc123",
        author_date=datetime(2026, 3, 17, 12, 0, tzinfo=kst),
        committer_date=datetime(2026, 3, 17, 12, 1, tzinfo=kst),
        message="법령 개정",
    )
    law_text = """---
제목: 민법
공포일자: 2026-03-17
시행일자: 2026-03-17
---
# 민법
"""

    class DummyClient:
        def close(self):
            pass

    monkeypatch.setattr(mcp_server, "_make_client", lambda: (DummyClient(), None))
    monkeypatch.setattr(
        mcp_server, "get_revisions", lambda _client, _cache, _path: [commit]
    )
    monkeypatch.setattr(
        mcp_server,
        "get_file_raw",
        lambda _client, _owner, _repo, _path, ref: law_text.encode(),
    )

    payload = json.loads(mcp_server.laws_get("민법", date="2026-06-05"))

    assert payload["kind"] == "laws.get"
    assert payload["frontmatter"]["공포일자"] == "2026-03-17"
    assert payload["frontmatter"]["시행일자"] == "2026-03-17"
