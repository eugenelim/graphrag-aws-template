"""AC10 — the before/after delta demo, driven from REAL git history via scripts/delta-demo.sh.

Builds a throwaway git repo of corpus files, commits a base state and a delta state
(add + change + delete + move), then runs the demo over the two commits and asserts the
narratable report classifies the delta and lists the orphans removed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]
SCRIPT = REPO_ROOT / "scripts" / "delta-demo.sh"
FIXTURE_CORPUS = Path(__file__).parent / "fixtures" / "corpus"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _rev_parse(repo: Path, ref: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref], check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def test_delta_demo_over_real_git_history(tmp_path: Path) -> None:
    repo = tmp_path / "corpus-repo"
    shutil.copytree(FIXTURE_CORPUS, repo)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "demo@example.com")
    _git(repo, "config", "user.name", "Demo")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base snapshot")
    base_sha = _rev_parse(repo, "HEAD")

    keps = repo / "enhancements" / "keps"
    # add
    new_kep = keps / "sig-node" / "4242-brand-new"
    new_kep.mkdir(parents=True)
    (new_kep / "kep.yaml").write_text(
        "kep-number: 4242\ntitle: New\nstatus: provisional\nowning-sig: sig-node\n",
        encoding="utf-8",
    )
    (new_kep / "README.md").write_text("# New\n\nProse.\n", encoding="utf-8")
    # delete
    shutil.rmtree(keps / "sig-network" / "1880-multiple-service-cidrs")
    # change (a kep.yaml)
    changed = keps / "sig-network" / "2086-service-internal-traffic-policy" / "kep.yaml"
    changed.write_text(changed.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")
    # move (rename a KEP dir — same content, new path)
    (keps / "sig-node" / "1287-in-place-update-pod-resources").rename(
        keps / "sig-node" / "1287-in-place-pod-resize"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "delta snapshot")

    result = subprocess.run(
        ["bash", str(SCRIPT), str(repo), base_sha, "HEAD"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "== delta demo ==" in out
    assert "BEFORE (base snapshot):" in out
    # The classified delta is named, not hidden (charter principle 1).
    assert "added:" in out and "changed:" in out and "deleted:" in out and "moved:" in out
    assert "orphans removed:" in out
    # The deleted KEP's orphan is gone; the added KEP appears; a move is reported.
    assert any(line.startswith("  + ") and "4242-brand-new" in line for line in out.splitlines())
    assert any(line.startswith("  - ") and "1880" in line for line in out.splitlines())
    assert any(line.startswith("  > ") for line in out.splitlines())


def test_demo_script_usage_on_bad_args() -> None:
    result = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 2
    assert "usage:" in result.stderr
