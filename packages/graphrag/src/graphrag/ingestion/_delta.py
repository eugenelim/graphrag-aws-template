"""graphrag.ingestion._delta — GitDeltaReader + ManifestManager.

GitDeltaReader   — parses ``git diff --name-status`` output; reads the last_commit_sha
                   from the S3 manifest to compute the delta.
ManifestManager  — reads/writes the HEAD SHA to S3 at ``manifest/last_commit_sha``.

Offline unit-test contract: pass a fixture ``--name-status`` string directly to
``GitDeltaReader.parse_name_status()``; stub the S3 client for manifest tests.
No live git binary is required.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "DeltaAction",
    "DeltaEntry",
    "GitDeltaReader",
    "ManifestManager",
]

# git empty-tree SHA — used as a sentinel to trigger a full-corpus rescan.
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_MANIFEST_KEY = "manifest/last_commit_sha"

# git diff --name-status lines are tab-separated:
#   A\tpath/to/file
#   M\tpath/to/file
#   D\tpath/to/file
#   R100\told/path\tnew/path  (renamed; suffix = similarity percent)
#
# Only A, M, D, R-variants are handled; other codes (C, U, X …) are silently ignored.
_RENAME_STATUS_RE = re.compile(r"^R\d+$")

# Validate S3-sourced SHAs before passing to subprocess argv (security: git arg injection).
_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")


class DeltaAction(StrEnum):
    added = "A"
    modified = "M"
    deleted = "D"


@dataclass
class DeltaEntry:
    """A single file-status entry from ``git diff --name-status``."""

    action: DeltaAction
    path: str
    old_path: str | None = None  # populated for renames (R-type)


def _extract_error_code(exc: Exception) -> str:
    """Extract the AWS error code from a botocore ClientError or similar exception."""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        return resp.get("Error", {}).get("Code", "")
    return type(exc).__name__


class ManifestManager:
    """Read/write the ingestion manifest SHA from S3.

    The manifest is a single S3 object at ``manifest/last_commit_sha`` containing
    the raw hex SHA (no JSON wrapper).  Only one Fargate task runs at a time —
    no locking is required.
    """

    def __init__(self, s3_client: Any, bucket: str) -> None:
        self._s3 = s3_client
        self._bucket = bucket

    def read_sha(self) -> str:
        """Read the last ingested commit SHA from S3.

        Returns the empty-tree SHA sentinel when the manifest key does not exist,
        triggering a full-corpus rescan on the next run.

        The SHA is validated against the git hex format (7–64 hex chars) before it is
        returned; an invalid value (e.g. one injected into S3 to exploit git argv) is
        treated the same as a missing key (full-rescan fallback).
        """
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=_MANIFEST_KEY)
            sha = response["Body"].read().decode().strip()
            if sha and _SHA_RE.fullmatch(sha):
                return sha
            return _EMPTY_TREE_SHA
        except Exception as exc:  # noqa: BLE001
            code = _extract_error_code(exc)
            if code in ("NoSuchKey", "404"):
                return _EMPTY_TREE_SHA
            raise

    def write_sha(self, sha: str) -> None:
        """Write the new HEAD SHA to S3 (plain PUT, no conditional write)."""
        self._s3.put_object(
            Bucket=self._bucket,
            Key=_MANIFEST_KEY,
            Body=sha.encode(),
            ContentType="text/plain",
        )


class GitDeltaReader:
    """Parse ``git diff --name-status`` output into :class:`DeltaEntry` items.

    In production the reader shells out to ``git -C <repo_path> diff <last_sha>..HEAD
    --name-status`` once the S3 git bundle has been reconstructed locally.  In unit
    tests bypass the subprocess by calling :meth:`parse_name_status` directly with a
    fixture string.
    """

    def __init__(self, manifest: ManifestManager, repo_path: str = "/tmp/repo") -> None:  # noqa: S108
        self._manifest = manifest
        self._repo_path = repo_path

    # ------------------------------------------------------------------
    # Pure parsing — testable without subprocess
    # ------------------------------------------------------------------

    @staticmethod
    def parse_name_status(output: str) -> list[DeltaEntry]:
        """Parse a ``git diff --name-status`` output string into DeltaEntry objects.

        Rules:
        - ``A\t<path>`` → added.
        - ``M\t<path>`` → modified.
        - ``D\t<path>`` → deleted.
        - ``R<pct>\t<old>\t<new>`` → deleted old + added new.
        - Any other status code is silently skipped.

        Args:
            output: Raw stdout from ``git diff --name-status``.

        Returns:
            List of :class:`DeltaEntry` objects, one per changed file path.
        """
        entries: list[DeltaEntry] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:  # pragma: no cover
                continue
            status = parts[0]

            if _RENAME_STATUS_RE.match(status):
                if len(parts) < 3:
                    continue  # malformed rename line
                old_path, new_path = parts[1], parts[2]
                entries.append(DeltaEntry(action=DeltaAction.deleted, path=old_path))
                entries.append(
                    DeltaEntry(action=DeltaAction.added, path=new_path, old_path=old_path)
                )
            elif status == "A":
                entries.append(DeltaEntry(action=DeltaAction.added, path=parts[1]))
            elif status == "M":
                entries.append(DeltaEntry(action=DeltaAction.modified, path=parts[1]))
            elif status == "D":
                entries.append(DeltaEntry(action=DeltaAction.deleted, path=parts[1]))
            # else: ignore unknown status codes (C, U, X …)

        return entries

    # ------------------------------------------------------------------
    # Production path — reads manifest + shells to git
    # ------------------------------------------------------------------

    def read_delta(self) -> tuple[str, list[DeltaEntry]]:
        """Read the manifest SHA, run git diff, and return the parsed delta.

        Returns:
            ``(last_sha, entries)`` — the SHA before this run and the parsed delta.

        Note:
            Requires ``git`` on PATH and the repo present at :attr:`_repo_path`.
            Use :meth:`parse_name_status` directly in unit tests.
        """
        last_sha = self._manifest.read_sha()
        result = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "git",
                "-C",
                self._repo_path,
                "diff",
                f"{last_sha}..HEAD",
                "--name-status",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        entries = self.parse_name_status(result.stdout)
        return last_sha, entries
