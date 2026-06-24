#!/usr/bin/env python3
"""Pre-PR hook (adopter-facing): the work-loop's mechanical termination
check, plus a place to wire *your* project's gate.

Most agent tools fire no pre-PR / pre-push event, so wire this via a Git
``pre-push`` hook (``.git/hooks/pre-push``) or run it by hand before opening
a PR — the same way regardless of which agent tool you use:

    python tools/hooks/pre-pr.py

What it runs:
  - ``loop-cohort.py check <spec-dir>`` for each ``docs/specs/*/state.json``,
    in ``--phase implement`` and ``--phase review`` — the work-loop's
    iteration/stasis caps. The script ships with the work-loop skill; this
    hook finds it under whichever skills directory your agent tool installed
    into (``.claude/``, ``.agents/``, ``.kiro/`` …). Skipped cleanly when
    there are no active specs (or the work-loop isn't installed).

It deliberately runs **none** of the source project's own artifact linters —
those enforce that project's conventions on its own tree and
don't apply to your repo. Wire your project's lint/typecheck/test commands
into the stub below instead (or let the ``adapt-to-project`` skill do it).

This hook degrades gracefully: a missing tool is a skip with a notice, never
a hard failure.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


# The work-loop skill ships with `core` but lands under different roots
# depending on which agent tool the pack was installed for — so probe the
# known adapter skill directories rather than assuming Claude Code's `.claude/`.
_SKILL_ROOTS = (
    ".claude/skills",  # Claude Code
    ".agents/skills",  # Codex
    ".kiro/skills",    # Kiro
    ".apm/skills",     # APM (and the pack's own source layout)
)


def _find_loop_cohort() -> Path | None:
    """Locate the work-loop's ``loop-cohort.py`` under whichever adapter skill
    root it was installed into. Returns ``None`` when the work-loop isn't
    present (caps check is then skipped, not failed)."""
    for root in _SKILL_ROOTS:
        candidate = Path(root) / "work-loop" / "scripts" / "loop-cohort.py"
        if candidate.is_file():
            return candidate
    return None


# --- Committed-config secret guard (infra-config-separation AC7) -------------
# This repo's `apps/infra/scripts/config*.env` files are committed (per-deployer
# values live in the gitignored config.local.env). Guard the *tracked* config files
# against a real subscriber email / IAM role ARN landing in history. A targeted
# guard, not a repo-wide scanner -- the full gitleaks/shellcheck CI is deferred
# (docs/backlog.md `infra-secret-scan-ci`).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_ROLE_ARN_RE = re.compile(r"arn:aws:iam::([^:\s]+):role/")
# RFC-2606 reserved domains are documentation placeholders, not real addresses.
_PLACEHOLDER_EMAIL_DOMAINS = {"example.com", "example.org", "example.net"}


def _is_placeholder_email(addr: str) -> bool:
    # Suffix match: the reserved domain itself or any subdomain of it is a
    # placeholder (`you@example.com`, `ops@foo.example.com`), but a lookalike
    # suffix like `you@example.com.attacker.io` is NOT (fails closed).
    domain = addr.rsplit("@", 1)[-1].lower()
    return any(
        domain == d or domain.endswith("." + d) for d in _PLACEHOLDER_EMAIL_DOMAINS
    )


def _is_placeholder_arn(account: str) -> bool:
    # An all-zero account or an angle-bracket token (`<acct>`) is a placeholder.
    return account == "000000000000" or account.startswith("<")


def _scan_config_text(rel: str, text: str) -> list[str]:
    """Pure secret-scan of one config file's *text* — no git, no I/O. Returns
    human-readable findings for any non-placeholder email address or IAM role ARN."""
    findings: list[str] = []
    for m in _EMAIL_RE.finditer(text):
        if not _is_placeholder_email(m.group(0)):
            findings.append(f"{rel}: non-placeholder email '{m.group(0)}'")
    for m in _ROLE_ARN_RE.finditer(text):
        if not _is_placeholder_arn(m.group(1)):
            findings.append(f"{rel}: non-placeholder IAM role ARN (account {m.group(1)})")
    return findings


def _tracked_config_files(repo_root: Path) -> list[str]:
    """The tracked ``apps/infra/scripts/config*`` files (repo-relative paths). The
    gitignored ``config.local.env`` is never tracked, so it is excluded by construction."""
    listed = subprocess.run(
        ["git", "ls-files", "apps/infra/scripts/config*"],
        capture_output=True, text=True, check=False, cwd=repo_root,
    )
    return listed.stdout.split()


def _committed_config_secret_findings(repo_root: Path) -> list[str]:
    """Scan tracked ``apps/infra/scripts/config*`` files for a non-placeholder email
    address or IAM role ARN. Returns a list of human-readable findings (empty = clean)."""
    findings: list[str] = []
    for rel in _tracked_config_files(repo_root):
        try:
            text = (repo_root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        findings.extend(_scan_config_text(rel, text))
    return findings


def _repo_root() -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except FileNotFoundError:
        pass
    return Path.cwd()


def _run(label: str, argv: list[str]) -> None:
    """Run *argv*; on non-zero exit, surface the tool's output, print the
    failure line, and ``sys.exit(1)``. On success, print the success line.

    A missing executable/script is treated as a **skip** (not a failure) so a
    fresh adopter tree that hasn't wired a given gate yet doesn't hard-crash.
    """
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        # To stderr (not stdout) so a *wired-but-mistyped* tool is visually
        # distinct from a passing check and doesn't scroll past as a ✓.
        print(f"pre-pr: — {label} skipped (not found: {argv[0]})", file=sys.stderr)
        return
    if result.returncode != 0:
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        print(f"pre-pr: ✖ {label} failed", file=sys.stderr)
        sys.exit(1)
    print(f"pre-pr: ✓ {label}")


def main() -> int:
    repo_root = _repo_root()
    os.chdir(repo_root)

    py = sys.executable  # use the parent interpreter for child scripts

    # --- Work-loop caps gate (ships with `core`) -----------------------------
    loop_cohort = _find_loop_cohort()
    state_files = sorted(Path("docs/specs").glob("*/state.json"))
    if loop_cohort is None:
        print("pre-pr: — loop-cohort.py not found — skipping work-loop caps check")
    elif not state_files:
        print("pre-pr: (no active state.json — skipping loop-cohort check)")
    else:
        for state in state_files:
            spec_dir = state.parent
            for phase in ("implement", "review"):
                result = subprocess.run(
                    [py, str(loop_cohort), "check", str(spec_dir), "--phase", phase],
                    capture_output=True, text=True, check=False,
                )
                if result.returncode != 0:
                    if result.stdout:
                        sys.stdout.write(result.stdout)
                    if result.stderr:
                        sys.stderr.write(result.stderr)
                    print(
                        f"pre-pr: ✖ loop-cohort check {spec_dir} --phase {phase} failed",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                print(f"pre-pr: ✓ loop-cohort check {spec_dir} ({phase})")

    # --- Committed-config secret guard (infra-config-separation AC7) ---------
    secret_findings = _committed_config_secret_findings(repo_root)
    if secret_findings:
        for finding in secret_findings:
            print(f"pre-pr: ✖ committed-config secret — {finding}", file=sys.stderr)
        print(
            "pre-pr: ✖ committed-config secret guard failed "
            "(put per-deployer values in the gitignored config.local.env)",
            file=sys.stderr,
        )
        sys.exit(1)
    print("pre-pr: ✓ committed-config secret guard")

    # --- This project's gate: ruff (lint + format + S security), mypy, pytest ---
    # Silence the CDK/jsii "untested node version" warning during the synth test.
    os.environ.setdefault("JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION", "1")
    _run("lint", ["ruff", "check", "packages", "apps"])
    _run("format", ["ruff", "format", "--check", "packages", "apps"])
    _run("typecheck", ["mypy", "packages/graphrag/src", "apps"])
    _run("test", [py, "-m", "pytest", "-q"])

    print("pre-pr: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
