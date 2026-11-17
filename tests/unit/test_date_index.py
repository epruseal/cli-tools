"""Unit tests for the date-index lookup in :mod:`legalize_cli.services.date_index`.

An in-memory ``FakeGitHub`` stands in for the GitHub client, so no network is used.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from legalize_cli.laws.list import _LAW_PATH_RE
from legalize_cli.services.date_index import (
    DateIndexError,
    NoResultError,
    index_failure,
    attach_bodies,
    count_git_bodies,
    find_date_result,
    law_category,
    load_date_index,
    select_date,
)
from legalize_cli.util.errors import AmbiguousMatchError, LegalizeError, NotFoundError, RateLimitError

from ._date_index_support import COMMIT, FakeGitHub, build_index, spec, write_index

LAWS = [
    spec("L", "A", p="2000-01-01", e="2000-01-01"),
    spec("L", "B", "unresolved", p="2005-01-01"),
    spec("L", "C", p="2010-01-01", e="2030-01-01"),
    spec("L", "D", p="2034-02-01", e="2034-02-01", repeal=True),
]


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for dataset in ("LAWS", "ADMRULES", "ORDINANCES"):
        monkeypatch.delenv(f"LEGALIZE_{dataset}_DATE_INDEX_PATH", raising=False)


def _load(tmp_path, specs, dataset="laws", basis="enforcement", fake=None):
    fake = fake or FakeGitHub()
    index = load_date_index(fake, "legalize-kr", dataset, write_index(tmp_path, build_index(fake, dataset, basis, specs)))
    return fake, index


def _lookup(index, day, semantic, identifier="L", **kwargs):
    return find_date_result(index, date.fromisoformat(day), semantic, identifier, **kwargs)


# ---- selection, 1 to 5 -------------------------------------------------

PROMULGATION_CASES = [
    ("2005-01-01", "B", "unresolved", "uncertain", []),
    ("2010-06-01", "C", "confirmed", "not_yet_effective", []),
    ("2030-01-01", "C", "confirmed", "effective", []),
    ("2034-02-01", "D", "confirmed", "repealed", []),
]
ENFORCEMENT_CASES = [
    ("2004-12-31", "A", "confirmed", "effective", []),
    ("2005-01-01", "A", "confirmed", "effective", ["B"]),
    ("2029-12-31", "A", "confirmed", "effective", ["B"]),
    ("2030-01-01", "C", "confirmed", "effective", ["B"]),
    ("2034-02-01", "D", "confirmed", "repealed", ["B"]),
]


@pytest.mark.parametrize("basis", ["promulgation", "enforcement"])
@pytest.mark.parametrize("semantic,cases", [("공포일자", PROMULGATION_CASES), ("시행일자", ENFORCEMENT_CASES)])
def test_selection_is_independent_of_the_generation_basis(tmp_path, basis, semantic, cases):
    fake, index = _load(tmp_path, LAWS, basis=basis)
    for day, selected, resolution, as_of, candidates in cases:
        row = _lookup(index, day, semantic)
        assert row["basis"] == basis and row["semantic"] == semantic
        assert (row["selected"]["revision"] if row["selected"] else None) == selected, day
        assert row["date_resolution_status"] == resolution and row["as_of_status"] == as_of, day
        assert [c["revision"] for c in row["candidates"]] == candidates, day


def test_nothing_before_the_first_exposure(tmp_path):
    _, index = _load(tmp_path, LAWS)
    for semantic in ("공포일자", "시행일자"):
        with pytest.raises(NotFoundError):
            _lookup(index, "1999-12-31", semantic)


def test_unresolved_candidate_appears_on_its_exposure_day_only(tmp_path):
    _, index = _load(tmp_path, LAWS)
    assert _lookup(index, "2004-12-31", "시행일자")["candidates"] == []
    assert [c["revision"] for c in _lookup(index, "2005-01-01", "시행일자")["candidates"]] == ["B"]
    assert _lookup(index, "2004-12-31", "공포일자")["selected"]["revision"] == "A"


def test_confirmed_repeal_has_no_confirmed_but_keeps_the_selected_evidence(tmp_path):
    _, index = _load(tmp_path, LAWS)
    row = _lookup(index, "2035-01-01", "시행일자")
    assert row["as_of_status"] == "repealed" and row["confirmed"] is None
    assert row["selected"]["revision"] == "D" and row["selected"]["repeal"] is True


def test_estimated_repeal_keeps_confirmed_and_reports_the_estimate(tmp_path):
    _, index = _load(tmp_path, [spec("E", "R", "estimated", p="1999-03-04", e="1999-03-04", repeal=True)])
    row = _lookup(index, "1999-03-04", "시행일자", "E")
    assert row["as_of_status"] == "repealed" and row["date_resolution_status"] == "estimated"
    assert row["confirmed"] is not None


def test_undated_is_listed_separately_and_never_selected(tmp_path):
    _, index = _load(tmp_path, [spec("U", "X", "undated"), spec("U", "Y", p="2000-01-01", e="2000-01-01")])
    for semantic in ("공포일자", "시행일자"):
        row = _lookup(index, "2026-10-30", semantic, "U")
        assert [u["revision"] for u in row["undated"]] == ["X"]
        assert row["selected"]["revision"] == "Y"
    only = _load(tmp_path, [spec("V", "Z", "undated")])[1]
    row = _lookup(only, "2026-10-30", "시행일자", "V")
    assert row["selected"] is None and row["date_resolution_status"] == "undated" and row["as_of_status"] == "uncertain"


def test_undated_revision_is_found_by_identity_name_and_path(tmp_path):
    _, index = _load(tmp_path, [spec("U", "X", "undated", name="only undated")])
    for identifier in ("U", "only undated", "kr/법률/only undated.md"):
        row = _lookup(index, "2026-10-30", "시행일자", identifier)
        assert row["selected"] is None and row["confirmed"] is None and row["candidates"] == []
        assert [u["revision"] for u in row["undated"]] == ["X"]
        assert (row["date_resolution_status"], row["as_of_status"]) == ("undated", "uncertain")
    with pytest.raises(NotFoundError):
        _lookup(index, "2026-10-30", "시행일자", "another name")


def test_a_dated_result_carries_the_undated_revisions_of_the_same_identity(tmp_path):
    _, index = _load(tmp_path, [spec("U", "X", "undated"), spec("U", "Y", p="2000-01-01", e="2000-01-01")])
    for identifier in ("U", "fixture", "kr/법률/fixture.md"):
        row = _lookup(index, "2026-10-30", "시행일자", identifier)
        assert row["selected"]["revision"] == "Y" and [u["revision"] for u in row["undated"]] == ["X"]


def test_undated_name_overlap_between_identities_follows_the_ambiguity_rule(tmp_path):
    _, index = _load(tmp_path, [spec("A", "1", p="2000-01-01", e="2000-01-01"), spec("B", "2", "undated")])
    with pytest.raises(AmbiguousMatchError):
        _lookup(index, "2026-10-30", "시행일자", "fixture")
    assert _lookup(index, "2026-10-30", "시행일자", "B")["undated"][0]["revision"] == "2"


def test_undated_lookup_respects_the_corpus_filters(tmp_path):
    path = "서울/강남/조례/fixture/본문.md"
    _, index = _load(tmp_path, [spec("O", "1", "undated", category="조례", path=path)], "ordinances")
    assert _lookup(index, "2026-10-30", "시행일자", "fixture", parents=("서울", "강남"))["undated"]
    with pytest.raises(NotFoundError):
        _lookup(index, "2026-10-30", "시행일자", "fixture", parents=("강남", "서울"))


# ---- undated_semantics (occurrences whose date for one lookup basis cannot be used) ----

BOTH = ["공포일자", "시행일자"]
BROKEN_P = dict(p=None, e="1999-01-01", semantics=["공포일자"])  # like MST 496220: broken promulgation date


def test_broken_promulgation_date_is_listed_for_promulgation_and_selected_for_enforcement(tmp_path):
    _, index = _load(tmp_path, [spec("U", "496220", **BROKEN_P)])
    row = _lookup(index, "2026-10-30", "공포일자", "U")
    assert row["selected"] is None and row["confirmed"] is None and row["candidates"] == []
    assert [u["revision"] for u in row["undated"]] == ["496220"]
    assert (row["date_resolution_status"], row["as_of_status"]) == ("undated", "uncertain")
    row = _lookup(index, "2026-10-30", "시행일자", "U")
    assert row["selected"]["revision"] == "496220" and row["undated"] == []
    assert (row["date_resolution_status"], row["as_of_status"]) == ("confirmed", "effective")
    with pytest.raises(NotFoundError):  # nothing is visible before the enforcement date
        _lookup(index, "1998-12-31", "시행일자", "U")


def test_broken_promulgation_date_is_found_by_identity_name_and_path_in_promulgation_lookups(tmp_path):
    _, index = _load(tmp_path, [spec("U", "X", name="broken", **BROKEN_P)])
    for identifier in ("U", "broken", "kr/법률/broken.md"):
        row = _lookup(index, "2026-10-30", "공포일자", identifier)
        assert row["selected"] is None and [u["revision"] for u in row["undated"]] == ["X"]


def test_broken_promulgation_date_is_listed_beside_the_dated_selection(tmp_path):
    _, index = _load(tmp_path, [spec("U", "Y", p="2000-01-01", e="2000-01-01"), spec("U", "X", **BROKEN_P)])
    row = _lookup(index, "2026-10-30", "공포일자", "U")
    assert row["selected"]["revision"] == "Y" and [u["revision"] for u in row["undated"]] == ["X"]
    row = _lookup(index, "2026-10-30", "시행일자", "U")
    assert row["selected"]["revision"] == "Y" and row["undated"] == []  # X (1999) is older than Y (2000) by enforcement
    row = _lookup(index, "1999-06-01", "시행일자", "U")
    assert row["selected"]["revision"] == "X" and row["undated"] == []


def test_broken_promulgation_date_follows_ambiguity_and_parent_filters(tmp_path):
    _, index = _load(tmp_path, [spec("A", "1", p="2000-01-01", e="2000-01-01"), spec("B", "2", **BROKEN_P)])
    with pytest.raises(AmbiguousMatchError):
        _lookup(index, "2026-10-30", "공포일자", "fixture")
    assert _lookup(index, "2026-10-30", "공포일자", "B")["undated"][0]["revision"] == "2"
    path = "서울/강남/조례/fixture/본문.md"
    _, index = _load(tmp_path, [spec("O", "1", category="조례", path=path, **BROKEN_P)], "ordinances")
    assert _lookup(index, "2026-10-30", "공포일자", "fixture", parents=("서울", "강남"))["undated"]
    with pytest.raises(NotFoundError):
        _lookup(index, "2026-10-30", "공포일자", "fixture", parents=("강남", "서울"))


def test_an_index_without_the_key_behaves_as_before(tmp_path):
    _, index = _load(tmp_path, [spec("U", "496220", p=None, e="1999-01-01")])
    with pytest.raises(NotFoundError):  # no key: the revision is not listed as undated, as in the old behaviour
        _lookup(index, "2026-10-30", "공포일자", "U")
    assert _lookup(index, "2026-10-30", "시행일자", "U")["selected"]["revision"] == "496220"
    # An empty list is the same as an absent key when the promulgation date is usable.
    _, index = _load(tmp_path, [spec("D", "1", p="2000-01-01", e="2000-01-01", semantics=[])])
    assert _lookup(index, "2026-10-30", "공포일자", "D")["undated"] == []


def test_undated_status_with_both_semantics_keeps_the_existing_behaviour(tmp_path):
    _, index = _load(tmp_path, [spec("V", "Z", "undated", semantics=BOTH)])
    for semantic in BOTH:
        row = _lookup(index, "2026-10-30", semantic, "V")
        assert [u["revision"] for u in row["undated"]] == ["Z"] and row["selected"] is None


@pytest.mark.parametrize("specs", [
    [spec("U", "X", p=None, e="1999-01-01", semantics=BOTH)],            # status is not undated
    [spec("U", "X", p=None, e="1999-01-01", semantics=["시행일자"])],
    [spec("U", "X", p=None, e="1999-01-01", semantics=[])],               # broken P needs the key when present
    [spec("U", "X", p="2000-01-01", e="2000-01-01", semantics=["공포일자"])],  # P is usable
    [spec("U", "X", "undated", semantics=["공포일자"])],                    # undated needs both
    [spec("U", "X", "undated", semantics=[])],
    [spec("U", "X", p=None, e="1999-01-01", semantics=["공포일자", "공포일자"])],
    [spec("U", "X", p=None, e="1999-01-01", semantics=["공포일자", "기타"])],
    [spec("U", "X", p=None, e="1999-01-01", semantics="공포일자")],
    [spec("U", "X", p=None, e="1999-01-01", semantics=[["공포일자"]])],
    [spec("U", "X", p=None, e="1999-01-01", semantics=[1])],
])
def test_invalid_undated_semantics_is_rejected_at_load_time(tmp_path, specs):
    fake = FakeGitHub()
    path = write_index(tmp_path, build_index(fake, "laws", "enforcement", specs))
    message = _error(lambda: load_date_index(fake, "legalize-kr", "laws", path))
    assert message.startswith("DATE_INDEX_INVALID") and message.count("Generate the index") == 1


# ---- laws names and categories keep their existing meaning (file layout kr/{name}/{category}.md)

REAL_LAWS = [
    spec("m", "1", p="2000-01-01", e="2000-01-01", name="민법", category="법률", path="kr/민법/법률.md"),
    spec("a", "1", p="2000-01-01", e="2000-01-01", name="군인사법", category="법률", path="kr/군인사법/법률.md"),
    spec("b", "1", p="2000-01-01", e="2000-01-01", name="군인사법 시행령", category="대통령령", path="kr/군인사법/시행령.md"),
    spec("c", "1", p="2000-01-01", e="2000-01-01", name="군인사법 시행규칙", category="국방부령", path="kr/군인사법/시행규칙.md"),
    spec("d", "1", p="2000-01-01", e="2000-01-01", name="조정관세규정", category="대통령령", path="kr/조정관세규정/대통령령.md"),
]


def test_law_category_is_the_file_category_not_the_original_classification(tmp_path):
    _, index = _load(tmp_path, REAL_LAWS)
    rows = select_date(index, date(2026, 10, 30), "시행일자")
    assert {r["selected"]["canonical_path"]: law_category(r["selected"]) for r in rows} == {
        "kr/민법/법률.md": "법률", "kr/군인사법/법률.md": "법률", "kr/군인사법/시행령.md": "시행령",
        "kr/군인사법/시행규칙.md": "시행규칙", "kr/조정관세규정/대통령령.md": "대통령령"}


def test_category_selects_the_same_laws_as_the_existing_path_enumeration(tmp_path):
    _, index = _load(tmp_path, REAL_LAWS)
    items = {r["selected"]["canonical_path"]: r["selected"] for r in select_date(index, date(2026, 10, 30), "시행일자")}
    for category in ("법률", "시행령", "시행규칙", "대통령령"):
        existing = {path for path in items if (m := _LAW_PATH_RE.match(path)) and m.group("category") == category}
        assert existing and existing == {path for path, item in items.items() if law_category(item) == category}


@pytest.mark.parametrize("identifier,category,path", [
    ("군인사법", "시행령", "kr/군인사법/시행령.md"),
    ("군인사법", "시행규칙", "kr/군인사법/시행규칙.md"),
    ("군인사법", "법률", "kr/군인사법/법률.md"),
    ("군인사법 시행령", "시행령", "kr/군인사법/시행령.md"),
    ("kr/군인사법/시행령.md", "시행령", "kr/군인사법/시행령.md"),
])
def test_law_name_and_category_resolve_like_the_existing_get(tmp_path, identifier, category, path):
    _, index = _load(tmp_path, REAL_LAWS)
    assert _lookup(index, "2026-10-30", "시행일자", identifier, category=category)["selected"]["canonical_path"] == path


def test_law_category_that_names_no_file_is_not_silently_empty_but_not_found(tmp_path):
    _, index = _load(tmp_path, REAL_LAWS)
    for category in ("대통령령", "국방부령"):  # original classifications, not file categories, for this name
        with pytest.raises(NotFoundError):
            _lookup(index, "2026-10-30", "시행일자", "군인사법", category=category)


def test_git_body_count_for_the_call_budget_counts_only_unfetched_returned_bodies(tmp_path):
    fake, index = _load(tmp_path, MANY + LAWS)
    rows = select_date(index, date(2026, 10, 30), "시행일자")
    assert count_git_bodies(index, rows) == len(MANY) + 2  # one per identity, plus selected and candidate of the law
    attach_bodies(index, rows[:3])
    assert count_git_bodies(index, rows) == len(MANY) + 2 - 3
    inline = _load(tmp_path, [spec("i", "a", p="2000-01-01", e="2000-01-01", body="inline")])[1]
    assert count_git_bodies(inline, select_date(inline, date(2026, 10, 30), "시행일자")) == 0


# ---- identifier resolution, 6 and 7 -------------------------------------

def test_name_is_ambiguous_only_when_both_identities_are_visible_at_that_date(tmp_path):
    specs = [spec("i1", "a", p="2000-01-01", e="2000-01-01"), spec("i2", "b", p="2030-01-01", e="2030-01-01")]
    _, index = _load(tmp_path, specs)
    assert _lookup(index, "2010-01-01", "시행일자", "fixture")["identity"] == "i1"
    with pytest.raises(AmbiguousMatchError):
        _lookup(index, "2031-01-01", "시행일자", "fixture")
    with pytest.raises(NotFoundError):
        _lookup(index, "2010-01-01", "시행일자", "i2")
    assert _lookup(index, "2031-01-01", "시행일자", "i2")["selected"]["revision"] == "b"


def test_several_revisions_of_one_identity_are_not_ambiguous(tmp_path):
    _, index = _load(tmp_path, LAWS)
    assert _lookup(index, "2031-01-01", "시행일자", "fixture")["identity"] == "L"


def test_ordinance_parents_are_positional(tmp_path):
    path = "서울/강남/조례/fixture/본문.md"
    _, index = _load(tmp_path, [spec("O", "1", p="2000-01-01", e="2000-01-01", category="조례", path=path)], "ordinances")
    assert _lookup(index, "2001-01-01", "시행일자", "fixture", category="조례", parents=("서울", "강남"))
    assert _lookup(index, "2001-01-01", "시행일자", "fixture", parents=("서울", ""))
    for wrong in (("강남", "서울"), ("", "서울"), ("강남", "")):
        with pytest.raises(NotFoundError):
            _lookup(index, "2001-01-01", "시행일자", "fixture", parents=wrong)


def test_admrule_agency_matches_any_parent_segment_but_not_the_category(tmp_path):
    path = "행정안전부/_본부/고시/fixture/본문.md"
    _, index = _load(tmp_path, [spec("R", "1", p="2000-01-01", e="2000-01-01", category="고시", path=path)], "admrules")
    assert _lookup(index, "2001-01-01", "시행일자", "fixture", parents=("_본부",))
    with pytest.raises(NotFoundError):
        _lookup(index, "2001-01-01", "시행일자", "fixture", parents=("고시",))


def test_non_law_path_must_end_in_a_document_body_file(tmp_path):
    _, index = _load(tmp_path, [spec("O", "1", p="2000-01-01", e="2000-01-01", category="조례", path="kr/fixture/본문.md")], "ordinances")
    with pytest.raises(DateIndexError, match="DATE_INDEX_INVALID") as excinfo:
        _lookup(index, "2001-01-01", "시행일자", "fixture")
    assert "Generate the index" in str(excinfo.value)


# ---- error classes and guidance, 8 --------------------------------------

def _error(callable_):
    with pytest.raises(DateIndexError) as excinfo:
        callable_()
    return str(excinfo.value)


def test_missing_source_is_required_with_one_guidance(tmp_path):
    message = _error(lambda: load_date_index(FakeGitHub(), "legalize-kr", "laws", None))
    assert message.startswith("DATE_INDEX_REQUIRED") and "LEGALIZE_LAWS_DATE_INDEX_PATH" in message
    assert "legalize-kr-compiler" in message and message.count("Generate the index") == 1
    assert _error(lambda: load_date_index(FakeGitHub(), "legalize-kr", "laws", tmp_path / "absent.json")).startswith("DATE_INDEX_REQUIRED")


def test_only_laws_guidance_mentions_the_default_lookup(tmp_path):
    laws = _error(lambda: load_date_index(FakeGitHub(), "legalize-kr", "laws", tmp_path / "absent.json"))
    assert "omit --date-index and unset LEGALIZE_LAWS_DATE_INDEX_PATH" in laws and "enforcement date is not determined" in laws
    assert laws.count("Generate the index") == 1
    for dataset, repo in (("admrules", "admrule-kr"), ("ordinances", "ordinance-kr")):
        message = _error(lambda: load_date_index(FakeGitHub(), repo, dataset, tmp_path / "absent.json"))
        assert "Generate the index" in message and "default lookup" not in message


def test_remote_index_errors_carry_the_same_guidance_and_keep_a_remote_mark(tmp_path):
    for dataset, repo, path, hint in (("laws", "legalize-kr", "kr/법률/fixture.md", True),
                                      ("admrules", "admrule-kr", "행정안전부/_본부/고시/fixture/본문.md", False)):
        fake = FakeGitHub()
        index = load_date_index(fake, repo, dataset, write_index(tmp_path, build_index(
            fake, dataset, "enforcement", [spec("X", "1", p="2000-01-01", e="2000-01-01", path=path)])))
        fake.compare_status[COMMIT] = "diverged"
        with pytest.raises(DateIndexError) as excinfo:
            _lookup(index, "2001-01-01", "시행일자", "X", parents=("",))
        message = str(excinfo.value)
        assert excinfo.value.remote is True
        assert message.startswith("DATE_INDEX_INVALID: body commit is not reachable from HEAD.")
        assert message.count("Generate the index") == 1 and ("default lookup" in message) is hint


def test_index_requested_follows_the_option_or_the_environment(monkeypatch, tmp_path):
    from legalize_cli.services.date_index import index_requested

    assert index_requested("laws", None) is False
    assert index_requested("laws", tmp_path / "x.json") is True
    monkeypatch.setenv("LEGALIZE_LAWS_DATE_INDEX_PATH", "x.json")
    assert index_requested("laws", None) is True
    assert index_requested("laws", None, date_option_given=False) is False  # a get without date options stays on the default path
    assert index_requested("admrules", None) is False


def test_environment_path_is_used_when_the_option_is_missing(tmp_path, monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setenv("LEGALIZE_ADMRULES_DATE_INDEX_PATH", str(write_index(tmp_path, build_index(fake, "admrules", "enforcement", []))))
    assert load_date_index(fake, "admrule-kr", "admrules", None)["dataset"] == "admrules"
    assert _error(lambda: load_date_index(fake, "legalize-kr", "laws", None)).startswith("DATE_INDEX_REQUIRED")


@pytest.mark.parametrize("text", ["{not json", '{"date_index": {"a": 1, "a": 2}}', '{"date_index": NaN}', "ÿ"])
def test_unreadable_index_files_are_invalid_with_guidance(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_bytes(text.encode("latin-1") if text == "ÿ" else text.encode())
    message = _error(lambda: load_date_index(FakeGitHub(), "legalize-kr", "laws", path))
    assert message.startswith("DATE_INDEX_INVALID") and message.count("Generate the index") == 1


def _mutate(tmp_path, mutate, specs=LAWS):
    fake = FakeGitHub()
    payload = build_index(fake, "laws", "enforcement", specs)
    mutate(payload["date_index"])
    return _error(lambda: load_date_index(fake, "legalize-kr", "laws", write_index(tmp_path, payload)))


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(schema_version=3),  # 1 and 2 (LLM judgment dates, compiler#2) are supported
    lambda d: d.update(dataset="admrules"),
    lambda d: d["revisions"][0]["occurrences"][0].update(status="undated"),
    lambda d: d["revisions"][0]["occurrences"][0].update(promulgation_rank=None),
    lambda d: d["revisions"][1]["occurrences"][0].update(enforcement_date="2005-01-01"),
    lambda d: d["revisions"][0].pop("name"),
])
def test_invalid_structure_is_reported_with_guidance_and_no_input_values(tmp_path, mutate):
    message = _mutate(tmp_path, mutate)
    assert message.startswith("DATE_INDEX_INVALID") and message.count("Generate the index") == 1


def test_head_mismatch_is_invalid_with_guidance(tmp_path):
    fake = FakeGitHub()
    path = write_index(tmp_path, build_index(fake, "laws", "enforcement", LAWS))
    fake.head = "c" * 40
    message = _error(lambda: load_date_index(fake, "legalize-kr", "laws", path))
    assert "HEAD mismatch" in message and "Generate the index" in message


def test_errors_that_are_not_index_errors_keep_their_class_and_text(tmp_path):
    fake, index = _load(tmp_path, LAWS)
    fake.files.clear()  # the referenced file is gone: a downstream 404, not an index problem
    with pytest.raises(NotFoundError) as excinfo:
        _lookup(index, "2001-01-01", "시행일자")
    assert not isinstance(excinfo.value, DateIndexError) and "Generate the index" not in str(excinfo.value)
    assert str(excinfo.value).endswith("-> 404 Not Found")


def test_index_failure_adds_the_hint_to_backend_failures_and_keeps_class_and_exit_code():
    for original in (NotFoundError("GET /x -> 404 Not Found"), RateLimitError("rate limit exhausted"), LegalizeError("other")):
        text, cls, code = str(original), type(original), original.exit_code
        out = index_failure(original, "ordinances")
        assert out is original and type(out) is cls and out.exit_code == code
        assert str(out).startswith(text + ".") and str(out).count("Generate the index") == 1 and "default lookup" not in str(out)
    assert "omit --date-index" in str(index_failure(NotFoundError("x"), "laws"))


def test_index_failure_maps_a_network_error_to_exit_code_1_and_keeps_its_text():
    out = index_failure(httpx.ConnectError("connection refused"), "laws")
    assert type(out) is LegalizeError and out.exit_code == 1
    assert str(out).startswith("ConnectError: connection refused.") and "Generate the index" in str(out)


def test_index_failure_leaves_valid_answers_and_index_errors_untouched():
    answers = [AmbiguousMatchError("ambiguous identifier: x", ["a", "b"]), NoResultError("no dated result for x")]
    for original in answers:
        before = str(original)
        assert index_failure(original, "laws") is original and str(original) == before
    assert isinstance(answers[1], NotFoundError) and answers[1].exit_code == 4
    with pytest.raises(DateIndexError) as excinfo:
        load_date_index(FakeGitHub(), "legalize-kr", "laws", None)
    before = str(excinfo.value)
    assert index_failure(excinfo.value, "laws") is excinfo.value and str(excinfo.value) == before


# ---- lazy body verification, 9 and 10 -----------------------------------

MANY = [spec(f"n{i}", f"r{i}", p="2000-01-01", e="2000-01-01", name=f"name{i}") for i in range(40)]


def test_backend_calls_follow_the_number_of_returned_git_bodies(tmp_path):
    fake, index = _load(tmp_path, MANY + LAWS)
    assert fake.contents_calls == 0 and fake.compare_calls == 0 and fake.ref_calls == 1
    _lookup(index, "2026-10-30", "시행일자", "n3")
    assert (fake.contents_calls, fake.compare_calls) == (1, 1)
    _lookup(index, "2005-06-01", "시행일자", "L")  # selected A plus candidate B
    assert (fake.contents_calls, fake.compare_calls) == (3, 1)  # compare is cached per commit
    with pytest.raises(NotFoundError):
        _lookup(index, "1990-01-01", "시행일자", "n3")
    assert fake.contents_calls == 3


def test_as_of_rows_are_verified_only_when_attached(tmp_path):
    fake, index = _load(tmp_path, MANY)
    rows = select_date(index, date(2026, 10, 30), "시행일자")
    assert len(rows) == 40 and fake.contents_calls == 0
    attach_bodies(index, rows[:5])
    assert fake.contents_calls == 5 and all("body" in r["selected"] for r in rows[:5])


def test_tampering_is_detected_for_returned_git_bodies_only(tmp_path):
    fake, index = _load(tmp_path, MANY)
    fake.files[(COMMIT, "kr/법률/name9.md#r9")] = b"changed\n"  # not returned by the lookup below
    assert _lookup(index, "2026-10-30", "시행일자", "n3")["selected"]["body"].startswith("n3/r3")
    message = _error(lambda: _lookup(index, "2026-10-30", "시행일자", "n9"))
    assert "body blob mismatch" in message and "Generate the index" in message
    index["revisions"][4]["body"]["sha256"] = "0" * 64
    assert "body hash mismatch" in _error(lambda: _lookup(index, "2026-10-30", "시행일자", "n4"))
    assert _lookup(index, "2026-10-30", "시행일자", "n5")["selected"]["revision"] == "r5"


def test_inline_bodies_are_verified_for_every_revision_at_load_time(tmp_path):
    specs = [spec("i", "a", p="2000-01-01", e="2000-01-01", body="inline"), spec("j", "b", p="2000-01-01", e="2000-01-01", name="other", body="inline")]
    message = _mutate(tmp_path, lambda d: d["revisions"][1]["body"].update(text="tampered\n"), specs)
    assert "body hash mismatch" in message and "Generate the index" in message
    fake, index = _load(tmp_path, specs)
    assert _lookup(index, "2001-01-01", "시행일자", "i")["selected"]["body"] == "i/a body\n"
    assert fake.contents_calls == 0


# ---- git body paths are used as the index gives them ------------------------------------------------------------------

LONG_PATHS = [
    "kr/법률/.legalize/candidates/laws/" + "ab" * 240 + "/" + "cd" * 240 + ".md",       # long UTF-8 hex directories
    "kr/법률/.legalize/candidates/laws/sha256-" + "0123456789abcdef" * 4 + "/" + "ef" * 32 + ".md",  # the hashed form
]


@pytest.mark.parametrize("path", LONG_PATHS)
def test_git_body_paths_are_used_verbatim_whatever_their_length(tmp_path, path):
    fake, index = _load(tmp_path, [spec("L", "A", p="2000-01-01", e="2000-01-01", path=path)])
    requested = []
    original = fake.get_raw
    fake.get_raw = lambda p, *, params=None: (requested.append((p, dict(params))), original(p, params=params))[1]
    row = _lookup(index, "2001-01-01", "시행일자")
    assert row["selected"]["body"] == "L/A body\n"
    assert len(requested) == 1 and requested[0][0].split("/contents/", 1)[1] == path + "#A"
    assert requested[0][1] == {"ref": COMMIT}


@pytest.mark.parametrize("path", LONG_PATHS)
def test_a_tampered_blob_at_a_long_path_still_fails_as_an_invalid_index(tmp_path, path):
    fake = FakeGitHub()
    payload = build_index(fake, "laws", "enforcement", [spec("L", "A", p="2000-01-01", e="2000-01-01", path=path)])
    payload["date_index"]["revisions"][0]["body"]["blob_sha"] = "0" * 40
    index = load_date_index(fake, "legalize-kr", "laws", write_index(tmp_path, payload))
    message = _error(lambda: _lookup(index, "2001-01-01", "시행일자"))
    assert message.startswith("DATE_INDEX_INVALID: body blob mismatch") and message.count("Generate the index") == 1


# ---- dates: the same range as the compiler (years 0001 to 9999, a real day, the ISO form only) -------------------------

BAD_DATES = ["0000-01-01", "0000-12-31", "00000101", "99991231", "20200101", "2020-1-1", "2020-02-30", "2021-02-29", "2020-13-01", "2020-00-10",
             "2020-01-00", "-001-01-01", " 2020-01-01", "2020-01-01 ", "2020-01-01\n", "２０２０-01-01", "2020/01/01", "2020-01",
             "2020-01-01,2021-01-01", "2020-01-01/2030-01-01", "2020-01-01 2021-01-01", "2020-01-01T00:00:00", 20200101, True]
GOOD_DATES = ["0001-01-01", "1000-01-01", "2020-02-29", "9999-12-31"]


@pytest.mark.parametrize("value", BAD_DATES, ids=repr)
def test_a_date_outside_the_compiler_range_or_form_is_an_invalid_index(tmp_path, value):
    def mutate(d):
        d["revisions"][0]["occurrences"][0]["promulgation_date"] = value
        d["revisions"][0]["promulgation_date"] = value
    message = _mutate(tmp_path, mutate, [spec("L", "A", p="2000-01-01", e="2000-01-01")])
    assert message.startswith("DATE_INDEX_INVALID") and message.count("Generate the index") == 1


@pytest.mark.parametrize("value", GOOD_DATES)
def test_the_boundary_dates_of_the_range_load_and_compare_as_text(tmp_path, value):
    _, index = _load(tmp_path, [spec("L", "A", p=value, e=value)])
    assert _lookup(index, value, "공포일자")["selected"]["revision"] == "A"
    assert _lookup(index, value, "시행일자")["selected"]["revision"] == "A"


# ---- LLM judgment dates (compiler#2, schema 2) -------------------------

LLM = [
    spec("L", "A", "confirmed", p="2000-01-01", e="2000-01-01"),
    spec("L", "B", "llm", p="2010-01-01", e="2010-02-01"),
]


def test_an_llm_date_is_selected_like_a_confirmed_one_and_named(tmp_path):
    _, index = _load(tmp_path, LLM)
    row = _lookup(index, "2010-03-01", "시행일자")
    assert row["selected"]["revision"] == "B"
    assert (row["date_resolution_status"], row["as_of_status"]) == ("llm", "effective")
    assert row["llm_judgment"]["label"] == "LLM 판단"
    assert row["llm_judgment"]["quote"] == "이 조례는 공포일부터 시행한다."
    before = _lookup(index, "2010-01-15", "시행일자")
    assert before["selected"]["revision"] == "A" and "llm_judgment" not in before


def test_an_llm_repeal_has_no_confirmed(tmp_path):
    specs = [spec("L", "A", "confirmed", p="2000-01-01", e="2000-01-01"),
             spec("L", "B", "llm", p="2010-01-01", e="2010-02-01", repeal=True)]
    _, index = _load(tmp_path, specs)
    row = _lookup(index, "2010-03-01", "시행일자")
    assert (row["as_of_status"], row["confirmed"]) == ("repealed", None)


def test_schema_1_with_an_llm_status_is_rejected(tmp_path):
    fake = FakeGitHub()
    payload = build_index(fake, "laws", "enforcement", LLM)
    payload["date_index"]["schema_version"] = 1
    with pytest.raises(LegalizeError):
        load_date_index(fake, "legalize-kr", "laws", write_index(tmp_path, payload))
    payload["date_index"]["schema_version"] = 3
    with pytest.raises(LegalizeError):
        load_date_index(fake, "legalize-kr", "laws", write_index(tmp_path, payload, name="three.json"))
