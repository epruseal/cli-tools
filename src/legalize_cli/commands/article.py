"""``legalize laws article <law> <article-no>`` — point-in-time article extract."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, cast

import typer

from ..laws.articles import parse_articles
from ..laws.asof import Semantic
from ..laws.frontmatter import parse as parse_frontmatter
from ..laws.lookup import resolve_law_file_as_of
from ..laws.model import ArticleNo
from ..util.article_parse import parse_article_query
from ..util.cli_common import (
    build_global_opts,
    emit_json,
    handle_domain_error,
    make_client,
)
from ..util.errors import LegalizeError, NotFoundError
from .list_laws import laws_app


@laws_app.command("article")
def article_cmd(
    law_name: str = typer.Argument(..., metavar="<law-name>"),
    article_no: str = typer.Argument(..., metavar="<article-no>"),
    category: str = typer.Option("법률", "--category"),
    the_date: Optional[str] = typer.Option(None, "--date"),
    semantic: str = typer.Option("공포일자", "--semantic"),
    json_output: bool = typer.Option(False, "--json"),
    token: Optional[str] = typer.Option(None, "--token"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    cache_dir: Optional[Path] = typer.Option(None, "--cache-dir"),
    offline: bool = typer.Option(False, "--offline"),
) -> None:
    """Slice a single article out of a law at a point in time."""
    if semantic not in ("공포일자", "시행일자"):
        raise typer.BadParameter("--semantic must be 공포일자 or 시행일자")

    query: ArticleNo = parse_article_query(article_no)
    target = _parse_date(the_date)
    path = f"kr/{law_name}/{category}.md"

    opts = build_global_opts(token, no_cache, cache_dir, offline, json_output)
    client, cache = make_client(opts)

    try:
        resolved = resolve_law_file_as_of(
            client, cache, path, target, cast(Semantic, semantic)
        )
        if resolved is None:
            raise NotFoundError(
                f"no {semantic} revision at or before {target.isoformat()} for {path}"
            )
    except LegalizeError as exc:
        raise handle_domain_error(exc) from exc
    finally:
        client.close()

    text = resolved.raw.decode("utf-8", errors="replace")
    fm, md_body = parse_frontmatter(text)
    articles = parse_articles(md_body)

    match = next(
        (
            a
            for a in articles
            if a.article_no.jo == query.jo and (a.article_no.ui or None) == (query.ui or None)
        ),
        None,
    )

    if match is None:
        raise handle_domain_error(
            NotFoundError(
                f"article {article_no} not found in {law_name}/{category} at {target.isoformat()}"
            )
        )

    if json_output:
        payload = {
            "law": law_name,
            "category": category,
            "semantic": semantic,
            "requested_date": target.isoformat(),
            "resolved_version_date": resolved.resolution.semantic_date.isoformat(),
            "resolved_commit_date": resolved.resolution.commit.author_date.date().isoformat(),
            "resolved_commit_sha": resolved.resolution.commit.sha,
            "공포일자": fm.promulgation_date,
            "시행일자": fm.enforcement_date,
            "출처": fm.source,
            "법령ID": fm.law_id,
            "법령MST": fm.law_mst,
            "file_effective_date_only": True,
            "article_no": match.article_no.model_dump(by_alias=True),
            "status": match.status,
            "annotations": match.annotations,
            "path": path,
            "content": match.content,
            "parent_structure": match.parent_structure,
        }
        warning = _file_scope_warning(semantic, target, fm.enforcement_date)
        if warning is not None:
            payload["warning"] = warning
        emit_json(payload, kind="laws.article")
        return

    warning = _file_scope_warning(semantic, target, fm.enforcement_date)
    if warning is not None:
        typer.echo(f"warning: {warning}", err=True)
    if match.parent_structure:
        typer.echo(" > ".join(match.parent_structure))
    typer.echo(match.content)


# ---- internals --------------------------------------------------------


def _parse_date(raw: Optional[str]) -> date:
    if raw is None:
        return datetime.now(timezone.utc).astimezone().date()
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"--date must be YYYY-MM-DD ({exc})") from exc


def _file_scope_warning(
    semantic: str, target: date, enforcement_date: Optional[date]
) -> Optional[str]:
    if semantic == "공포일자" and enforcement_date is not None and target < enforcement_date:
        return (
            "선택한 공포일자 버전은 기준일에 아직 시행 전입니다. "
            "시행 중인 파일 버전은 --semantic 시행일자로 조회하세요."
        )
    return None


__all__ = ["article_cmd"]
