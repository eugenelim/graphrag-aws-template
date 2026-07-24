"""Session-scoped tfplan fixture for the Terraform plan-assertion suite.

Loads plan from TFPLAN_JSON_PATH env var (CI / committed fixture) or
generates a fresh plan live. Fresh-plan mode: computed attributes
(Neptune cluster_resource_id, S3 bucket name, role ARNs) are null;
use the committed applied-state fixture for full-coverage assertions.
"""

import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def tfplan(tmp_path_factory):
    override = os.environ.get("TFPLAN_JSON_PATH")
    if override:
        return json.loads(Path(override).read_text())

    terraform_bin = os.environ.get("TERRAFORM_BIN", "terraform")
    if not terraform_bin or not _has_terraform(terraform_bin):
        pytest.skip("terraform not found; set TERRAFORM_BIN or TFPLAN_JSON_PATH")

    infra_dir = Path(__file__).parent.parent
    plan_file = tmp_path_factory.mktemp("tfplan") / "plan.tfplan"

    # Stub zip allows plan to compute filebase64sha256; cleared after plan.
    lambda_zip = (infra_dir / "../graphrag/dist/graphrag.zip").resolve()
    stub_created = False
    if not lambda_zip.exists():
        lambda_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(lambda_zip, "w") as zf:
            zf.writestr("stub.txt", "stub")
        stub_created = True

    try:
        subprocess.run(
            [terraform_bin, "init", "-backend=false", "-input=false"],
            cwd=infra_dir,
            check=True,
        )
        subprocess.run(
            [
                terraform_bin,
                "plan",
                "-out",
                str(plan_file),
                "-input=false",
                "-var=budget_alarm_email=test@example.com",
                "-var=invoker_role_arn=arn:aws:iam::123456789012:role/invoker",
                "-var=mcp_invoker_role_arn=arn:aws:iam::123456789012:role/mcp-invoker",
                (
                    "-var=codestar_connection_arn="
                    "arn:aws:codestar-connections:us-east-1:123456789012:"
                    "connection/12345678-1234-1234-1234-123456789012"  # pragma: allowlist secret
                ),
                "-var=github_repo_id=owner/repo",
            ],
            cwd=infra_dir,
            check=True,
        )
    finally:
        if stub_created:
            lambda_zip.unlink(missing_ok=True)

    result = subprocess.run(
        [terraform_bin, "show", "-json", str(plan_file)],
        cwd=infra_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _has_terraform(terraform_bin: str) -> bool:
    try:
        subprocess.run([terraform_bin, "version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
