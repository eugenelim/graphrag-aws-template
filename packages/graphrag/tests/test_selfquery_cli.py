"""T6 — CLI verb selfquery-query (offline default + live Function-URL client) (AC6).

Offline runs are deterministic over the fixture corpus (in-memory store + rule extractor +
offline synthesizer) and print the ordered trace; the live path is a SigV4-signed POST
carrying ``mode: "selfquery"`` in the body.

# STUB: AC6
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from graphrag import cli
from graphrag.store.neptune import HttpResponse

CORPUS = Path(__file__).parent / "fixtures" / "corpus"
COMMUNITY = str(CORPUS / "community")
ENHANCEMENTS = str(CORPUS / "enhancements")


def test_selfquery_offline_extracts_filter_and_narrows(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(
        [
            "selfquery-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--q",
            "in the enhancements repo, which KEPs are owned by SIG Network?",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    # the ordered trace: question -> extracted filter -> filtered hits -> answer.
    assert (
        out.index("question:")
        < out.index("extracted filter:")
        < out.index("filtered hits:")
        < out.index("answer:")
    )
    # the structured filter the rule extractor read out of the question.
    assert "source (enum) = enhancements" in out
    assert "entity_ids (entity) = sig:sig-network" in out
    assert "non-semantic" in out.lower()  # offline extractor/synthesizer labeled
    # the source filter bit: no community-repo chunk in the filtered hits.
    hits_section = out[out.index("filtered hits:") : out.index("answer:")]
    assert "[community]" not in hits_section


def test_selfquery_offline_no_filter_question_is_unfiltered(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli.main(
        [
            "selfquery-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--q",
            "tell me about pod startup",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "no filter extracted" in out


class _FakeHttp:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, *, data: bytes, headers: dict[str, str], verify: bool) -> HttpResponse:
        self.calls.append({"url": url, "data": data, "headers": headers, "verify": verify})
        if not 200 <= self.status < 300:
            return HttpResponse(status=self.status, text='{"error": "boom"}')
        body = json.dumps(
            {
                "mode": "vector",
                "extracted_filter": {"source": ["enhancements"]},
                "dropped": [],
                "hits": ["enhancements/keps/2086/README.md#0"],
                "answer": "live self-query answer",
                "citations": ["enhancements:keps/2086/README.md#Summary"],
                "trace": "extracted filter: ...",
            }
        )
        return HttpResponse(status=200, text=body)


def _fake_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDTEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRETTEST")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "TOKENTEST")


def test_selfquery_function_url_sends_mode_selfquery(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_creds(monkeypatch)
    fake = _FakeHttp()
    monkeypatch.setattr(cli, "_make_http_client", lambda: fake)
    rc = cli.main(
        [
            "selfquery-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--function-url",
            "https://abc123.lambda-url.us-east-1.on.aws/",
            "--q",
            "what does SIG Network own in enhancements?",
        ]
    )
    assert rc == 0
    assert len(fake.calls) == 1
    assert b'"mode": "selfquery"' in fake.calls[0]["data"]
    out = capsys.readouterr().out
    assert "live self-query answer" in out


def test_selfquery_function_url_non_2xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_creds(monkeypatch)
    monkeypatch.setattr(cli, "_make_http_client", lambda: _FakeHttp(status=403))
    with pytest.raises(RuntimeError, match="boom"):
        cli.main(
            [
                "selfquery-query",
                "--community",
                COMMUNITY,
                "--enhancements",
                ENHANCEMENTS,
                "--function-url",
                "https://abc123.lambda-url.us-east-1.on.aws/",
                "--q",
                "x",
            ]
        )
