"""TDD tests for graphrag.ingestion._delta — GitDeltaReader + ManifestManager."""

from __future__ import annotations

from unittest.mock import MagicMock

from graphrag.ingestion._delta import (
    DeltaAction,
    DeltaEntry,
    GitDeltaReader,
    ManifestManager,
)

_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# ── T1-1: A <path> → added ──────────────────────────────────────────────────────


def test_parse_added_line() -> None:
    """A\\t<path> → DeltaEntry(action=added)."""
    entries = GitDeltaReader.parse_name_status("A\tpath/to/file.docx")
    assert len(entries) == 1
    assert entries[0] == DeltaEntry(action=DeltaAction.added, path="path/to/file.docx")


# ── T1-2: M <path> → modified ──────────────────────────────────────────────────


def test_parse_modified_line() -> None:
    """M\\t<path> → DeltaEntry(action=modified)."""
    entries = GitDeltaReader.parse_name_status("M\tpath/to/file.docx")
    assert len(entries) == 1
    assert entries[0] == DeltaEntry(action=DeltaAction.modified, path="path/to/file.docx")


# ── T1-3: D <path> → deleted ───────────────────────────────────────────────────


def test_parse_deleted_line() -> None:
    """D\\t<path> → DeltaEntry(action=deleted)."""
    entries = GitDeltaReader.parse_name_status("D\tpath/to/file.docx")
    assert len(entries) == 1
    assert entries[0] == DeltaEntry(action=DeltaAction.deleted, path="path/to/file.docx")


# ── T1-4: R100 old new → delete old + add new ──────────────────────────────────


def test_parse_rename_line() -> None:
    """R100\\told.docx\\tnew.docx → deleted old + added new (two entries)."""
    entries = GitDeltaReader.parse_name_status("R100\told.docx\tnew.docx")
    assert len(entries) == 2
    assert entries[0] == DeltaEntry(action=DeltaAction.deleted, path="old.docx")
    assert entries[1] == DeltaEntry(action=DeltaAction.added, path="new.docx", old_path="old.docx")


def test_parse_rename_any_similarity() -> None:
    """R<n> with any similarity percentage is treated as a rename."""
    entries = GitDeltaReader.parse_name_status("R75\toldname.md\tnewname.md")
    assert len(entries) == 2
    assert entries[0].action == DeltaAction.deleted
    assert entries[1].action == DeltaAction.added


# ── T1-5: missing manifest → empty-tree SHA ────────────────────────────────────


def test_manifest_missing_returns_empty_tree_sha() -> None:
    """NoSuchKey on manifest read → last_sha = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'."""
    mock_s3 = MagicMock()

    class _FakeClientError(Exception):
        def __init__(self) -> None:
            super().__init__("NoSuchKey")
            self.response = {"Error": {"Code": "NoSuchKey"}}

    mock_s3.get_object.side_effect = _FakeClientError()

    manager = ManifestManager(s3_client=mock_s3, bucket="my-bucket")
    sha = manager.read_sha()
    assert sha == _EMPTY_TREE_SHA


# ── T1-6: full multi-line delta ────────────────────────────────────────────────


def test_parse_full_delta_string() -> None:
    """Multiple lines → correct sets of added/modified/deleted."""
    output = (
        "A\tdocs/new_policy.md\n"
        "M\tdocs/existing_sop.md\n"
        "D\tdocs/old_contract.docx\n"
        "R100\tdocs/temp.md\tdocs/final.md\n"
    )
    entries = GitDeltaReader.parse_name_status(output)
    added = [e for e in entries if e.action == DeltaAction.added]
    modified = [e for e in entries if e.action == DeltaAction.modified]
    deleted = [e for e in entries if e.action == DeltaAction.deleted]

    # docs/new_policy.md added; docs/final.md added (from rename)
    assert {e.path for e in added} == {"docs/new_policy.md", "docs/final.md"}
    # docs/existing_sop.md modified
    assert {e.path for e in modified} == {"docs/existing_sop.md"}
    # docs/old_contract.docx deleted; docs/temp.md deleted (from rename)
    assert {e.path for e in deleted} == {"docs/old_contract.docx", "docs/temp.md"}


# ── extra: manifest write ───────────────────────────────────────────────────────


def test_manifest_write_calls_put_object() -> None:
    """write_sha() calls s3.put_object with the correct bucket and key."""
    mock_s3 = MagicMock()
    manager = ManifestManager(s3_client=mock_s3, bucket="my-bucket")
    manager.write_sha("deadbeef1234")  # pragma: allowlist secret
    mock_s3.put_object.assert_called_once()
    call_kwargs = mock_s3.put_object.call_args.kwargs
    assert call_kwargs["Bucket"] == "my-bucket"
    assert call_kwargs["Key"] == "manifest/last_commit_sha"
    assert b"deadbeef1234" in call_kwargs["Body"]  # pragma: allowlist secret


def test_manifest_read_strips_whitespace() -> None:
    """Trailing newline/whitespace in S3 body is stripped before returning."""
    mock_body = MagicMock()
    mock_body.read.return_value = b"abc1234\n"  # pragma: allowlist secret
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": mock_body}
    manager = ManifestManager(s3_client=mock_s3, bucket="my-bucket")
    sha = manager.read_sha()
    assert sha == "abc1234"  # pragma: allowlist secret


def test_parse_empty_output() -> None:
    """Empty diff output → no entries."""
    assert GitDeltaReader.parse_name_status("") == []


def test_parse_unknown_status_is_ignored() -> None:
    """Unknown status codes (C, U, X) are silently skipped."""
    entries = GitDeltaReader.parse_name_status(
        "C100\tsrc/file.md\tdst/file.md\nU\tconflict.md\nA\treal_file.md\n"
    )
    # Only the A line should produce an entry.
    assert len(entries) == 1
    assert entries[0].action == DeltaAction.added
    assert entries[0].path == "real_file.md"
