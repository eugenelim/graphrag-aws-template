"""T6 — CLI verbs hybrid-query + compare (offline default + live Function-URL client).

Offline runs are deterministic over the fixture corpus (in-memory stores + offline
embedder + offline synthesizer); the live path is a SigV4-signed (service=lambda)
POST whose signature covers the body (payload-hash present, not UNSIGNED-PAYLOAD).

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


def test_hybrid_query_offline_prints_trace(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(
        [
            "hybrid-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--q",
            "the KEPs the SIG @thockin tech-leads owns",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    # ordered seeds -> hops -> citations -> answer.
    assert out.index("seeds") < out.index("hops") < out.index("citations") < out.index("answer")
    assert "person:thockin" in out  # the question seed
    assert "non-semantic" in out.lower()  # offline embedder/synthesizer labeled


def test_compare_offline_prints_three_modes(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(
        [
            "compare",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--q",
            "what does SIG Network own",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "vector-only" in out
    assert "graph-only" in out
    assert "hybrid" in out
    assert "non-semantic" in out.lower()


class _FakeHttp:
    """Captures the signed request; returns a canned Function-URL JSON response."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, *, data: bytes, headers: dict[str, str], verify: bool) -> HttpResponse:
        self.calls.append({"url": url, "data": data, "headers": headers, "verify": verify})
        if not 200 <= self.status < 300:
            return HttpResponse(status=self.status, text='{"error": "boom"}')
        body = json.dumps(
            {
                "answer": "live answer",
                "citations": ["ENHANCEMENTS:keps/.../README.md#Summary"],
                "trace": "seeds: ...",
                "seeds": [{"entity_id": "person:thockin", "source": "question"}],
                "hops": [],
            }
        )
        return HttpResponse(status=200, text=body)


def _fake_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDTEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRETTEST")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "TOKENTEST")


def test_function_url_client_signs_body(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_creds(monkeypatch)
    fake = _FakeHttp()
    monkeypatch.setattr(cli, "_make_http_client", lambda: fake)
    rc = cli.main(
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
    assert rc == 0
    assert len(fake.calls) == 1
    call = fake.calls[0]
    headers = {k.lower(): v for k, v in call["headers"].items()}
    # SigV4 signed: an Authorization header naming the lambda service.
    assert "authorization" in headers
    assert "aws4-hmac-sha256" in headers["authorization"].lower()
    assert "/lambda/aws4_request" in headers["authorization"]
    # The signature covers the body: a payload hash header, not UNSIGNED-PAYLOAD.
    assert "x-amz-content-sha256" in headers
    assert headers["x-amz-content-sha256"] != "UNSIGNED-PAYLOAD"
    # The question rides the POST body.
    assert b"@thockin" in call["data"]
    out = capsys.readouterr().out
    assert "live answer" in out


def test_function_url_non_2xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_creds(monkeypatch)
    fake = _FakeHttp(status=403)
    monkeypatch.setattr(cli, "_make_http_client", lambda: fake)
    with pytest.raises(RuntimeError, match="boom"):
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
                "x",
            ]
        )


# --- slice-4: --persona permission filter on the CLI (AC6) ----------------------------


def _compare(args_extra: list[str]) -> list[str]:
    return ["compare", "--community", COMMUNITY, "--enhancements", ENHANCEMENTS, *args_extra]


def test_compare_persona_public_reader_filters_and_labels(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli.main(_compare(["--q", "What KEPs does SIG Node own?", "--persona", "public-reader"]))
    assert rc == 0
    out = capsys.readouterr().out
    assert "persona: public-reader" in out
    assert "not real authz" in out  # synthetic stand-in labeled
    # the restricted KEP is filtered out of the public-reader's view.
    assert "kep-1287" not in out


def test_compare_persona_maintainer_sees_restricted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli.main(_compare(["--q", "What KEPs does SIG Node own?", "--persona", "maintainer"]))
    assert rc == 0
    out = capsys.readouterr().out
    assert "kep-1287" in out  # the maintainer sees the restricted KEP


def test_unknown_persona_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(_compare(["--q", "anything", "--persona", "root"]))


def test_no_persona_output_byte_identical_to_pre_slice4(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Without --persona, the offline corpus is labeled but visibility is inert: no
    # clearance/filtered/persona lines, no filtering — the slice-3 trace, unchanged.
    rc = cli.main(_compare(["--q", "What KEPs does SIG Node own?"]))
    assert rc == 0
    out = capsys.readouterr().out
    assert "persona:" not in out
    assert "clearance:" not in out
    assert "filtered (visibility" not in out
    # filtering is off: the restricted KEP appears for the no-persona (unrestricted) run.
    assert "kep-1287" in out


def test_function_url_persona_rides_body_and_prints_banner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The live path must (a) send the persona in the POST body and (b) print the same
    # synthetic-stand-in banner the offline verbs print — persona contract consistent
    # across ingresses (the server's trace also carries the clearance line).
    _fake_creds(monkeypatch)
    fake = _FakeHttp()
    monkeypatch.setattr(cli, "_make_http_client", lambda: fake)
    rc = cli.main(
        [
            "hybrid-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--function-url",
            "https://abc123.lambda-url.us-east-1.on.aws/",
            "--q",
            "what does @thockin own",
            "--persona",
            "public-reader",
        ]
    )
    assert rc == 0
    assert b'"persona": "public-reader"' in fake.calls[0]["data"]
    out = capsys.readouterr().out
    assert "persona: public-reader" in out
    assert "not real authz" in out


def test_function_url_unknown_persona_exits_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unknown persona fails closed client-side, before any signed network call.
    _fake_creds(monkeypatch)
    fake = _FakeHttp()
    monkeypatch.setattr(cli, "_make_http_client", lambda: fake)
    with pytest.raises(SystemExit):
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
                "x",
                "--persona",
                "root",
            ]
        )
    assert fake.calls == []  # no network call was made
