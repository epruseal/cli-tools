"""``legalize admrules ...`` subcommand group."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from ..config import ADMRULES_REPO
from ..documents import (
    enumerate_documents,
    fetch_document_by_name_or_path,
    filter_and_paginate_documents,
)
from ..laws.frontmatter import parse as parse_frontmatter
from ..util.cli_common import (
    build_global_opts,
    emit_json,
    handle_domain_error,
    make_client,
)
from ..util.errors import LegalizeError

admrules_app = typer.Typer(
    name="admrules",
    help="Operate on the Korean administrative rules mirror (legalize-kr/admrule-kr).",
    no_args_is_help=True,
)


@admrules_app.command("list")
def list_admrules_cmd(
    type_: Optional[str] = typer.Option(
        None, "--type", help="Filter by 행정규칙종류 (고시|훈령|예규|공고|...).",
    ),
    agency: Optional[str] = typer.Option(
        None, "--agency", help="Filter by any 기관경로 segment.",
    ),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(100, "--page-size", min=1, max=500),
    json_output: bool = typer.Option(False, "--json"),
    token: Optional[str] = typer.Option(None, "--token"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    cache_dir: Optional[Path] = typer.Option(None, "--cache-dir"),
    offline: bool = typer.Option(False, "--offline"),
) -> None:
    """List administrative rules from the admrule-kr tree."""
    opts = build_global_opts(token, no_cache, cache_dir, offline, json_output)
    client, cache = make_client(opts)

    try:
        entries = enumerate_documents(client, cache, repo=ADMRULES_REPO)
        total, window, next_page = filter_and_paginate_documents(
            entries,
            category=type_,
            parent_contains=agency,
            page=page,
            page_size=page_size,
        )
    except LegalizeError as exc:
        raise handle_domain_error(exc) from exc
    finally:
        client.close()

    if json_output:
        emit_json(
            {
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": [entry.model_dump() for entry in window],
                "next_page": next_page,
            },
            kind="admrules.list",
        )
        return

    if not window:
        typer.echo(f"(no administrative rules match; total={total})")
        return

    truncate_at = 50
    for entry in window[:truncate_at]:
        typer.echo(f"{entry.category:<8}  {'/'.join(entry.parents):<28}  {entry.name}")
    if len(window) > truncate_at:
        typer.echo(f"... +{len(window) - truncate_at} more (page_size={page_size})")
    typer.echo(f"(page {page}; total={total})")


@admrules_app.command("get")
def get_admrule_cmd(
    identifier: str = typer.Argument(..., metavar="<행정규칙명|path>"),
    type_: Optional[str] = typer.Option(None, "--type", help="Disambiguate by 행정규칙종류."),
    agency: Optional[str] = typer.Option(None, "--agency", help="Disambiguate by 기관경로 segment."),
    json_output: bool = typer.Option(False, "--json"),
    token: Optional[str] = typer.Option(None, "--token"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    cache_dir: Optional[Path] = typer.Option(None, "--cache-dir"),
    offline: bool = typer.Option(False, "--offline"),
) -> None:
    """Fetch one administrative rule by exact name or repository path."""
    opts = build_global_opts(token, no_cache, cache_dir, offline, json_output)
    client, cache = make_client(opts)

    try:
        path, body = fetch_document_by_name_or_path(
            client,
            cache,
            identifier,
            repo=ADMRULES_REPO,
            category=type_,
            parent_contains=agency,
        )
    except LegalizeError as exc:
        raise handle_domain_error(exc) from exc
    finally:
        client.close()

    text = body.decode("utf-8", errors="replace")
    if json_output:
        fm, md_body = parse_frontmatter(text)
        emit_json(
            {
                "identifier": identifier,
                "path": path,
                "frontmatter": fm.model_dump(by_alias=True, exclude_none=True),
                "body": md_body,
            },
            kind="admrules.get",
        )
        return

    typer.echo(text)


__all__ = ["admrules_app", "list_admrules_cmd", "get_admrule_cmd"]
