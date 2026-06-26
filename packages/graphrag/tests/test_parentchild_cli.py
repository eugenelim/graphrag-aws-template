"""T5 — CLI verb parentchild-query (offline default + live Function-URL client) (AC5).

Offline runs are deterministic over the fixture corpus (in-memory nested store + HashEmbedder +
offline synthesizer) and print the ordered trace; the live path is a SigV4-signed POST carrying
``mode: "parentchild"`` in the body.

# STUB: AC5
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


def test_parentchild_offline_matches_child_returns_parent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli.main(
        [
            "parentchild-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--q",
            "what does the in-place pod resize KEP say about its rollout?",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    # the ordered trace: question -> matched children -> returned parents -> answer.
    assert (
        out.index("question:")
        < out.index("matched children")
        < out.index("returned parents")
        < out.index("answer:")
    )
    assert "non-semantic" in out.lower()  # offline embedder/synthesizer labeled
    # a returned parent names a doc by its full path (the parent unit, cited by doc_path)
    assert "README.md" in out


def test_parentchild_offline_persona_filters(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(
        [
            "parentchild-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--persona",
            "public-reader",
            "--q",
            "in-place pod resize",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "persona: public-reader" in out  # the synthetic-clearance banner


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
                "hits": ["enhancements/keps/sig-node/1287-x/README.md"],
                "matched_children": ["enhancements/keps/sig-node/1287-x/README.md#1"],
                "answer": "live parent-child answer",
                "citations": ["enhancements:keps/sig-node/1287-x/README.md#Summary"],
                "trace": "matched children: ... returned parents: ...",
            }
        )
        return HttpResponse(status=200, text=body)


def _fake_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDTEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRETTEST")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "TOKENTEST")


def test_parentchild_function_url_sends_mode_parentchild(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_creds(monkeypatch)
    fake = _FakeHttp()
    monkeypatch.setattr(cli, "_make_http_client", lambda: fake)
    rc = cli.main(
        [
            "parentchild-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--function-url",
            "https://abc123.lambda-url.us-east-1.on.aws/",
            "--q",
            "what does the pod resize KEP say?",
        ]
    )
    assert rc == 0
    assert len(fake.calls) == 1
    assert b'"mode": "parentchild"' in fake.calls[0]["data"]
    out = capsys.readouterr().out
    assert "live parent-child answer" in out


def test_parentchild_function_url_non_2xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_creds(monkeypatch)
    monkeypatch.setattr(cli, "_make_http_client", lambda: _FakeHttp(status=403))
    with pytest.raises(RuntimeError, match="boom"):
        cli.main(
            [
                "parentchild-query",
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
