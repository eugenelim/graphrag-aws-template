"""AC7 — the pre-pr.py committed-config secret guard.

Loads `tools/hooks/pre-pr.py` by path (the filename is hyphenated, so it is not an
importable module name) and exercises the placeholder predicate + the pure text scan.
The `git ls-files` plumbing in `_committed_config_secret_findings` is exercised live
against the real repo by running the hook in the project gate; here we unit-test the
secret-detection logic, which is the part that matters.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HOOK_PATH = _REPO_ROOT / "tools" / "hooks" / "pre-pr.py"


def _load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pre_pr_hook", _HOOK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load_hook()


def test_placeholder_email_passes() -> None:
    assert hook._is_placeholder_email("you@example.com")
    assert hook._is_placeholder_email("admin@example.org")
    assert hook._is_placeholder_email("ops@EXAMPLE.NET")
    # A subdomain of a reserved domain is still a placeholder.
    assert hook._is_placeholder_email("alerts@foo.example.com")


def test_real_email_flagged() -> None:
    assert not hook._is_placeholder_email("real.person@accenture.com")
    assert not hook._is_placeholder_email("dev@mycorp.io")
    # A lookalike suffix must NOT be treated as a placeholder (fails closed).
    assert not hook._is_placeholder_email("you@example.com.attacker.io")


def test_placeholder_arn_passes() -> None:
    assert hook._is_placeholder_arn("000000000000")
    assert hook._is_placeholder_arn("<acct>")


def test_real_arn_flagged() -> None:
    assert not hook._is_placeholder_arn("752989493306")


def test_scan_flags_real_email() -> None:
    findings = hook._scan_config_text(
        "config.env", ': "${BUDGET_EMAIL:=real.person@accenture.com}"\n'
    )
    assert findings and "real.person@accenture.com" in findings[0]


def test_scan_flags_real_role_arn() -> None:
    findings = hook._scan_config_text(
        "config.prod.env", "# INVOKER_ROLE_ARN=arn:aws:iam::752989493306:role/Admin\n"
    )
    assert findings and "752989493306" in findings[0]


def test_scan_passes_placeholders() -> None:
    text = (
        ': "${BUDGET_EMAIL:=you@example.com}"\n'
        "# INVOKER_ROLE_ARN=arn:aws:iam::000000000000:role/YourDeployRole\n"
        "# arn:aws:iam::<acct>:role/<role>\n"
    )
    assert hook._scan_config_text("config.local.env.example", text) == []


def test_scan_handles_empty_and_multi_finding_text() -> None:
    # Invariant: never crashes; collects every finding.
    assert hook._scan_config_text("x", "") == []
    assert hook._scan_config_text("x", "nothing secret here\n") == []
    multi = hook._scan_config_text(
        "x", "a@corp.com\narn:aws:iam::111111111111:role/X\nb@example.com\n"
    )
    assert len(multi) == 2  # the placeholder b@example.com is excluded


def test_scan_of_the_real_committed_config_is_clean() -> None:
    # The actual files this PR commits must carry no real secret.
    scripts = _REPO_ROOT / "apps" / "infra" / "scripts"
    for name in ("config.env", "config.local.env.example"):
        text = (scripts / name).read_text(encoding="utf-8")
        assert hook._scan_config_text(name, text) == [], name


def test_tracked_config_glob_discovers_the_committed_files() -> None:
    # Guards against the fail-OPEN regression: if the git glob ever selects zero
    # files the scan passes vacuously. Assert it actually finds the committed config.
    tracked = hook._tracked_config_files(_REPO_ROOT)
    assert "apps/infra/scripts/config.env" in tracked
    assert "apps/infra/scripts/config.local.env.example" in tracked
    # And it must never list the gitignored local file.
    assert "apps/infra/scripts/config.local.env" not in tracked
