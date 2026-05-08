"""Wrapper for ``GET /repos/{owner}/{repo}/git/blobs/{sha}``.

The blob endpoint supports files up to 100MB when ``Accept:
application/vnd.github.raw`` is sent. It is useful when callers already have a
blob SHA from a tree response.
"""

from __future__ import annotations

from ..http import GitHubClient


def get_blob_raw(
    client: GitHubClient,
    owner: str,
    repo: str,
    sha: str,
) -> bytes:
    """Return the raw bytes of the blob identified by ``sha``."""
    return client.get_raw(f"/repos/{owner}/{repo}/git/blobs/{sha}")


__all__ = ["get_blob_raw"]
