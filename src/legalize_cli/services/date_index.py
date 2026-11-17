from __future__ import annotations

import hashlib
import json
import os
import re
from contextvars import ContextVar
from datetime import date
from pathlib import Path

import httpx

from ..config import OWNER
from ..documents import BODY_FILENAME, TreeDocumentEntry, _matches_document
from ..github.contents import get_file_raw
from ..github.refs import resolve_default_branch_head
from ..util.errors import AmbiguousMatchError, LegalizeError, NotFoundError


#: Dataset of the lookup in progress. The two public entry points set it, so
#: every error built below picks its generate/configure hint in one place.
_DATASET: ContextVar[str | None] = ContextVar("date_index_dataset", default=None)


class DateIndexError(LegalizeError):
    """Date index problem. The single constructor appends the generate/configure hint.

    ``remote=True`` only classifies an error that comes from a downstream remote result
    (for example a body commit that is not reachable). It does not remove the hint.
    Failures of the backend (a missing file, a rate limit, a network error) are not index
    errors; the commands add the same hint to them with :func:`index_failure`.
    """

    def __init__(self, code: str, message: str, *, remote: bool = False) -> None:
        self.remote = remote
        dataset = _DATASET.get()
        hint = "" if dataset is None else f".{_guidance(dataset)}"
        super().__init__(f"{code}: {message}{hint}")


class NoResultError(NotFoundError):
    """The lookup ran on a valid index and found nothing. A valid answer, not a failure."""


# Errors a dated command can meet while it reads or verifies the index, the source and the bodies.
LOOKUP_ERRORS = (LegalizeError, httpx.HTTPError)


def index_failure(exc: Exception, dataset: str) -> LegalizeError:
    """Return ``exc`` with the generate/fallback hint added when the lookup failed.

    The original message and exit code stay. A valid answer (an ambiguous identifier or no result)
    and an error that already carries the hint get no hint. A network error becomes a
    ``LegalizeError`` (exit code 1, as the unhandled error did) with its text kept.
    """
    if isinstance(exc, (DateIndexError, AmbiguousMatchError, NoResultError)):
        return exc
    if isinstance(exc, LegalizeError):
        exc.args = (f"{exc}.{_guidance(dataset)}",)
        return exc
    return LegalizeError(f"{type(exc).__name__}: {exc}.{_guidance(dataset)}")


def _invalid(message: str, *, remote: bool = False) -> DateIndexError:
    return DateIndexError("DATE_INDEX_INVALID", message, remote=remote)


def _source_line(exc: BaseException) -> int:
    """Line in this module that raised ``exc``; the check that failed."""
    line = 0
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == __file__:
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


def _date(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise _invalid("invalid date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise _invalid("invalid date") from exc
    if parsed.isoformat() != value:
        raise _invalid("invalid date")
    return value


_BOTH_SEMANTICS = (["공포일자", "시행일자"], ["시행일자", "공포일자"])


def _validate_undated_semantics(occ, status, promulgation):
    """Check the optional `undated_semantics` key. Absent means an empty list (old indexes)."""
    if "undated_semantics" not in occ:
        return
    value = occ["undated_semantics"]
    if status == "undated":
        allowed = _BOTH_SEMANTICS
    elif promulgation is None:
        allowed = (["공포일자"],)
    else:
        allowed = ([],)
    if not isinstance(value, list) or value not in allowed:
        raise _invalid("invalid undated_semantics for the occurrence status and dates")


_COMPILERS = {
    "laws": "legalize-kr-compiler",
    "admrules": "admrule-kr-compiler",
    "ordinances": "ordinance-kr-compiler",
}


def _guidance(dataset: str) -> str:
    env = f"LEGALIZE_{dataset.upper()}_DATE_INDEX_PATH"
    text = (
        f" Generate the index with `{_COMPILERS[dataset]} --commit-date-basis promulgation|enforcement`: "
        "the default file is <output>.date-index.json, or pass --manifest PATH "
        "(required for enforcement) to embed it in the manifest. "
        f"Then pass --date-index PATH or set {env}."
    )
    if dataset == "laws":
        # Only laws have a default lookup to fall back to; admrules and ordinances dated lookups need the index.
        text += (
            f" To use the default lookup instead, omit --date-index and unset {env}."
            " The default lookup can omit revisions whose enforcement date is not determined."
        )
    return text


def index_requested(dataset: str, path: Path | None, *, date_option_given: bool = True) -> bool:
    """True when the caller chose the date-index lookup.

    The index is used when ``--date-index`` or the environment path is set, but only for a dated command:
    a ``get`` without ``--date`` and ``--semantic`` keeps the existing single-document path and ignores both.
    """
    if not date_option_given:
        return False
    return path is not None or bool(os.environ.get(f"LEGALIZE_{dataset.upper()}_DATE_INDEX_PATH"))


def _required(message: str) -> DateIndexError:
    return DateIndexError("DATE_INDEX_REQUIRED", message)


def _unique_object(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate object key")
    return dict(pairs)


def _reject_constant(_name):
    raise ValueError("non-standard JSON constant")


def _read_index_file(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise _required(f"date index file not found at {path}") from exc
    except OSError as exc:
        raise _invalid(f"cannot read {path} ({type(exc).__name__}, errno {exc.errno})") from exc
    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except UnicodeDecodeError as exc:
        raise _invalid(f"{path} is not valid UTF-8 (byte offset {exc.start})") from exc
    except json.JSONDecodeError as exc:
        raise _invalid(f"{path} is not strict JSON (line {exc.lineno}, column {exc.colno})") from exc
    except ValueError as exc:
        raise _invalid(f"{path} is not strict JSON ({exc})") from exc


def load_date_index(client, repo: str, dataset: str, path: Path | None):
    token = _DATASET.set(dataset)
    try:
        return _load_date_index(client, repo, dataset, path)
    finally:
        _DATASET.reset(token)


def _load_date_index(client, repo: str, dataset: str, path: Path | None):
    env = f"LEGALIZE_{dataset.upper()}_DATE_INDEX_PATH"
    if path is None:
        configured = os.environ.get(env)
        path = Path(configured).expanduser() if configured else None
    if path is None:
        raise _required(f"no date index path was specified (--date-index or {env})")
    payload = _read_index_file(path)
    try:
        index = payload["date_index"]
        if not isinstance(index, dict) or type(index["schema_version"]) is not int or index["schema_version"] != 1:
            raise _invalid("unsupported schema")
        if index["basis"] not in ("promulgation", "enforcement") or index["dataset"] != dataset:
            raise _invalid("basis or dataset mismatch")
        head = index["head_commit_sha"]
        if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
            raise _invalid("invalid HEAD")
        if resolve_default_branch_head(client, OWNER, repo) != head:
            raise _invalid("HEAD mismatch")
        revisions = index["revisions"]
        if not isinstance(revisions, list) or not isinstance(index["snapshots"], list):
            raise _invalid("invalid revisions or snapshots")
        ranks = {"promulgation_rank": set(), "enforcement_rank": set()}
        rank_dates = {"promulgation_rank": [], "enforcement_rank": []}
        identities = set()
        for snapshot in index["snapshots"]:
            if _date(snapshot["snapshot_date"]) is None or not re.fullmatch(r"[0-9a-f]{40}", snapshot["commit_sha"]):
                raise _invalid("invalid snapshot")
        for rev in revisions:
            for key in ("identity", "revision", "copy_id", "name", "category", "canonical_path", "reason"):
                if not isinstance(rev[key], str) or not rev[key]:
                    raise _invalid(f"invalid {key}")
            if not re.fullmatch(r"[0-9a-f]{64}", rev["copy_id"]) or type(rev["repeal"]) is not bool:
                raise _invalid("invalid copy_id or repeal")
            key = (rev["identity"], rev["revision"], rev["copy_id"])
            if key in identities:
                raise _invalid("duplicate revision copy")
            identities.add(key)
            if not isinstance(rev["date_evidence"], dict):
                raise _invalid("invalid date evidence")
            if not isinstance(rev["occurrences"], list) or not rev["occurrences"]:
                raise _invalid("missing occurrences")
            body = rev["body"]
            if not isinstance(body, dict) or not isinstance(body["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", body["sha256"]):
                raise _invalid("invalid body reference")
            if body["kind"] == "inline":
                if not isinstance(body["text"], str):
                    raise _invalid("invalid inline body")
                # Inline bytes cost no network call, so they are verified for every revision at load time.
                if hashlib.sha256(body["text"].encode("utf-8")).hexdigest() != body["sha256"]:
                    raise _invalid("body hash mismatch")
            elif body["kind"] == "git":
                commit, blob, body_path = body["commit_sha"], body["blob_sha"], body["path"]
                if not all(isinstance(v, str) for v in (commit, blob, body_path)) or not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[0-9a-f]{40}", blob):
                    raise _invalid("invalid git body reference")
            else:
                raise _invalid("invalid body kind")
            for occ in rev["occurrences"]:
                status = occ["status"]
                if status not in ("confirmed", "estimated", "unresolved", "undated"):
                    raise _invalid("invalid occurrence status")
                for key in ("source_path", "reason"):
                    if not isinstance(occ[key], str) or not occ[key]:
                        raise _invalid(f"invalid occurrence {key}")
                for key in ("raw_promulgation_date", "raw_enforcement_date"):
                    if occ[key] is not None and not isinstance(occ[key], str):
                        raise _invalid(f"invalid {key}")
                if not isinstance(occ["date_evidence"], dict) or not isinstance(occ["observed_metadata"], dict):
                    raise _invalid("invalid occurrence metadata")
                promulgation = _date(occ["promulgation_date"])
                enforcement = _date(occ["enforcement_date"])
                exposure = _date(occ["exposure_date"])
                if status in ("confirmed", "estimated"):
                    valid_dates = enforcement is not None and exposure is None
                elif status == "unresolved":
                    valid_dates = enforcement is None and promulgation is not None and exposure == promulgation
                else:
                    valid_dates = promulgation is None and enforcement is None and exposure is None
                if not valid_dates:
                    raise _invalid("occurrence status conflicts with dates")
                _validate_undated_semantics(occ, status, promulgation)
                event = enforcement if status in ("confirmed", "estimated") else exposure if status == "unresolved" else None
                for key, event_date in (("promulgation_rank", promulgation), ("enforcement_rank", event)):
                    rank = occ[key]
                    if event_date is None:
                        if rank is not None:
                            raise _invalid(f"unexpected {key}")
                    elif type(rank) is not int or not 0 <= rank <= 9007199254740991 or rank in ranks[key]:
                        raise _invalid(f"invalid or duplicate {key}")
                    else:
                        ranks[key].add(rank)
                        rank_dates[key].append((rank, event_date))
            for field in ("promulgation_date", "enforcement_date", "status"):
                values = [occ[field] for occ in rev["occurrences"]]
                expected = values[0] if all(value == values[0] for value in values) else None
                if rev[field] != expected:
                    raise _invalid(f"inconsistent revision summary {field}")
        for values in rank_dates.values():
            dates = [event for _, event in sorted(values)]
            if dates != sorted(dates):
                raise _invalid("rank order conflicts with dates")
        # Git body reachability, blob SHA and SHA-256 are verified lazily for returned bodies only.
        index["_lazy"] = {"client": client, "repo": repo, "head": head, "texts": {}, "reach": {},
                          "by_rev": {(r["identity"], r["revision"], r["copy_id"]): r for r in revisions}}
        return index
    except (ValueError, KeyError, TypeError, UnicodeError, AttributeError) as exc:
        # Downstream remote errors (OSError, NotFoundError, ...) are not caught
        # here and keep their own classification. The text below carries only
        # the error kind and the source line, never an input key or value.
        raise _invalid(f"malformed index structure ({type(exc).__name__} at line {_source_line(exc)})") from exc


def _verified_text(index, key):
    """Fetch and verify one revision body (reachability, blob SHA, SHA-256) with the same error classes as before."""
    lazy = index["_lazy"]
    if key in lazy["texts"]:
        return lazy["texts"][key]
    body = lazy["by_rev"][key]["body"]
    if body["kind"] == "inline":
        raw = body["text"].encode("utf-8")
    else:
        client, repo, head, commit = lazy["client"], lazy["repo"], lazy["head"], body["commit_sha"]
        if commit not in lazy["reach"]:
            comparison = client.get_json(f"/repos/{OWNER}/{repo}/compare/{commit}...{head}")
            lazy["reach"][commit] = comparison.get("status") in ("ahead", "identical")
        if not lazy["reach"][commit]:
            raise _invalid("body commit is not reachable from HEAD", remote=True)
        raw = get_file_raw(client, OWNER, repo, body["path"], ref=commit)
        if hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != body["blob_sha"]:
            raise _invalid("body blob mismatch")
    if hashlib.sha256(raw).hexdigest() != body["sha256"]:
        raise _invalid("body hash mismatch")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid("body is not valid UTF-8") from exc
    lazy["texts"][key] = text
    return text


def count_git_bodies(index, rows) -> int:
    """Number of distinct git bodies the rows would still fetch; the as-of call budget is estimated from it."""
    lazy = index["_lazy"]
    keys = set()
    for row in rows:
        for item in [row["selected"], row["confirmed"], *row["candidates"], *row["undated"]]:
            if item is None:
                continue
            key = (item["identity"], item["revision"], item["copy_id"])
            if key not in lazy["texts"] and lazy["by_rev"][key]["body"]["kind"] == "git":
                keys.add(key)
    return len(keys)


def attach_bodies(index, rows):
    """Verify and attach bodies for exactly the revisions the rows return (selected, confirmed, candidates, undated)."""
    token = _DATASET.set(index["dataset"])
    try:
        for row in rows:
            items = [row["selected"], row["confirmed"], *row["candidates"], *row["undated"]]
            for item in items:
                if item is not None and "body" not in item:
                    item["body"] = _verified_text(index, (item["identity"], item["revision"], item["copy_id"]))
    finally:
        _DATASET.reset(token)
    return rows


def select_date(index, target: date, semantic: str):
    target_string = target.isoformat()
    grouped = {}
    for rev in index["revisions"]:
        for occ in rev["occurrences"]:
            item = {key: value for key, value in rev.items() if key not in ("occurrences", "body")}
            item["occurrence"] = occ
            group = grouped.setdefault(rev["identity"], {"versions": [], "candidates": [], "undated": []})
            if occ["status"] == "undated" or semantic in occ.get("undated_semantics", ()):
                group["undated"].append(item)
            if semantic == "공포일자":
                event, rank = occ["promulgation_date"], occ["promulgation_rank"]
                if event is not None and event <= target_string:
                    group["versions"].append((rank, item))
            elif occ["status"] in ("confirmed", "estimated"):
                event, rank = occ["enforcement_date"], occ["enforcement_rank"]
                if event is not None and event <= target_string:
                    group["versions"].append((rank, item))
            elif occ["status"] == "unresolved" and occ["exposure_date"] is not None and occ["exposure_date"] <= target_string:
                group["candidates"].append(item)
    results = []
    for identity, group in grouped.items():
        chosen = max(group["versions"], default=(None, None), key=lambda x: x[0])[1]
        resolution = chosen["occurrence"]["status"] if chosen else "unresolved" if group["candidates"] else "undated"
        enforcement = chosen["occurrence"]["enforcement_date"] if chosen else None
        if resolution in ("unresolved", "undated") or enforcement is None:
            as_of_status = "uncertain"
        elif enforcement > target_string:
            as_of_status = "not_yet_effective"
        elif chosen["repeal"]:
            as_of_status = "repealed"
        elif resolution == "estimated":
            as_of_status = "estimated"
        else:
            as_of_status = "effective"
        confirmed = None if as_of_status == "repealed" and resolution == "confirmed" else chosen
        if chosen or group["candidates"] or group["undated"]:
            results.append({"identity": identity, "semantic": semantic, "requested_date": target_string, "basis": index["basis"], "selected": chosen, "confirmed": confirmed, "candidates": group["candidates"], "undated": group["undated"], "date_resolution_status": resolution, "as_of_status": as_of_status})
    return results


def find_date_result(index, target, semantic, identifier, category=None, parents=(), *, category_explicit=True):
    """Find the one identity ``identifier`` names at ``target``.

    For laws a 법령ID already names one family file, so a category that the caller did not choose
    (``category_explicit=False``, the option default) does not filter an identity match; a name still does.
    """
    token = _DATASET.set(index["dataset"])
    try:
        return _find_date_result(index, target, semantic, identifier, category, parents, category_explicit)
    finally:
        _DATASET.reset(token)


_LAW_FILE = re.compile(r"^kr/(?P<dir>[^/]+)/(?P<category>법률|시행령|시행규칙|대통령령)(?:\([^/]+\))?\.md$")


def law_category(item) -> str:
    """The category of a law as the existing arguments use it: the file name in ``kr/{name}/{category}.md``.

    The index ``category`` field is the original 법종구분, which is a different vocabulary, so it is only
    the fallback for a path that does not follow the repository layout.
    """
    match = _LAW_FILE.match(item["canonical_path"])
    return match["category"] if match else item["category"]


def _law_name_matches(identifier: str, item) -> bool:
    match = _LAW_FILE.match(item["canonical_path"])
    return identifier in (item["name"], item["canonical_path"]) or bool(match and identifier.replace(" ", "") == match["dir"])


def _find_date_result(index, target, semantic, identifier, category, parents, category_explicit):
    results = select_date(index, target, semantic)
    exact_identity = any(rev["identity"] == identifier for rev in index["revisions"])
    def accepts(item) -> bool:
        laws = index["dataset"] == "laws"
        if not exact_identity:
            named = _law_name_matches(identifier, item) if laws else identifier in (item["name"], item["canonical_path"])
            if not named:
                return False
        if laws:
            if exact_identity and not category_explicit:
                return True
            return not category or law_category(item) == category
        path_parts = item["canonical_path"].split("/")
        if len(path_parts) < 4 or path_parts[-1] != BODY_FILENAME:
            raise _invalid("invalid document canonical path")
        entry = TreeDocumentEntry(path=item["canonical_path"], name=item["name"],
                                  category=item["category"], parents=path_parts[:-3])
        if index["dataset"] == "admrules":
            parent_contains, parent0, parent1 = parents[0], None, None
        else:
            parent_contains, parent0, parent1 = None, parents[0], parents[1]
        return _matches_document(entry, category=category, parent_contains=parent_contains,
                                 parent0=parent0, parent1=parent1)

    matches = []
    for row in results:
        if exact_identity and row["identity"] != identifier:
            continue
        dated = ([row["selected"]] if row["selected"] else []) + row["candidates"]
        # A revision with no usable date is still found by its name or path when no dated revision matches.
        pools = [dated + row["undated"]] if exact_identity else [dated, row["undated"]]
        if any(accepts(item) for pool in pools for item in pool):
            matches.append(row)
    if len(matches) > 1:
        raise AmbiguousMatchError(f"ambiguous identifier: {identifier}", sorted(row["identity"] for row in matches))
    if not matches:
        raise NoResultError(f"no dated result for {identifier}")
    attach_bodies(index, [matches[0]])
    return matches[0]


def emit_date_result(result, json_output, kind):
    from ..util.cli_common import emit_json
    import typer

    if json_output:
        emit_json(result, kind=kind)
    else:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
