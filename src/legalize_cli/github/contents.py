"""Wrapper for ``GET /repos/{owner}/{repo}/contents/{path}``.

The JSON media type has reduced behavior for files above 1 MiB, but the raw
media type used here supports files up to GitHub's 100 MiB contents limit.
Callers that already have a blob SHA can also use :mod:`legalize_cli.github.blobs`.
"""

from __future__ import annotations

from typing import Optional

from ..http import GitHubClient
from ..util.errors import LegalizeError

#: GitHub's documented size ceiling for the contents endpoint.
CONTENTS_SIZE_LIMIT_BYTES = 100 * 1024 * 1024


class FileTooLargeError(LegalizeError):
    """Raised when the contents endpoint refuses a file due to size.

    Callers should retry via :func:`legalize_cli.github.blobs.get_blob_raw`
    or the raw CDN host.
    """

    exit_code = 10


def get_file_raw(
    client: GitHubClient,
    owner: str,
    repo: str,
    path: str,
    ref: Optional[str] = None,
) -> bytes:
    """Fetch the raw bytes of a file at ``path``.

    :param ref: Commit SHA / branch / tag. Passed as ``?ref=``. When ``None``
        GitHub serves the default branch HEAD.
    :raises FileTooLargeError: The contents endpoint refused the file because
        it exceeds GitHub's size cap. Retry via :mod:`legalize_cli.github.blobs`.
    """
    params: dict[str, str] = {}
    if ref:
        params["ref"] = ref

    # Translate documented size-limit failures to a typed error; other 403s
    # are already mapped to RateLimitError by the HTTP layer when appropriate.
    try:
        return client.get_raw(
            f"/repos/{owner}/{repo}/contents/{path}",
            params=params or None,
        )
    except Exception as exc:  # pragma: no cover - defensive path
        message = str(exc)
        if "too_large" in message.lower() or "larger than" in message.lower():
            raise FileTooLargeError(
                f"{path} exceeds the contents endpoint size cap; "
                "use github.blobs.get_blob_raw or raw.githubusercontent.com",
            ) from exc
        raise


__all__ = ["get_file_raw", "FileTooLargeError", "CONTENTS_SIZE_LIMIT_BYTES"]
