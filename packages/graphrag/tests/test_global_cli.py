"""T6 — CLI verbs global-query + detect-communities (offline default + live Function-URL) (AC6).

Offline runs are deterministic over the fixture corpus (in-memory graph → Louvain → in-memory
community store + offline synthesizer) and print the ordered trace; the live path is a
SigV4-signed POST carrying ``mode: "global"`` in the body.

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


def test_global_offline_prints_ordered_trace(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(
        [
            "global-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--q",
            "what are the major areas of work across the SIGs?",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    # the ordered trace: question -> communities considered -> map verdicts -> answer
    assert (
        out.index("Q:")
        < out.index("communities considered")
        < out.index("map verdicts:")
        < out.index("answer:")
    )
    assert "non-semantic" in out.lower()  # offline synthesizer labeled
    assert "Louvain" in out  # the algorithm is named (charter honesty note)


def test_detect_communities_offline_lists_partition(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(
        ["detect-communities", "--community", COMMUNITY, "--enhancements", ENHANCEMENTS]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "== detect-communities (offline) ==" in out
    assert "Louvain" in out and "not Leiden" in out  # the stated divergence
    assert "Neptune Analytics" in out  # the rejected managed alternative is named
    assert "community-0" in out and "members:" in out and "summary:" in out


def test_global_offline_persona_filters(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(
        [
            "global-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--persona",
            "public-reader",
            "--q",
            "summarize the corpus",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "persona: public-reader" in out  # the synthetic-clearance banner
    # real filtering, not just the banner: kep-1287 lives only in the restricted community, so a
    # public-reader's trace must NOT surface it (the above-clearance community is gated out).
    assert "kep-1287" not in out


def test_global_offline_unrestricted_surfaces_restricted_member(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # the contrast to the persona test: with no clearance, the restricted community is served,
    # so its member kep-1287 DOES appear — proving the persona test's absence is real filtering.
    rc = cli.main(
        ["global-query", "--community", COMMUNITY, "--enhancements", ENHANCEMENTS, "--q", "all"]
    )
    assert rc == 0
    assert "kep-1287" in capsys.readouterr().out


def test_global_unknown_persona_is_fail_closed() -> None:
    with pytest.raises(SystemExit, match="unknown persona"):
        cli.main(
            [
                "global-query",
                "--community",
                COMMUNITY,
                "--enhancements",
                ENHANCEMENTS,
                "--persona",
                "ceo",
                "--q",
                "x",
            ]
        )


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
                "communities": [{"id": "community-0", "tier": "public", "size": 4}],
                "answer": "live global answer",
                "citations": ["community:community-0", "enhancements/keps/sig-node/1287/README.md"],
                "trace": "communities considered: ... map verdicts: ... answer: ...",
            }
        )
        return HttpResponse(status=200, text=body)


def _fake_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDTEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRETTEST")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "TOKENTEST")


def test_global_function_url_sends_mode_global(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_creds(monkeypatch)
    fake = _FakeHttp()
    monkeypatch.setattr(cli, "_make_http_client", lambda: fake)
    rc = cli.main(
        [
            "global-query",
            "--community",
            COMMUNITY,
            "--enhancements",
            ENHANCEMENTS,
            "--function-url",
            "https://abc123.lambda-url.us-east-1.on.aws/",
            "--persona",
            "maintainer",
            "--q",
            "summarize the whole corpus",
        ]
    )
    assert rc == 0
    assert len(fake.calls) == 1
    assert b'"mode": "global"' in fake.calls[0]["data"]
    assert b'"persona": "maintainer"' in fake.calls[0]["data"]  # persona rides the body
    assert "X-Amz-Content-SHA256" in fake.calls[0]["headers"]  # signature covers the body
    assert "live global answer" in capsys.readouterr().out


def test_global_function_url_non_2xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_creds(monkeypatch)
    monkeypatch.setattr(cli, "_make_http_client", lambda: _FakeHttp(status=403))
    with pytest.raises(RuntimeError, match="boom"):
        cli.main(
            [
                "global-query",
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
