"""graphrag.ingestion — Bronze → Silver → Gold per-document pipeline.

Public API
----------
process_document  — entry point for the MedallionOrchestrator (spec-git-ingestion).
ProcessResult     — return type of process_document().
CleansingReport   — audit record included in ProcessResult.

Usage::

    from graphrag.ingestion.pipeline import process_document
    from graphrag.ingestion._types import ProcessResult, CleansingReport
"""
