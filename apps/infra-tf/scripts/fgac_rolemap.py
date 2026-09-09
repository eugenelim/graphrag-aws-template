"""fgac_rolemap.py — Lambda handler that applies OpenSearch FGAC role mappings.

Runs INSIDE the VPC. The graphrag-vectors domain is VPC-only, so the security-plugin
API (`_plugins/_security/api/*`) is unreachable from a laptop or from CI — this handler
is the only supported path to it. Deployed, invoked once, and deleted by
scripts/fgac-rolemap.sh; it is not a standing resource.

Why this exists at all: Terraform's AWS provider can enable fine-grained access control
but cannot express role mappings. Without them every workload role gets 403 from the
domain regardless of what its IAM policy or the domain access policy allows — under FGAC
the security plugin authorizes independently, and an identity-policy grant buys nothing.

Role ARNs arrive via env var rather than being hardcoded: the Terraform-generated name
suffixes change on every domain/role rebuild, so a literal ARN list would silently rot.

Env:
  OS_ENDPOINT     https://vpc-<domain>-<hash>.<region>.es.amazonaws.com
  WORKLOAD_ROLES  comma-separated IAM role ARNs -> graphrag_workload (read+write)
  SEARCH_ROLES    comma-separated IAM role ARNs -> graphrag_search   (read-only)
"""

import json
import os
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

HOST = os.environ["OS_ENDPOINT"].rstrip("/")
REGION = os.environ.get("AWS_REGION", "us-east-1")
_SESSION = boto3.Session()

# Fail closed on the scheme at import rather than trusting the env var. urlopen would
# otherwise honour `file:` (or any custom scheme), turning a mis-set OS_ENDPOINT into a
# local file read inside the VPC under a role holding OpenSearch cluster-admin.
if not HOST.startswith("https://"):
    raise ValueError(f"OS_ENDPOINT must be an https:// URL, got: {HOST!r}")

# Two roles, not one: the workload roles hold es:ESHttpGet/Put/Post/Delete/Head while
# the MCP lambda holds only Get/Post/Head. Mapping everything to all_access would restore
# service faster but make FGAC decorative, which defeats the point of enabling it.
ROLE_DEFS = {
    "graphrag_workload": {
        "cluster_permissions": ["cluster_composite_ops", "cluster_monitor"],
        "index_permissions": [
            {
                "index_patterns": ["*"],
                "allowed_actions": ["crud", "create_index", "indices_monitor"],
            }
        ],
    },
    "graphrag_search": {
        "cluster_permissions": ["cluster_composite_ops_ro", "cluster_monitor"],
        "index_permissions": [{"index_patterns": ["*"], "allowed_actions": ["read", "search"]}],
    },
}


def _arns(var: str) -> list[str]:
    return [a.strip() for a in os.environ.get(var, "").split(",") if a.strip()]


def _call(method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    """Signed request to the domain. Returns (status, truncated body)."""
    url = HOST + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}

    signed = AWSRequest(method=method, url=url, data=data, headers=headers)
    SigV4Auth(_SESSION.get_credentials().get_frozen_credentials(), "es", REGION).add_auth(signed)

    # S310: the https:// scheme is enforced at import (see HOST above) and `url` is
    # built only from that constant plus a literal path, so no untrusted scheme reaches
    # urlopen.
    req = urllib.request.Request(  # noqa: S310
        url, data=data, method=method, headers=dict(signed.headers)
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return resp.status, resp.read().decode()[:1200]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:1200]
    except Exception as exc:  # noqa: BLE001 — surface transport errors as a result row
        return -1, repr(exc)[:600]


def handler(event, _context):
    # probe_only verifies VPC reachability and SigV4 signing without mutating anything.
    # Worth running before enabling FGAC: it is the one part of this that can be tested
    # in advance, since the security API does not exist until FGAC is on.
    if event.get("probe_only"):
        return {"results": [["probe", "/", *_call("GET", "/")]]}

    mappings = {
        "graphrag_workload": _arns("WORKLOAD_ROLES"),
        "graphrag_search": _arns("SEARCH_ROLES"),
    }
    missing = [name for name, arns in mappings.items() if not arns]
    if missing:
        return {"error": f"no ARNs supplied for: {', '.join(missing)}", "results": []}

    results = []
    for name, body in ROLE_DEFS.items():
        results.append(["role", name, *_call("PUT", f"/_plugins/_security/api/roles/{name}", body)])
    for name, arns in mappings.items():
        results.append(
            [
                "mapping",
                name,
                *_call(
                    "PUT",
                    f"/_plugins/_security/api/rolesmapping/{name}",
                    {"backend_roles": arns},
                ),
            ]
        )
    results.append(
        ["verify", "rolesmapping", *_call("GET", "/_plugins/_security/api/rolesmapping")]
    )

    ok = all(r[2] in (200, 201) for r in results)
    return {"ok": ok, "results": results}
