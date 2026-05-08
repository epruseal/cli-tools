"""Wrapper for ``GET /repos/{owner}/{repo}/git/trees/{ref}``.

A single recursive tree call enumerates every path in the repo at ``ref`` —
this is how Feature 1 (``legalize laws list``) avoids the lack of a laws-level
``metadata.json`` in ``legalize-kr/legalize-kr``.
"""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

from ..http import GitHubClient

TreeEntryType = Literal["blob", "tree", "commit"]


class TreeEntry(BaseModel):
    """A single entry in a Git tree response."""

    path: str
    type: TreeEntryType
    sha: str
    size: int | None = Field(default=None)


def get_tree(
    client: GitHubClient,
    owner: str,
    repo: str,
    ref: str = "HEAD",
    *,
    recursive: bool = True,
) -> List[TreeEntry]:
    """Return all entries in the tree rooted at ``ref``.

    ``recursive`` uses GitHub's ``?recursive=1`` flag, which returns a flat
    list of every blob + tree below the root. The response includes a
    ``truncated: true`` flag on very large repos; when that happens, split the
    traversal by subtree so callers do not silently miss paths.
    """
    payload = _fetch_tree_payload(client, owner, repo, ref, recursive=recursive)
    if recursive and payload.get("truncated"):
        return _walk_complete_tree(
            client,
            owner,
            repo,
            ref,
            prefix="",
            first_payload=payload,
        )

    return _parse_entries(payload)


def _fetch_tree_payload(
    client: GitHubClient,
    owner: str,
    repo: str,
    ref: str,
    *,
    recursive: bool,
) -> dict:
    params: dict[str, str] = {}
    if recursive:
        params["recursive"] = "1"

    return client.get_json(
        f"/repos/{owner}/{repo}/git/trees/{ref}",
        params=params,
        cache_ttl=60 * 60,
    )


def _parse_entries(payload: dict) -> List[TreeEntry]:
    tree = payload.get("tree", [])
    return [TreeEntry.model_validate(entry) for entry in tree]


def _walk_complete_tree(
    client: GitHubClient,
    owner: str,
    repo: str,
    ref: str,
    *,
    prefix: str,
    first_payload: dict | None = None,
) -> List[TreeEntry]:
    payload = first_payload or _fetch_tree_payload(
        client, owner, repo, ref, recursive=True
    )
    if not payload.get("truncated"):
        return [_with_prefix(entry, prefix) for entry in _parse_entries(payload)]

    payload = _fetch_tree_payload(client, owner, repo, ref, recursive=False)
    result: List[TreeEntry] = []
    for entry in _parse_entries(payload):
        prefixed = _with_prefix(entry, prefix)
        result.append(prefixed)
        if entry.type == "tree":
            result.extend(
                _walk_complete_tree(
                    client,
                    owner,
                    repo,
                    entry.sha,
                    prefix=prefixed.path,
                )
            )
    return result


def _with_prefix(entry: TreeEntry, prefix: str) -> TreeEntry:
    if not prefix:
        return entry
    return entry.model_copy(update={"path": f"{prefix}/{entry.path}"})


__all__ = ["TreeEntry", "TreeEntryType", "get_tree"]
