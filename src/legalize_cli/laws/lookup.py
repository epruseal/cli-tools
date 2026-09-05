"""Shared frontmatter-aware law revision lookup for CLI and MCP callers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from ..cache import DiskCache
from ..config import LAWS_REPO, OWNER
from ..github.commits import CommitInfo
from ..github.contents import get_file_raw
from ..http import GitHubClient
from .asof import ResolvedAsOf, Semantic, resolve_as_of_with_frontmatter
from .frontmatter import parse as parse_frontmatter
from .model import Frontmatter
from .revisions import get_revisions


@dataclass(frozen=True)
class ResolvedLawFile:
    """A selected law file, retaining the fetched source bytes for its caller."""

    resolution: ResolvedAsOf
    raw: bytes


def resolve_law_file_as_of(
    client: GitHubClient,
    cache: Optional[DiskCache],
    path: str,
    target_date: date,
    semantic: Semantic,
    *,
    owner: str = OWNER,
    repo: str = LAWS_REPO,
) -> Optional[ResolvedLawFile]:
    """Fetch and select one law revision under a file-level date semantic.

    A single invocation memoizes each revision body and frontmatter. This is
    essential for 시행일자 lookup: selection reads candidate frontmatters and
    the successful candidate is then used again by the presentation layer.
    """
    commits = get_revisions(client, cache, path, owner=owner, repo=repo)
    if not commits:
        return None

    raw_by_sha: dict[str, bytes] = {}
    frontmatter_by_sha: dict[str, Frontmatter] = {}

    def get_raw(commit: CommitInfo) -> bytes:
        if commit.sha not in raw_by_sha:
            raw_by_sha[commit.sha] = get_file_raw(
                client, owner, repo, path, ref=commit.sha
            )
        return raw_by_sha[commit.sha]

    def get_frontmatter(commit: CommitInfo) -> Frontmatter:
        if commit.sha not in frontmatter_by_sha:
            text = get_raw(commit).decode("utf-8", errors="replace")
            frontmatter_by_sha[commit.sha] = parse_frontmatter(text)[0]
        return frontmatter_by_sha[commit.sha]

    resolution = resolve_as_of_with_frontmatter(
        commits, target_date, semantic, get_frontmatter
    )
    if resolution is None:
        return None
    return ResolvedLawFile(resolution=resolution, raw=get_raw(resolution.commit))


__all__ = ["ResolvedLawFile", "resolve_law_file_as_of"]
