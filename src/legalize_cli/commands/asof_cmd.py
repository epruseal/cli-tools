"""``legalize laws as-of`` and ``legalize laws get`` subcommands.

Named with the ``_cmd`` suffix per plan §3 to avoid shadowing
:mod:`legalize_cli.laws.asof`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional, cast

import typer

from ..contracts.requests import LawGetRequest
from ..laws.asof import Semantic
from ..laws.list import enumerate_laws
from ..laws.lookup import resolve_law_file_as_of
from ..services.context import ServiceContext
from ..services.dates import parse_date_or_today
from ..services.laws import get_law
from ..services.date_index import (
    attach_bodies,
    count_git_bodies,
    emit_date_result,
    find_date_result,
    LOOKUP_ERRORS,
    index_failure,
    index_requested,
    law_category,
    load_date_index,
    select_date,
)
from ..util.cli_common import (
    build_global_opts,
    emit_json,
    handle_domain_error,
    make_client,
)
from ..util.errors import InputValidationError, LegalizeError
from .list_laws import laws_app

#: Limit above which a heavy scan without a token must be confirmed.
_DEFAULT_SCAN_LIMIT = 100


@laws_app.command("as-of")
def as_of_cmd(
    the_date: Optional[str] = typer.Option(
        None, "--date", help="ISO date (YYYY-MM-DD); default: today KST.",
    ),
    category: str = typer.Option("all", "--category"),
    include_repealed: bool = typer.Option(False, "--include-repealed"),
    semantic: str = typer.Option("공포일자", "--semantic"),
    date_index: Optional[Path] = typer.Option(
        None, "--date-index",
        help="Compiler date index. Without it (and without LEGALIZE_LAWS_DATE_INDEX_PATH) the default lookup is used.",
    ),
    limit: Optional[int] = typer.Option(None, "--limit"),
    yes_exhaust: bool = typer.Option(False, "--yes-exhaust-quota"),
    json_output: bool = typer.Option(False, "--json"),
    token: Optional[str] = typer.Option(None, "--token"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    cache_dir: Optional[Path] = typer.Option(None, "--cache-dir"),
    offline: bool = typer.Option(False, "--offline"),
) -> None:
    """List law files selected as of a given date."""
    if semantic not in ("공포일자", "시행일자"):
        raise typer.BadParameter("--semantic must be 공포일자 or 시행일자")

    target = _parse_date(the_date)
    opts = build_global_opts(token, no_cache, cache_dir, offline, json_output)
    client, cache = make_client(opts)

    if index_requested("laws", date_index):
        _as_of_indexed(client, opts, target, semantic, category, include_repealed, limit, yes_exhaust, date_index, json_output)
        return

    results = []
    try:
        laws = enumerate_laws(client, cache)
        if category != "all":
            laws = [e for e in laws if e.category == category]

        if limit is not None:
            laws = laws[:limit]

        _preflight_budget(
            client,
            candidates=len(laws),
            yes_exhaust=yes_exhaust,
            token_present=bool(opts.token) or client.token_source != "none",
            default_limit=_DEFAULT_SCAN_LIMIT,
            limit=limit,
        )

        for entry in laws:
            resolved = resolve_law_file_as_of(
                client, cache, entry.path, target, cast(Semantic, semantic)
            )
            if resolved is None:
                continue
            if not include_repealed and resolved.resolution.frontmatter.status == "폐지":
                continue
            results.append(
                {
                    "name": entry.name,
                    "category": entry.category,
                    "path": entry.path,
                    "resolved_version_date": resolved.resolution.semantic_date.isoformat(),
                    "resolved_commit_date": resolved.resolution.commit.author_date.date().isoformat(),
                    "resolved_commit_sha": resolved.resolution.commit.sha,
                }
            )
    except LegalizeError as exc:
        raise handle_domain_error(exc) from exc
    finally:
        client.close()

    if json_output:
        emit_json(
            {
                "requested_date": target.isoformat(),
                "semantic": semantic,
                "items": results,
                "total": len(results),
            },
            kind="laws.asof",
        )
        return

    for row in results:
        typer.echo(f"{row['category']:<12}  {row['name']:<28}  {row['resolved_version_date']}")
    typer.echo(f"(total={len(results)} as-of {target.isoformat()} ({semantic}))")


@laws_app.command("get")
def get_law_cmd(
    ctx: typer.Context,
    law_name: str = typer.Argument(..., metavar="<law-name>"),
    category: str = typer.Option("법률", "--category"),
    the_date: Optional[str] = typer.Option(None, "--date"),
    semantic: str = typer.Option("공포일자", "--semantic"),
    date_index: Optional[Path] = typer.Option(
        None, "--date-index",
        help="Compiler date index. Without it (and without LEGALIZE_LAWS_DATE_INDEX_PATH) the default lookup is used.",
    ),
    json_output: bool = typer.Option(False, "--json"),
    token: Optional[str] = typer.Option(None, "--token"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    cache_dir: Optional[Path] = typer.Option(None, "--cache-dir"),
    offline: bool = typer.Option(False, "--offline"),
) -> None:
    """Fetch one law's markdown at a given date."""
    semantic_given = _option_given(ctx, "semantic")
    use_index = index_requested("laws", date_index, date_option_given=the_date is not None or semantic_given)
    if semantic not in ("공포일자", "시행일자"):
        raise typer.BadParameter("--semantic must be 공포일자 or 시행일자")

    target = _parse_date(the_date)
    opts = build_global_opts(token, no_cache, cache_dir, offline, json_output)
    client, cache = make_client(opts)

    if use_index:
        _get_indexed(client, target, semantic, law_name, category, _option_given(ctx, "category"), date_index, json_output)
        return

    try:
        loaded = get_law(
            ServiceContext(client, cache),
            LawGetRequest(law_name=law_name, category=category, date=target.isoformat(), semantic=cast(Semantic, semantic)),
        )
    except LegalizeError as exc:
        raise handle_domain_error(exc) from exc
    finally:
        client.close()

    result = loaded.result
    text = loaded.raw_text
    fm = result.frontmatter
    md_body = result.body
    warning = _file_scope_warning(semantic, target, fm.enforcement_date)

    if json_output:
        payload = {
            "law": result.law,
            "category": result.category,
            "semantic": semantic,
            "requested_date": target.isoformat(),
            "resolved_version_date": result.version.resolved_version_date.isoformat(),
            "resolved_commit_date": result.version.resolved_commit_date.isoformat(),
            "resolved_commit_sha": result.source.ref,
            "path": result.source.path,
            "frontmatter": fm.model_dump(by_alias=True, exclude_none=True),
            "file_effective_date_only": True,
            "body": md_body,
        }
        if warning is not None:
            payload["warning"] = warning
        emit_json(payload, kind="laws.get")
        return

    if warning is not None:
        typer.echo(f"warning: {warning}", err=True)
    typer.echo(text)


def _as_of_indexed(client, opts, target, semantic, category, include_repealed, limit, yes_exhaust, date_index, json_output) -> None:
    """Date-index lookup: selected, confirmed, candidates, undated and the separated statuses."""
    try:
        try:
            index = load_date_index(client, "legalize-kr", "laws", date_index)
            results = select_date(index, target, semantic)
            if category != "all":
                results = [
                    row for row in results
                    if any(law_category(item) == category
                           for item in ([row["selected"]] if row["selected"] else []) + row["candidates"] + row["undated"])
                ]
            if not include_repealed:
                results = [row for row in results if row["as_of_status"] != "repealed"]
            if limit is not None:
                results = results[:limit]
        except LOOKUP_ERRORS as exc:
            raise handle_domain_error(index_failure(exc, "laws")) from exc
        # Same call-budget check as the existing path, estimated from the git bodies still to fetch.
        # It is a guard of the command with its own advice, so it keeps its text.
        try:
            _preflight_budget(
                client,
                candidates=count_git_bodies(index, results),
                yes_exhaust=yes_exhaust,
                token_present=bool(opts.token) or client.token_source != "none",
                default_limit=_DEFAULT_SCAN_LIMIT,
                limit=limit,
            )
        except LegalizeError as exc:
            raise handle_domain_error(exc) from exc
        try:
            attach_bodies(index, results)
        except LOOKUP_ERRORS as exc:
            raise handle_domain_error(index_failure(exc, "laws")) from exc
    finally:
        client.close()

    if json_output:
        emit_json(
            {
                "requested_date": target.isoformat(),
                "semantic": semantic,
                "items": results,
                "total": len(results),
            },
            kind="laws.asof",
        )
        return

    for row in results:
        typer.echo(_text_row(row, semantic))
    typer.echo(f"(total={len(results)} as-of {target.isoformat()} ({semantic}))")


def _text_row(row, semantic) -> str:
    """The existing three columns (category, name, version date), then the as-of status of the index."""
    item = row["selected"] or (row["candidates"] or row["undated"])[0]
    chosen = row["selected"]["occurrence"] if row["selected"] else None
    day = (chosen["promulgation_date"] if semantic == "공포일자" else chosen["enforcement_date"]) if chosen else None
    return f"{law_category(item):<12}  {item['name']:<28}  {day or '-':<10}  {row['as_of_status']}"


def _get_indexed(client, target, semantic, law_name, category, category_given, date_index, json_output) -> None:
    try:
        # The same normalisation as the existing path: a path or a 시행령/시행규칙 title suffix selects the category.
        try:
            request = LawGetRequest(law_name=law_name, category=category)
        except ValueError as exc:
            raise handle_domain_error(InputValidationError("법령명 또는 전체 경로와 법령 구분을 확인하세요.")) from exc
        try:
            index = load_date_index(client, "legalize-kr", "laws", date_index)
            result = find_date_result(index, target, semantic, request.law_name, request.category,
                                      category_explicit=category_given)
        except LOOKUP_ERRORS as exc:
            raise handle_domain_error(index_failure(exc, "laws")) from exc
    finally:
        client.close()
    emit_date_result(result, json_output, "laws.get")


# ---- internals --------------------------------------------------------


def _option_given(ctx: typer.Context, name: str) -> bool:
    """True when the option came from the command line or the environment, not from its default.

    Typer may ship its own click copy, so the source is compared by enum name instead of by class.
    """
    source = ctx.get_parameter_source(name)
    return source is not None and source.name != "DEFAULT"


def _parse_date(raw: Optional[str]) -> date:
    try:
        return parse_date_or_today(raw)
    except LegalizeError as exc:
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


def _preflight_budget(
    client,
    *,
    candidates: int,
    yes_exhaust: bool,
    token_present: bool,
    default_limit: int,
    limit: Optional[int],
) -> None:
    """Rate-limit pre-flight for scans that fetch N contents at once.

    Refuses if (a) no token AND candidates > default_limit AND user did not
    pass --limit or --yes-exhaust-quota, OR (b) remaining quota is insufficient
    for the minimum ``2 × candidates`` request estimate. ``시행일자`` can read
    more candidate frontmatters, so its actual request count may be higher.
    """
    if not token_present and candidates > default_limit and not yes_exhaust and limit is None:
        raise LegalizeError(
            f"would scan {candidates} laws without a token "
            f"(default limit is {default_limit}); "
            "pass --limit N, --yes-exhaust-quota, or set GITHUB_TOKEN"
        )

    rl = getattr(client, "last_rate_limit", None)
    if rl is None:
        return
    needed = 2 * candidates
    if needed > rl.remaining and not yes_exhaust:
        raise LegalizeError(
            f"would exceed rate-limit budget (need {needed}, have {rl.remaining}); "
            "pass --yes-exhaust-quota to proceed or set GITHUB_TOKEN"
        )


__all__ = ["as_of_cmd", "get_law_cmd"]
