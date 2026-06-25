"""T6 — CLI verb governed-query (offline default + live Function-URL client) (AC6).

Offline runs are deterministic over the fixture corpus (in-memory store + rule selector +
offline synthesizer) and print the audit trace; the live path is a SigV4-signed POST
carrying ``mode: "governed"`` in the body.

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


def test_governed_query_offline_prints_audit_trace(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(
        [
            "governed-query",
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
    # the ordered audit trace: template -> params -> cypher -> rows -> answer.
    assert out.index("template:") < out.index("cypher:") < out.index("rows:") < out.index("answer:")
    assert "sig_owned_keps" in out
    assert "kep-1880" in out and "kep-2086" in out  # the executed rows
    assert "$sig" in out  # the parameterized cypher is shown
    assert "non-semantic" in out.lower()  # offline selector/synthesizer labeled


def test_governed_query_offline_no_match_is_legible(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(
        [
            "governed-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--q",
            "what is the weather today",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "no-match" in out
    assert "no query executed" in out


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
                "template_id": "sig_owned_keps",
                "params": {"sig": "sig:sig-network"},
                "cypher": "MATCH (s:Entity {id: $sig})-[r:REL {kind: 'OWNS'}]->(n:Entity) RETURN n",
                "rows": ["kep-1880", "kep-2086"],
                "answer": "live governed answer",
                "citations": ["kep-1880"],
                "trace": "template: sig_owned_keps ...",
            }
        )
        return HttpResponse(status=200, text=body)


def _fake_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDTEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRETTEST")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "TOKENTEST")


def test_governed_function_url_sends_mode_governed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_creds(monkeypatch)
    fake = _FakeHttp()
    monkeypatch.setattr(cli, "_make_http_client", lambda: fake)
    rc = cli.main(
        [
            "governed-query",
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
    # the additive mode field rides the signed body.
    assert b'"mode": "governed"' in fake.calls[0]["data"]
    out = capsys.readouterr().out
    assert "live governed answer" in out
    assert "sig_owned_keps" in out


def test_governed_function_url_non_2xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_creds(monkeypatch)
    monkeypatch.setattr(cli, "_make_http_client", lambda: _FakeHttp(status=403))
    with pytest.raises(RuntimeError, match="boom"):
        cli.main(
            [
                "governed-query",
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


def test_hybrid_function_url_body_unchanged_no_mode_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The additive mode field must not appear on a hybrid call — back-compat wire form.
    _fake_creds(monkeypatch)
    fake = _FakeHttp()
    monkeypatch.setattr(cli, "_make_http_client", lambda: fake)
    cli.main(
        [
            "hybrid-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--function-url",
            "https://abc123.lambda-url.us-east-1.on.aws/",
            "--q",
            "the KEPs @thockin owns",
        ]
    )
    assert b"mode" not in fake.calls[0]["data"]
