"""Test doubles for the date-index lookups: an in-memory GitHub client and an index builder.

``FakeGitHub`` keeps the method names, keyword-only arguments and return shapes of
:class:`legalize_cli.http.GitHubClient` for the calls the date-index code makes
(``get_json`` for the main ref and compare, ``get_raw`` for contents, ``close``).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Optional

from legalize_cli.util.errors import NotFoundError

HEAD = "a" * 40
COMMIT = "b" * 40


class FakeGitHub:
    token_source = "none"
    last_rate_limit = None

    def __init__(self, head: str = HEAD) -> None:
        self.head = head
        self.files: dict[tuple[str, str], bytes] = {}
        self.compare_status: dict[str, str] = {}
        self.compare_calls = 0
        self.contents_calls = 0
        self.closed = False
        self.ref_calls = 0

    def get_json(self, path: str, *, params: Optional[Mapping[str, Any]] = None,
                 accept: str = "application/vnd.github+json", cache_ttl: Optional[int] = None) -> Any:
        if path.endswith("/git/ref/heads/main"):
            self.ref_calls += 1
            return {"object": {"type": "commit", "sha": self.head}}
        if "/compare/" in path:
            self.compare_calls += 1
            base = path.rsplit("/compare/", 1)[1].split("...")[0]
            return {"status": self.compare_status.get(base, "ahead")}
        raise AssertionError(f"unexpected API path {path}")

    def get_raw(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> bytes:
        self.contents_calls += 1
        file_path = path.split("/contents/", 1)[1]
        try:
            return self.files[(params["ref"], file_path)]
        except KeyError:
            raise NotFoundError(f"GET {path} -> 404 Not Found") from None

    def close(self) -> None:
        self.closed = True


class FailingGitHub(FakeGitHub):
    """Raises ``error`` at one step of the date-index lookup: ``ref`` (HEAD), ``compare`` or ``body`` (contents)."""

    def __init__(self, where: str, error: BaseException) -> None:
        super().__init__()
        self.where, self.error = where, error

    def get_json(self, path, *, params=None, accept="application/vnd.github+json", cache_ttl=None):
        if (self.where == "ref" and path.endswith("/git/ref/heads/main")) or (self.where == "compare" and "/compare/" in path):
            raise self.error
        return super().get_json(path, params=params, accept=accept, cache_ttl=cache_ttl)

    def get_raw(self, path, *, params=None):
        if self.where == "body":
            raise self.error
        return super().get_raw(path, params=params)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def spec(identity: str, revision: str, status: str = "confirmed", *, p: Optional[str] = None,
         e: Optional[str] = None, repeal: bool = False, body: str = "git", name: str = "fixture",
         category: str = "법률", path: Optional[str] = None, semantics: Optional[list] = None) -> dict:
    """One revision with one occurrence. ``p``/``e`` are promulgation and enforcement dates.

    ``semantics`` is written as the occurrence key ``undated_semantics`` when it is not None.
    """
    return dict(identity=identity, revision=revision, status=status, p=p, e=e, repeal=repeal,
                body=body, name=name, category=category, path=path or f"kr/{category}/{name}.md",
                semantics=semantics)


def build_index(fake: FakeGitHub, dataset: str, basis: str, specs: list[dict]) -> dict:
    """Return the ``{"date_index": ...}`` wrapper. Ranks follow the contract (date order, unique)."""
    def event(s: dict) -> Optional[str]:
        return s["e"] if s["status"] in ("confirmed", "estimated", "llm") else s["p"] if s["status"] == "unresolved" else None

    ranked = {"p": {}, "e": {}}
    for key, dates in (("p", lambda s: s["p"]), ("e", event)):
        ordered = sorted((s for s in specs if dates(s)), key=lambda s: (dates(s), s["identity"], s["revision"]))
        ranked[key] = {id(s): n for n, s in enumerate(ordered)}
    revisions = []
    for s in specs:
        text = f"{s['identity']}/{s['revision']} body\n".encode()
        digest = _sha256(text)
        if s["body"] == "inline":
            body = {"kind": "inline", "text": text.decode(), "sha256": digest}
        else:
            fake.files[(COMMIT, s["path"] + "#" + s["revision"])] = text
            body = {"kind": "git", "commit_sha": COMMIT, "path": s["path"] + "#" + s["revision"],
                    "blob_sha": _blob_sha(text), "sha256": digest}
        occ = {"source_path": f"src/{s['revision']}.xml", "raw_promulgation_date": s["p"], "raw_enforcement_date": s["e"],
               "promulgation_date": s["p"], "enforcement_date": s["e"], "status": s["status"],
               "exposure_date": s["p"] if s["status"] == "unresolved" else None,
               "date_evidence": {"llm_judgment": {"field": "부칙", "quote": "이 조례는 공포일부터 시행한다.", "model": "m",
                                                  "judged_at": "t"}} if s["status"] == "llm" else {},
               "observed_metadata": {}, "reason": "fixture", "promulgation_rank": ranked["p"].get(id(s)),
               "enforcement_rank": ranked["e"].get(id(s))}
        if s["semantics"] is not None:
            occ["undated_semantics"] = s["semantics"]
        revisions.append({"identity": s["identity"], "revision": s["revision"], "copy_id": _sha256(s["revision"].encode()),
                          "name": s["name"], "category": s["category"], "canonical_path": s["path"],
                          "promulgation_date": s["p"], "enforcement_date": s["e"], "status": s["status"],
                          "repeal": s["repeal"], "reason": "fixture", "date_evidence": {}, "occurrences": [occ],
                          "body": body})
    schema = 2 if any(s["status"] == "llm" for s in specs) else 1
    return {"date_index": {"schema_version": schema, "basis": basis, "dataset": dataset, "head_commit_sha": fake.head,
                           "snapshots": [], "revisions": revisions}}


def write_index(tmp_path: Path, payload: dict, name: str = "index.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
