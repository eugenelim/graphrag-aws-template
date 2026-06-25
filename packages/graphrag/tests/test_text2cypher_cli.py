"""T6 — CLI verb text2cypher-query (offline default + live Function-URL client) (AC7).

Offline runs are deterministic over the fixture corpus (in-memory store + rule generator +
offline synthesizer) and print the audit trace; the live path is a SigV4-signed POST carrying
``mode: "text2cypher"`` in the body.

# STUB: AC7
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


def test_text2cypher_offline_prints_audit_trace(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(
        [
            "text2cypher-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--q",
            "Which KEPs does SIG Network own?",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    # the ordered audit trace: schema -> generated attempts -> executed query -> rows -> answer.
    assert out.index("schema:") < out.index("generated attempts:") < out.index("executed query:")
    assert out.index("executed query:") < out.index("rows:") < out.index("answer:")
    assert "kep-1880" in out and "kep-2086" in out  # the executed rows
    assert "non-semantic" in out.lower()  # offline generator/synthesizer labeled
    assert "subset" in out.lower()  # the offline-evaluator honesty label


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
                "executed_query": "MATCH (a:Entity {id: 'sig:sig-network'})-[r:REL {kind: 'OWNS'}]"
                "->(n:Entity) RETURN n LIMIT 100",
                "rows": ["kep-1880", "kep-2086"],
                "answer": "live text2cypher answer",
                "citations": ["kep-1880"],
                "trace": "question: ...\nexecuted query: ...",
                "refusal_reason": None,
            }
        )
        return HttpResponse(status=200, text=body)


def _fake_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDTEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRETTEST")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "TOKENTEST")


def test_text2cypher_function_url_sends_mode_text2cypher(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_creds(monkeypatch)
    fake = _FakeHttp()
    monkeypatch.setattr(cli, "_make_http_client", lambda: fake)
    rc = cli.main(
        [
            "text2cypher-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--function-url",
            "https://abc123.lambda-url.us-east-1.on.aws/",
            "--q",
            "Which KEPs does SIG Network own?",
        ]
    )
    assert rc == 0
    assert len(fake.calls) == 1
    assert b'"mode": "text2cypher"' in fake.calls[0]["data"]  # the additive mode rides the body
    out = capsys.readouterr().out
    assert "live text2cypher answer" in out


def test_text2cypher_function_url_non_2xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_creds(monkeypatch)
    monkeypatch.setattr(cli, "_make_http_client", lambda: _FakeHttp(status=403))
    with pytest.raises(RuntimeError, match="boom"):
        cli.main(
            [
                "text2cypher-query",
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
