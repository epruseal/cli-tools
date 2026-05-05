"""``legalize ordinances ...`` subcommand group."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from ..config import ORDINANCES_REPO
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

ordinances_app = typer.Typer(
    name="ordinances",
    help="Operate on the Korean local ordinances mirror (legalize-kr/ordinance-kr).",
    no_args_is_help=True,
)


@ordinances_app.command("list")
def list_ordinances_cmd(
    type_: Optional[str] = typer.Option(
        None, "--type", help="Filter by 자치법규종류 (조례|규칙|훈령|예규|고시|...).",
    ),
    jurisdiction: Optional[str] = typer.Option(
        None, "--jurisdiction", help="Filter by 광역자치단체.",
    ),
    subdivision: Optional[str] = typer.Option(
        None, "--subdivision", help="Filter by 기초자치단체, _본청, or _교육청.",
    ),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(100, "--page-size", min=1, max=500),
    json_output: bool = typer.Option(False, "--json"),
    token: Optional[str] = typer.Option(None, "--token"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    cache_dir: Optional[Path] = typer.Option(None, "--cache-dir"),
    offline: bool = typer.Option(False, "--offline"),
) -> None:
    """List local ordinances from the ordinance-kr tree."""
    opts = build_global_opts(token, no_cache, cache_dir, offline, json_output)
    client, cache = make_client(opts)

    try:
        entries = enumerate_documents(client, cache, repo=ORDINANCES_REPO)
        total, window, next_page = filter_and_paginate_documents(
            entries,
            category=type_,
            parent0=jurisdiction,
            parent1=subdivision,
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
            kind="ordinances.list",
        )
        return

    if not window:
        typer.echo(f"(no ordinances match; total={total})")
        return

    truncate_at = 50
    for entry in window[:truncate_at]:
        place = "/".join(entry.parents[:2])
        typer.echo(f"{entry.category:<8}  {place:<22}  {entry.name}")
    if len(window) > truncate_at:
        typer.echo(f"... +{len(window) - truncate_at} more (page_size={page_size})")
    typer.echo(f"(page {page}; total={total})")


@ordinances_app.command("get")
def get_ordinance_cmd(
    identifier: str = typer.Argument(..., metavar="<자치법규명|path>"),
    type_: Optional[str] = typer.Option(None, "--type", help="Disambiguate by 자치법규종류."),
    jurisdiction: Optional[str] = typer.Option(None, "--jurisdiction", help="Disambiguate by 광역자치단체."),
    subdivision: Optional[str] = typer.Option(None, "--subdivision", help="Disambiguate by 기초자치단체."),
    json_output: bool = typer.Option(False, "--json"),
    token: Optional[str] = typer.Option(None, "--token"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    cache_dir: Optional[Path] = typer.Option(None, "--cache-dir"),
    offline: bool = typer.Option(False, "--offline"),
) -> None:
    """Fetch one local ordinance by exact name or repository path."""
    opts = build_global_opts(token, no_cache, cache_dir, offline, json_output)
    client, cache = make_client(opts)

    try:
        path, body = fetch_document_by_name_or_path(
            client,
            cache,
            identifier,
            repo=ORDINANCES_REPO,
            category=type_,
            parent0=jurisdiction,
            parent1=subdivision,
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
            kind="ordinances.get",
        )
        return

    typer.echo(text)


__all__ = ["ordinances_app", "list_ordinances_cmd", "get_ordinance_cmd"]
