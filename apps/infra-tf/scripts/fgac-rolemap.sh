#!/usr/bin/env bash
# fgac-rolemap.sh — Apply OpenSearch fine-grained-access-control role mappings.
#
# MUST be run after every `terraform apply` that creates or replaces the
# graphrag-vectors domain. Terraform enables FGAC but cannot express role mappings,
# and the domain is VPC-only so the security API is unreachable from here — so this
# script stands up a throwaway in-VPC Lambda, applies the mappings, and deletes it.
#
# Until it runs, all four workload roles get 403 from the domain and the stack is
# functionally down. This is not optional cleanup; it is part of deploy.
#
# Prerequisites: terraform apply has completed; the FGAC master role exists and is set
# as var.opensearch_master_user_arn (it doubles as this Lambda's execution role).
#
# Usage:
#   cd apps/infra-tf
#   bash scripts/fgac-rolemap.sh
#
# Exit codes: 0 = all mappings applied; 1 = discovery, deploy, or mapping failure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DOMAIN="graphrag-vectors"
FN_NAME="graphrag-fgac-rolemap"
MASTER_ROLE_NAME="graphrag-opensearch-master"
ACTIVE_POLL_MAX=20
POLL_INTERVAL=15

WORK_DIR="$(mktemp -d)"
cleanup() {
  # Always remove the Lambda — leaving a standing function with cluster-admin
  # credentials in the VPC is exactly the posture FGAC was enabled to avoid.
  if aws lambda get-function --function-name "${FN_NAME}" >/dev/null 2>&1; then
    echo "==> Deleting ${FN_NAME}..."
    aws lambda delete-function --function-name "${FN_NAME}" || true
  fi
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

# ── 1. Resolve domain networking ───────────────────────────────────────────────
echo "==> Resolving ${DOMAIN} endpoint and VPC placement..."
DOMAIN_JSON=$(aws opensearch describe-domain --domain-name "${DOMAIN}" --output json)

OS_ENDPOINT="https://$(echo "${DOMAIN_JSON}" | jq -r '.DomainStatus.Endpoints.vpc')"
SUBNET_ID=$(echo "${DOMAIN_JSON}" | jq -r '.DomainStatus.VPCOptions.SubnetIds[0]')
FGAC_ON=$(echo "${DOMAIN_JSON}" | jq -r '.DomainStatus.AdvancedSecurityOptions.Enabled')

if [ "${FGAC_ON}" != "true" ]; then
  echo "ERROR: FGAC is not enabled on ${DOMAIN}; nothing to map." >&2
  exit 1
fi

# Reuse the query lambda's security group — it is already in the domain SG's ingress
# allow-list, so this needs no security-group change.
SG_ID=$(aws lambda get-function-configuration \
  --function-name graphrag-query-lambda \
  --query 'VpcConfig.SecurityGroupIds[0]' --output text)

echo "  endpoint = ${OS_ENDPOINT}"
echo "  subnet   = ${SUBNET_ID}"
echo "  sg       = ${SG_ID}"

# ── 2. Discover workload role ARNs by prefix ───────────────────────────────────
# Terraform appends a random suffix to each role name, so these are resolved live
# rather than hardcoded — a literal ARN list would break on the next rebuild.
echo "==> Discovering workload role ARNs..."
resolve_role() {
  aws iam list-roles \
    --query "Roles[?starts_with(RoleName, \`$1\`)].Arn | [0]" --output text
}

INGEST_ARN=$(resolve_role "graphrag-ingestion")
PROBE_ARN=$(resolve_role "graphrag-vector-probe")
QUERY_ARN=$(resolve_role "graphrag-query-")
MCP_ARN=$(resolve_role "graphrag-mcp-lambda")
MASTER_ARN=$(aws iam get-role --role-name "${MASTER_ROLE_NAME}" \
  --query 'Role.Arn' --output text)

for pair in "ingestion:${INGEST_ARN}" "vector-probe:${PROBE_ARN}" \
            "query:${QUERY_ARN}" "mcp-lambda:${MCP_ARN}"; do
  name="${pair%%:*}"; arn="${pair#*:}"
  if [ -z "${arn}" ] || [ "${arn}" = "None" ]; then
    echo "ERROR: could not resolve the ${name} role ARN" >&2
    exit 1
  fi
  echo "  ${name} = ${arn}"
done

WORKLOAD_ROLES="${INGEST_ARN},${PROBE_ARN},${QUERY_ARN}"
SEARCH_ROLES="${MCP_ARN}"

# ── 3. Package and deploy the throwaway Lambda ─────────────────────────────────
echo "==> Packaging ${FN_NAME}..."
cp "${SCRIPT_DIR}/fgac_rolemap.py" "${WORK_DIR}/handler.py"
(cd "${WORK_DIR}" && zip -q fn.zip handler.py)

# A stale function from a failed prior run would silently run old code.
if aws lambda get-function --function-name "${FN_NAME}" >/dev/null 2>&1; then
  echo "  Removing stale ${FN_NAME} from a previous run..."
  aws lambda delete-function --function-name "${FN_NAME}"
fi

echo "==> Deploying ${FN_NAME} into ${SUBNET_ID}..."
aws lambda create-function \
  --function-name "${FN_NAME}" \
  --runtime python3.12 --handler handler.handler \
  --role "${MASTER_ARN}" \
  --zip-file "fileb://${WORK_DIR}/fn.zip" \
  --timeout 120 --memory-size 256 \
  --vpc-config "SubnetIds=${SUBNET_ID},SecurityGroupIds=${SG_ID}" \
  --environment "Variables={OS_ENDPOINT=${OS_ENDPOINT},WORKLOAD_ROLES=${WORKLOAD_ROLES},SEARCH_ROLES=${SEARCH_ROLES}}" \
  --description "One-off: applies FGAC role mappings to ${DOMAIN}" \
  --query 'FunctionName' --output text >/dev/null

echo "==> Waiting for ENI provisioning..."
FN_READY=0
for i in $(seq 1 "${ACTIVE_POLL_MAX}"); do
  STATE=$(aws lambda get-function-configuration --function-name "${FN_NAME}" \
    --query 'State' --output text 2>/dev/null || echo "Pending")
  echo "  [${i}/${ACTIVE_POLL_MAX}] State = ${STATE}"
  if [ "${STATE}" = "Active" ]; then FN_READY=1; break; fi
  sleep "${POLL_INTERVAL}"
done
if [ "${FN_READY}" -ne 1 ]; then
  echo "ERROR: ${FN_NAME} did not reach Active" >&2
  exit 1
fi

# ── 4. Apply the mappings ──────────────────────────────────────────────────────
echo "==> Applying role mappings..."
aws lambda invoke --function-name "${FN_NAME}" \
  --cli-binary-format raw-in-base64-out --payload '{}' \
  "${WORK_DIR}/out.json" --query 'FunctionError' --output text > "${WORK_DIR}/err.txt"

echo ""
jq -r '.results[] | "  \(.[0]) \(.[1]) -> HTTP \(.[2])"' "${WORK_DIR}/out.json" \
  || cat "${WORK_DIR}/out.json"
echo ""

if jq -e '.ok == true' "${WORK_DIR}/out.json" >/dev/null 2>&1; then
  echo "==================================================="
  echo " FGAC ROLE MAPPINGS APPLIED"
  echo "  graphrag_workload : ingestion, vector-probe, query"
  echo "  graphrag_search   : mcp-lambda"
  echo "==================================================="
  echo ""
  echo "Now run 'bash scripts/probe.sh' to confirm the workload roles can reach the domain."
else
  echo "FAIL: one or more mapping calls did not return 200/201" >&2
  echo "Response: $(cat "${WORK_DIR}/out.json")" >&2
  exit 1
fi
