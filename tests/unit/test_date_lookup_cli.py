"""CLI tests for the date lookups: ``laws as-of``, and ``get --date/--semantic`` for the three corpora."""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from legalize_cli.__main__ import app
from legalize_cli.util.errors import LegalizeError, NotFoundError, RateLimitError

from ._date_index_support import FailingGitHub, FakeGitHub, build_index, spec, write_index

runner = CliRunner()
MODULES = {"laws": "asof_cmd", "admrules": "list_admrules", "ordinances": "list_ordinances"}
LAWS = [
    spec("L", "A", p="2000-01-01", e="2000-01-01"),
    spec("L", "B", "unresolved", p="2005-01-01"),
    spec("L", "C", p="2010-01-01", e="2030-01-01"),
    spec("M", "R", p="1990-01-01", e="1990-01-01", repeal=True, name="gone", category="대통령령"),
]


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for dataset in ("LAWS", "ADMRULES", "ORDINANCES"):
        monkeypatch.delenv(f"LEGALIZE_{dataset}_DATE_INDEX_PATH", raising=False)


def _run(monkeypatch, tmp_path, corpus, specs, *argv, basis="enforcement"):
    fake = FakeGitHub()
    index = write_index(tmp_path, build_index(fake, corpus, basis, specs))
    monkeypatch.setattr(f"legalize_cli.commands.{MODULES[corpus]}.make_client", lambda opts: (fake, None))
    tail = ["--date-index", str(index), "--no-cache", "--json"]
    result = runner.invoke(app, [corpus, *argv, *tail])
    return fake, result


def test_laws_get_returns_the_dated_selection(monkeypatch, tmp_path):
    _, result = _run(monkeypatch, tmp_path, "laws", LAWS, "get", "L", "--date", "2006-01-01", "--semantic", "시행일자")
    row = json.loads(result.stdout)
    assert result.exit_code == 0 and row["kind"] == "laws.get"
    assert row["selected"]["revision"] == "A" and row["confirmed"]["revision"] == "A"
    assert [c["revision"] for c in row["candidates"]] == ["B"] and row["undated"] == []
    assert (row["date_resolution_status"], row["as_of_status"]) == ("confirmed", "effective")


def test_laws_get_lists_a_broken_promulgation_date_only_in_promulgation_lookups(monkeypatch, tmp_path):
    specs = [spec("U", "496220", p=None, e="1999-01-01", semantics=["공포일자"])]
    _, result = _run(monkeypatch, tmp_path, "laws", specs, "get", "U", "--date", "2020-01-01", "--semantic", "공포일자")
    row = json.loads(result.stdout)
    assert result.exit_code == 0 and row["selected"] is None and [u["revision"] for u in row["undated"]] == ["496220"]
    assert (row["date_resolution_status"], row["as_of_status"]) == ("undated", "uncertain")
    _, result = _run(monkeypatch, tmp_path, "laws", specs, "get", "U", "--date", "2020-01-01", "--semantic", "시행일자")
    row = json.loads(result.stdout)
    assert row["selected"]["revision"] == "496220" and row["undated"] == []
    assert (row["date_resolution_status"], row["as_of_status"]) == ("confirmed", "effective")


def test_laws_get_promulgation_semantic_marks_a_future_enforcement(monkeypatch, tmp_path):
    _, result = _run(monkeypatch, tmp_path, "laws", LAWS, "get", "L", "--date", "2020-01-01", "--semantic", "공포일자")
    row = json.loads(result.stdout)
    assert row["selected"]["revision"] == "C" and row["as_of_status"] == "not_yet_effective"


def test_as_of_lists_only_visible_rows_and_attaches_their_bodies(monkeypatch, tmp_path):
    fake, result = _run(monkeypatch, tmp_path, "laws", LAWS, "as-of", "--date", "2006-01-01", "--semantic", "시행일자")
    payload = json.loads(result.stdout)
    assert result.exit_code == 0 and [r["identity"] for r in payload["items"]] == ["L"]  # the repealed row is dropped
    assert fake.contents_calls == 2  # selected A and candidate B; the dropped row is never fetched
    fake, result = _run(monkeypatch, tmp_path, "laws", LAWS, "as-of", "--date", "2006-01-01", "--semantic", "시행일자", "--include-repealed")
    rows = {r["identity"]: r for r in json.loads(result.stdout)["items"]}
    assert rows["M"]["as_of_status"] == "repealed" and rows["M"]["confirmed"] is None
    _, result = _run(monkeypatch, tmp_path, "laws", LAWS, "as-of", "--date", "2006-01-01", "--semantic", "시행일자", "--include-repealed", "--category", "대통령령")
    assert [r["identity"] for r in json.loads(result.stdout)["items"]] == ["M"]


def test_ordinance_get_applies_the_positional_parent_filters(monkeypatch, tmp_path):
    specs = [spec("O", "1", p="2000-01-01", e="2000-01-01", category="조례", path="서울/강남/조례/fixture/본문.md")]
    args = ("get", "fixture", "--date", "2001-01-01", "--semantic", "시행일자")
    _, ok = _run(monkeypatch, tmp_path, "ordinances", specs, *args, "--jurisdiction", "서울", "--subdivision", "강남")
    assert ok.exit_code == 0 and json.loads(ok.stdout)["selected"]["revision"] == "1"
    _, reversed_ = _run(monkeypatch, tmp_path, "ordinances", specs, *args, "--jurisdiction", "강남", "--subdivision", "서울")
    assert reversed_.exit_code == 4 and reversed_.stdout == ""


def test_admrule_get_uses_the_agency_filter(monkeypatch, tmp_path):
    specs = [spec("R", "1", p="2000-01-01", e="2000-01-01", category="고시", path="행정안전부/_본부/고시/fixture/본문.md")]
    args = ("get", "fixture", "--date", "2001-01-01", "--semantic", "공포일자")
    _, ok = _run(monkeypatch, tmp_path, "admrules", specs, *args, "--agency", "행정안전부")
    assert ok.exit_code == 0
    _, wrong = _run(monkeypatch, tmp_path, "admrules", specs, *args, "--agency", "국토교통부")
    assert wrong.exit_code == 4


class _Legacy:
    """Records that the default (pre-index) code paths were reached."""

    def __init__(self, monkeypatch):
        self.calls: list = []
        monkeypatch.setattr("legalize_cli.commands.asof_cmd.make_client", lambda opts: (FakeGitHub(), None))
        monkeypatch.setattr("legalize_cli.commands.asof_cmd.enumerate_laws", lambda *a, **k: self._enumerate())
        monkeypatch.setattr("legalize_cli.commands.asof_cmd.resolve_law_file_as_of", self._resolve)
        monkeypatch.setattr("legalize_cli.commands.asof_cmd.get_law", self._get_law)

    def _enumerate(self):
        from legalize_cli.laws.list import LawEntry

        return [LawEntry(name="예시법", path="kr/예시법/법률.md", category="법률")]

    def _resolve(self, client, cache, path, target, semantic):
        self.calls.append(("resolve", path, target.isoformat(), semantic))
        return None

    def _get_law(self, context, request):
        self.calls.append(("get_law", request.law_name, request.date, request.semantic))
        raise LegalizeError("default get_law reached")


def test_without_an_index_as_of_uses_the_default_resolver(monkeypatch):
    legacy = _Legacy(monkeypatch)
    result = runner.invoke(app, ["laws", "as-of", "--date", "2020-01-01", "--semantic", "시행일자", "--no-cache", "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0 and payload["kind"] == "laws.asof" and payload["items"] == []
    assert legacy.calls == [("resolve", "kr/예시법/법률.md", "2020-01-01", "시행일자")]


def test_without_an_index_the_text_output_of_as_of_keeps_the_existing_columns_exactly(monkeypatch):
    """The status column belongs to the index lookup only; the default text output stays byte for byte the same."""
    from datetime import date, datetime
    from types import SimpleNamespace

    _Legacy(monkeypatch)
    resolved = SimpleNamespace(resolution=SimpleNamespace(
        frontmatter=SimpleNamespace(status="시행"), semantic_date=date(2020, 1, 1),
        commit=SimpleNamespace(author_date=datetime(2020, 1, 1), sha="abc")))
    monkeypatch.setattr("legalize_cli.commands.asof_cmd.resolve_law_file_as_of", lambda *a, **k: resolved)
    result = runner.invoke(app, ["laws", "as-of", "--date", "2020-06-01", "--semantic", "시행일자", "--no-cache"])
    assert result.exit_code == 0
    assert result.stdout == "법률" + " " * 10 + "  예시법" + " " * 25 + "  2020-01-01\n(total=1 as-of 2020-06-01 (시행일자))\n"


def test_without_an_index_a_dated_get_uses_the_default_single_document_path(monkeypatch):
    legacy = _Legacy(monkeypatch)
    for argv, expected in ((["--date", "2020-01-01", "--semantic", "시행일자"], ("2020-01-01", "시행일자")), (["--date", "2020-01-01"], ("2020-01-01", "공포일자")),
                           (["--semantic", "시행일자"], (None, "시행일자")), ([], (None, "공포일자"))):
        legacy.calls.clear()
        result = runner.invoke(app, ["laws", "get", "예시법", *argv, "--no-cache"])
        assert result.exit_code == 1 and result.stderr == "error: default get_law reached\n" and result.stdout == ""
        call = legacy.calls[0]
        assert call[0] == "get_law" and call[3] == expected[1] and (call[2] == expected[0] or expected[0] is None)


@pytest.mark.parametrize("argv", [["laws", "get", "fixture", "--semantic", "", "--date", "2020-01-01"],
                                  ["laws", "as-of", "--semantic", "", "--date", "2020-01-01"],
                                  ["laws", "get", "fixture", "--semantic", "엉뚱", "--no-cache"]])
def test_invalid_semantic_is_rejected_as_before_on_the_default_path_and_the_index_path(monkeypatch, tmp_path, argv):
    legacy = _Legacy(monkeypatch)
    result = runner.invoke(app, [*argv, "--no-cache"])
    assert result.exit_code == 2 and "--semantic must be 공포일자 or 시행일자" in result.stderr and legacy.calls == []
    index = write_index(tmp_path, build_index(FakeGitHub(), "laws", "enforcement", LAWS))
    with_index = runner.invoke(app, [*argv, "--date-index", str(index), "--no-cache"])
    assert with_index.exit_code == 2 and "--semantic must be 공포일자 or 시행일자" in with_index.stderr


def test_the_semantic_default_is_still_shown_in_the_help():
    for command in ("get", "as-of"):
        result = runner.invoke(app, ["laws", command, "--help"], terminal_width=200)
        assert "공포일자" in result.stdout


def test_an_explicit_semantic_alone_selects_the_index_when_the_environment_is_set(monkeypatch, tmp_path):
    fake = FakeGitHub()
    index = write_index(tmp_path, build_index(fake, "laws", "enforcement", LAWS))
    monkeypatch.setattr("legalize_cli.commands.asof_cmd.make_client", lambda opts: (fake, None))
    monkeypatch.setenv("LEGALIZE_LAWS_DATE_INDEX_PATH", str(index))
    for argv in (["--semantic", "공포일자"], ["--semantic", "시행일자"]):
        result = runner.invoke(app, ["laws", "get", "L", *argv, "--no-cache", "--json"])
        assert result.exit_code == 0 and "selected" in json.loads(result.stdout)


def test_a_get_without_date_options_ignores_the_index_option_and_the_environment(monkeypatch, tmp_path):
    legacy = _Legacy(monkeypatch)
    index = write_index(tmp_path, build_index(FakeGitHub(), "laws", "enforcement", LAWS))
    monkeypatch.setenv("LEGALIZE_LAWS_DATE_INDEX_PATH", str(index))
    for extra in ([], ["--date-index", str(index)]):
        result = runner.invoke(app, ["laws", "get", "예시법", *extra, "--no-cache"])
        assert result.exit_code == 1 and "default get_law reached" in result.stderr and "DATE_INDEX" not in result.stderr
    assert [call[0] for call in legacy.calls] == ["get_law", "get_law"]
    for corpus, module in (("admrules", "list_admrules"), ("ordinances", "list_ordinances")):
        def marker(*args, **kwargs):
            raise LegalizeError("default document fetch reached")

        monkeypatch.setattr(f"legalize_cli.commands.{module}.make_client", lambda opts: (FakeGitHub(), None))
        monkeypatch.setattr(f"legalize_cli.commands.{module}.fetch_document_by_name_or_path", marker)
        result = runner.invoke(app, [corpus, "get", "x", "--date-index", str(index), "--no-cache"])
        assert result.exit_code == 1 and "default document fetch reached" in result.stderr


def test_laws_category_means_the_file_category_for_as_of_and_get(monkeypatch, tmp_path):
    specs = [spec("a", "1", p="2000-01-01", e="2000-01-01", name="군인사법", category="법률", path="kr/군인사법/법률.md"),
             spec("b", "1", p="2000-01-01", e="2000-01-01", name="군인사법 시행령", category="대통령령", path="kr/군인사법/시행령.md"),
             spec("c", "1", p="2000-01-01", e="2000-01-01", name="군인사법 시행규칙", category="국방부령", path="kr/군인사법/시행규칙.md")]
    for category, path in (("시행령", "kr/군인사법/시행령.md"), ("시행규칙", "kr/군인사법/시행규칙.md"), ("법률", "kr/군인사법/법률.md")):
        _, listed = _run(monkeypatch, tmp_path, "laws", specs, "as-of", "--date", "2001-01-01", "--category", category)
        assert [r["selected"]["canonical_path"] for r in json.loads(listed.stdout)["items"]] == [path]
        _, got = _run(monkeypatch, tmp_path, "laws", specs, "get", "군인사법", "--date", "2001-01-01", "--category", category)
        assert got.exit_code == 0 and json.loads(got.stdout)["selected"]["canonical_path"] == path


FAMILY = [spec("a", "1", p="2000-01-01", e="2000-01-01", name="군인사법", category="법률", path="kr/군인사법/법률.md"),
          spec("b", "1", p="2000-01-01", e="2000-01-01", name="군인사법 시행령", category="대통령령", path="kr/군인사법/시행령.md"),
          spec("c", "1", p="2000-01-01", e="2000-01-01", name="군인사법 시행규칙", category="국방부령", path="kr/군인사법/시행규칙.md")]


@pytest.mark.parametrize("identifier,extra,path", [
    ("군인사법", [], "kr/군인사법/법률.md"),                         # the default category is 법률, as before
    ("군인사법 시행령", [], "kr/군인사법/시행령.md"),               # a title suffix selects the category
    ("군인사법시행규칙", ["--category", "시행규칙"], "kr/군인사법/시행규칙.md"),
    ("kr/군인사법/시행령.md", [], "kr/군인사법/시행령.md"),         # a full path selects the category
    ("kr/군인사법/시행규칙.md", [], "kr/군인사법/시행규칙.md"),
    ("b", [], "kr/군인사법/시행령.md"),                              # a 법령ID names its file; the default category does not filter it
    ("c", [], "kr/군인사법/시행규칙.md"),
    ("b", ["--category", "시행령"], "kr/군인사법/시행령.md"),
    ("군인사법 시행령", ["--category", "법률"], "kr/군인사법/시행령.md"),  # the existing path lets the title suffix win over --category
])
def test_laws_get_through_the_index_normalises_name_path_id_and_category_like_the_existing_get(monkeypatch, tmp_path, identifier, extra, path):
    _, got = _run(monkeypatch, tmp_path, "laws", FAMILY, "get", identifier, "--date", "2001-01-01", *extra)
    assert got.exit_code == 0 and json.loads(got.stdout)["selected"]["canonical_path"] == path


def test_an_explicit_category_still_filters_a_law_id_and_a_bad_name_is_an_input_error(monkeypatch, tmp_path):
    _, got = _run(monkeypatch, tmp_path, "laws", FAMILY, "get", "b", "--date", "2001-01-01", "--category", "법률")
    assert got.exit_code == 4 and got.stderr == "error: no dated result for b\n"
    fake, bad = _run(monkeypatch, tmp_path, "laws", FAMILY, "get", "kr/없는/경로", "--date", "2001-01-01")
    assert bad.exit_code == 2 and bad.stderr == "error: 법령명 또는 전체 경로와 법령 구분을 확인하세요.\n" and fake.closed


def test_text_mode_as_of_prints_the_existing_columns_and_the_status_not_the_row(monkeypatch, tmp_path):
    specs = FAMILY[:2] + [spec("u", "1", "unresolved", p="2000-06-01", name="미해결법", path="kr/미해결법/법률.md"),
                          spec("x", "1", p="1999-06-01", e="2000-03-01", name="별도법", path="kr/별도법/법률.md")]
    fake = FakeGitHub()
    index = write_index(tmp_path, build_index(fake, "laws", "enforcement", specs))
    monkeypatch.setattr("legalize_cli.commands.asof_cmd.make_client", lambda opts: (fake, None))
    expected = {"시행일자": [["법률", "군인사법", "2000-01-01", "effective"], ["법률", "미해결법", "-", "uncertain"],
                         ["법률", "별도법", "2000-03-01", "effective"], ["시행령", "군인사법", "시행령", "2000-01-01", "effective"]],
                "공포일자": [["법률", "군인사법", "2000-01-01", "effective"], ["법률", "미해결법", "2000-06-01", "uncertain"],
                         ["법률", "별도법", "1999-06-01", "effective"], ["시행령", "군인사법", "시행령", "2000-01-01", "effective"]]}
    for semantic in ("시행일자", "공포일자"):
        day = "2000-07-01"
        result = runner.invoke(app, ["laws", "as-of", "--date", day, "--semantic", semantic, "--date-index", str(index), "--no-cache"])
        assert result.exit_code == 0
        lines = result.stdout.splitlines()
        assert lines[-1] == f"(total=4 as-of {day} ({semantic}))"
        assert sorted(line.split() for line in lines[:-1]) == sorted(expected[semantic])
        assert "{" not in result.stdout and "selected" not in result.stdout


class _Quota:
    def __init__(self, remaining):
        self.remaining = remaining


def _many(count):
    return [spec(f"n{i}", f"r{i}", p="2000-01-01", e="2000-01-01", name=f"name{i}", path=f"kr/name{i}/법률.md") for i in range(count)]


def test_indexed_as_of_goes_through_the_same_call_budget_check(monkeypatch, tmp_path):
    specs = _many(120)
    blocked_message = "would scan 120 laws without a token (default limit is 100); pass --limit N, --yes-exhaust-quota, or set GITHUB_TOKEN"
    fake, blocked = _run(monkeypatch, tmp_path, "laws", specs, "as-of", "--date", "2001-01-01")
    assert blocked.exit_code == 1 and blocked.stdout == "" and blocked.stderr == f"error: {blocked_message}\n"
    assert fake.contents_calls == 0  # refused before any body is fetched
    for flag in (["--yes-exhaust-quota"], ["--limit", "5"]):
        fake, allowed = _run(monkeypatch, tmp_path, "laws", specs, "as-of", "--date", "2001-01-01", *flag)
        assert allowed.exit_code == 0 and fake.contents_calls == (120 if flag[0] == "--yes-exhaust-quota" else 5)


def test_indexed_as_of_refuses_when_the_remaining_quota_is_too_small(monkeypatch, tmp_path):
    fake = FakeGitHub()
    fake.last_rate_limit = _Quota(remaining=7)
    fake.token_source = "env"
    index = write_index(tmp_path, build_index(fake, "laws", "enforcement", _many(10)))
    monkeypatch.setattr("legalize_cli.commands.asof_cmd.make_client", lambda opts: (fake, None))
    argv = ["laws", "as-of", "--date", "2001-01-01", "--date-index", str(index), "--no-cache", "--json"]
    refused = runner.invoke(app, argv)
    assert refused.exit_code == 1 and "would exceed rate-limit budget (need 20, have 7)" in refused.stderr and fake.contents_calls == 0
    assert runner.invoke(app, [*argv, "--yes-exhaust-quota"]).exit_code == 0


def test_an_index_option_or_environment_switches_to_the_new_lookup(monkeypatch, tmp_path):
    legacy = _Legacy(monkeypatch)
    fake = FakeGitHub()
    index = write_index(tmp_path, build_index(fake, "laws", "enforcement", LAWS))
    monkeypatch.setattr("legalize_cli.commands.asof_cmd.make_client", lambda opts: (fake, None))
    option = runner.invoke(app, ["laws", "as-of", "--date", "2006-01-01", "--semantic", "시행일자", "--date-index", str(index), "--no-cache", "--json"])
    assert option.exit_code == 0 and "identity" in json.loads(option.stdout)["items"][0]
    monkeypatch.setenv("LEGALIZE_LAWS_DATE_INDEX_PATH", str(index))
    env_asof = runner.invoke(app, ["laws", "as-of", "--date", "2006-01-01", "--semantic", "시행일자", "--no-cache", "--json"])
    env_get = runner.invoke(app, ["laws", "get", "L", "--date", "2006-01-01", "--semantic", "시행일자", "--no-cache", "--json"])
    assert env_asof.exit_code == 0 and env_get.exit_code == 0 and "selected" in json.loads(env_get.stdout)
    assert legacy.calls == []
    # the environment alone does not change a get without date options
    dateless = runner.invoke(app, ["laws", "get", "예시법", "--no-cache"])
    assert dateless.exit_code == 1 and "default get_law reached" in dateless.stderr


def test_a_failed_index_lookup_for_laws_explains_generation_and_the_default_lookup(monkeypatch, tmp_path):
    monkeypatch.setattr("legalize_cli.commands.asof_cmd.make_client", lambda opts: (FakeGitHub(), None))
    for argv in (["laws", "as-of", "--date", "2020-01-01"], ["laws", "get", "L", "--date", "2020-01-01"]):
        result = runner.invoke(app, [*argv, "--date-index", str(tmp_path / "absent.json"), "--no-cache"])
        assert result.exit_code == 1 and result.stdout == ""
        assert result.stderr.startswith("error: DATE_INDEX_REQUIRED") and "LEGALIZE_LAWS_DATE_INDEX_PATH" in result.stderr
        assert "Generate the index" in result.stderr and "omit --date-index" in result.stderr
        assert "enforcement date is not determined" in result.stderr


@pytest.mark.parametrize("corpus", ["admrules", "ordinances"])
def test_dated_get_of_admrules_and_ordinances_needs_an_index_and_has_no_default_lookup(monkeypatch, corpus):
    monkeypatch.setattr(f"legalize_cli.commands.{MODULES[corpus]}.make_client", lambda opts: (FakeGitHub(), None))
    result = runner.invoke(app, [corpus, "get", "x", "--date", "2020-01-01", "--no-cache"])
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr.startswith("error: DATE_INDEX_REQUIRED") and "Generate the index" in result.stderr
    assert "default lookup" not in result.stderr


def test_a_tampered_returned_body_fails_the_command_with_the_invalid_class(monkeypatch, tmp_path):
    fake = FakeGitHub()
    payload = build_index(fake, "laws", "enforcement", LAWS)
    payload["date_index"]["revisions"][0]["body"]["sha256"] = "0" * 64
    monkeypatch.setattr("legalize_cli.commands.asof_cmd.make_client", lambda opts: (fake, None))
    result = runner.invoke(app, ["laws", "get", "L", "--date", "2001-01-01", "--semantic", "시행일자",
                                 "--date-index", str(write_index(tmp_path, payload)), "--no-cache", "--json"])
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr.startswith("error: DATE_INDEX_INVALID: body hash mismatch") and "Generate the index" in result.stderr


# ---- every failure of the new lookup mode carries the hint, a valid answer does not ----------

_HINT = "Generate the index"
_BACK = "omit --date-index"  # only laws have a default lookup to go back to
FAILURES = [  # (where, error factory, exit code, text that must stay verbatim)
    ("ref", lambda: NotFoundError("GET /repos/legalize-kr/x/git/ref/heads/main -> 404 Not Found"), 4, "git/ref/heads/main -> 404 Not Found"),
    ("ref", lambda: RateLimitError("GitHub rate limit exhausted; resets at <unknown>"), 7, "GitHub rate limit exhausted; resets at <unknown>"),
    ("compare", lambda: RateLimitError("GitHub rate limit exhausted; resets at <unknown>"), 7, "GitHub rate limit exhausted; resets at <unknown>"),
    ("compare", lambda: NotFoundError("GET /compare/x -> 404 Not Found"), 4, "/compare/x -> 404 Not Found"),
    ("body", lambda: NotFoundError("GET /contents/y -> 404 Not Found"), 4, "/contents/y -> 404 Not Found"),
    ("body", lambda: RateLimitError("GitHub rate limit exhausted; resets at <unknown>"), 7, "GitHub rate limit exhausted; resets at <unknown>"),
    ("ref", lambda: httpx.ConnectError("connection refused"), 1, "ConnectError: connection refused"),
    ("body", lambda: httpx.ReadTimeout("timed out"), 1, "ReadTimeout: timed out"),
]
COMMANDS = [  # (corpus, argv, specs)
    ("laws", ["get", "L", "--date", "2001-01-01", "--semantic", "시행일자"], LAWS),
    ("laws", ["as-of", "--date", "2001-01-01", "--semantic", "시행일자"], LAWS),
    ("admrules", ["get", "fixture", "--date", "2001-01-01", "--semantic", "공포일자"],
     [spec("R", "1", p="2000-01-01", e="2000-01-01", category="고시", path="행정안전부/_본부/고시/fixture/본문.md")]),
    ("ordinances", ["get", "fixture", "--date", "2001-01-01", "--semantic", "시행일자"],
     [spec("O", "1", p="2000-01-01", e="2000-01-01", category="조례", path="서울/강남/조례/fixture/본문.md")]),
]


def _invoke(monkeypatch, tmp_path, corpus, specs, argv, fake):
    index = write_index(tmp_path, build_index(FakeGitHub(), corpus, "enforcement", specs))
    monkeypatch.setattr(f"legalize_cli.commands.{MODULES[corpus]}.make_client", lambda opts: (fake, None))
    return runner.invoke(app, [corpus, *argv, "--date-index", str(index), "--no-cache", "--json"])


@pytest.mark.parametrize("corpus,argv,specs", COMMANDS, ids=lambda v: v if isinstance(v, str) else None)
@pytest.mark.parametrize("where,make,code,verbatim", FAILURES)
def test_every_backend_failure_of_the_new_mode_keeps_its_text_and_exit_code_and_adds_the_hint(
        monkeypatch, tmp_path, corpus, argv, specs, where, make, code, verbatim):
    fake = FailingGitHub(where, make())
    result = _invoke(monkeypatch, tmp_path, corpus, specs, argv, fake)
    assert result.exit_code == code and result.stdout == ""
    assert result.stderr.startswith("error: ") and verbatim in result.stderr
    assert _HINT in result.stderr and ("default lookup" in result.stderr) == (corpus == "laws")
    assert (_BACK in result.stderr) == (corpus == "laws")
    assert result.stderr.count(_HINT) == 1 and fake.closed


def _twins(corpus):
    """Two identities that share one name, so the name is ambiguous."""
    paths = {"laws": ("kr/same/법률.md", "kr/same/법률.md"), "admrules": ("행정안전부/_본부/고시/same/본문.md", "국토교통부/_본부/고시/same/본문.md"),
             "ordinances": ("서울/강남/조례/same/본문.md", "부산/해운대/조례/same/본문.md")}[corpus]
    category = {"laws": "법률", "admrules": "고시", "ordinances": "조례"}[corpus]
    return [spec("A", "1", p="2000-01-01", e="2000-01-01", name="same", category=category, path=paths[0]),
            spec("B", "1", p="2000-01-01", e="2000-01-01", name="same", category=category, path=paths[1])]


@pytest.mark.parametrize("corpus", ["laws", "admrules", "ordinances"])
def test_an_ambiguous_identifier_is_an_answer_and_gets_no_hint(monkeypatch, tmp_path, corpus):
    result = _invoke(monkeypatch, tmp_path, corpus, _twins(corpus), ["get", "same", "--date", "2001-01-01", "--semantic", "시행일자"], FakeGitHub())
    assert result.exit_code == 4 and result.stdout == "" and result.stderr == "error: ambiguous identifier: same\n"


@pytest.mark.parametrize("corpus,argv,specs", COMMANDS[:1] + COMMANDS[2:], ids=lambda v: v if isinstance(v, str) else None)
def test_no_result_is_an_answer_and_gets_no_hint(monkeypatch, tmp_path, corpus, argv, specs):
    fake = FakeGitHub()
    result = _invoke(monkeypatch, tmp_path, corpus, specs, [argv[0], "nothing-here", *argv[2:]], fake)
    assert result.exit_code == 4 and result.stdout == "" and result.stderr == "error: no dated result for nothing-here\n"
    assert fake.closed


def test_an_error_of_the_default_path_is_not_changed(monkeypatch):
    legacy = _Legacy(monkeypatch)
    result = runner.invoke(app, ["laws", "get", "예시법", "--date", "2020-01-01", "--no-cache"])
    assert result.exit_code == 1 and result.stderr == "error: default get_law reached\n" and legacy.calls


@pytest.mark.parametrize("corpus", ["admrules", "ordinances"])
@pytest.mark.parametrize("semantic", ["", "엉뚱"])
def test_a_bad_semantic_is_rejected_for_admrules_and_ordinances_before_a_client_exists(monkeypatch, tmp_path, corpus, semantic):
    made = []
    monkeypatch.setattr(f"legalize_cli.commands.{MODULES[corpus]}.make_client", lambda opts: made.append(opts) or (FakeGitHub(), None))
    result = runner.invoke(app, [corpus, "get", "x", "--semantic", semantic, "--date", "2020-01-01", "--no-cache"])
    assert result.exit_code == 2 and "--semantic must be 공포일자 or 시행일자" in result.stderr and made == []


# ---- article and diff do not use the index (documented behaviour, unchanged from 11cfc54) -----------------------

_ENV = ("LEGALIZE_LAWS_DATE_INDEX_PATH", "LEGALIZE_ADMRULES_DATE_INDEX_PATH", "LEGALIZE_ORDINANCES_DATE_INDEX_PATH")


def _stub_article_and_diff(monkeypatch):
    calls: list = []
    for module in ("article", "diff"):
        monkeypatch.setattr(f"legalize_cli.commands.{module}.make_client", lambda opts: (FakeGitHub(), None))
    monkeypatch.setattr("legalize_cli.commands.article.load_law_article",
                        lambda context, request: calls.append(("article", request.law_name, request.date)) or (_ for _ in ()).throw(LegalizeError("article reached")))
    monkeypatch.setattr("legalize_cli.commands.diff.resolve_law_path",
                        lambda client, name, category: calls.append(("diff", name, category)) or (_ for _ in ()).throw(LegalizeError("diff reached")))
    return calls


@pytest.mark.parametrize("argv", [["laws", "article", "민법", "제1조", "--no-cache"], ["laws", "diff", "민법", "민법", "--no-cache"]])
def test_article_and_diff_ignore_the_index_environment_and_have_no_date_index_option(monkeypatch, tmp_path, argv):
    calls = _stub_article_and_diff(monkeypatch)
    plain = runner.invoke(app, argv)
    for name in _ENV:
        monkeypatch.setenv(name, str(tmp_path / "absent.json"))
    with_env = runner.invoke(app, argv)
    assert (plain.exit_code, plain.stdout, plain.stderr) == (with_env.exit_code, with_env.stdout, with_env.stderr)
    assert plain.exit_code == 1 and plain.stderr.endswith("reached\n") and len(calls) == 2 and calls[0] == calls[1]
    calls.clear()
    option = runner.invoke(app, [*argv, "--date-index", str(tmp_path / "absent.json")])
    assert option.exit_code == 2 and "No such option: --date-index" in option.stderr and calls == []


# ---- the loader accepts evidence it does not know -----------------------------------------------------------------

def test_unknown_evidence_and_occurrence_keys_do_not_break_loading_or_lookup(monkeypatch, tmp_path):
    fake = FakeGitHub()
    payload = build_index(fake, "laws", "enforcement", [spec("L", "A", p="2000-01-01", e="2000-01-01")])
    revision = payload["date_index"]["revisions"][0]
    extra = {"user_decisions": [{"id": "d1", "text": "x"}], "lead_rulings": ["r1", {"n": [1, 2]}], "approved_rule": "future-rule"}
    revision["date_evidence"].update(extra)
    revision["occurrences"][0]["date_evidence"].update(extra)
    revision["occurrences"][0]["a_future_key"] = {"anything": True}
    revision["a_future_revision_key"] = [1]
    payload["date_index"]["a_future_index_key"] = "ok"
    monkeypatch.setattr("legalize_cli.commands.asof_cmd.make_client", lambda opts: (fake, None))
    result = runner.invoke(app, ["laws", "get", "L", "--date", "2001-01-01", "--semantic", "시행일자",
                                 "--date-index", str(write_index(tmp_path, payload)), "--no-cache", "--json"])
    row = json.loads(result.stdout)
    assert result.exit_code == 0 and row["selected"]["revision"] == "A"
    assert row["selected"]["occurrence"]["date_evidence"]["user_decisions"] == [{"id": "d1", "text": "x"}]
    assert row["selected"]["date_evidence"]["lead_rulings"] == ["r1", {"n": [1, 2]}]
