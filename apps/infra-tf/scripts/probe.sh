#!/usr/bin/env bash
# probe.sh — Live end-to-end verification for the graphrag Terraform stack.
#
# Reads Terraform outputs, waits for Neptune and OpenSearch to be AVAILABLE,
# invokes the 3 Lambda probes, asserts success responses, and emits a report.
# Does NOT call terraform destroy — lifecycle ownership stays with the operator.
#
# Prerequisites: terraform apply must have been run from apps/infra-tf/.
# Usage:
#   cd apps/infra-tf
#   bash scripts/probe.sh
#
# Exit codes: 0 = all probes passed; 1 = readiness timeout or probe failure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

NEPTUNE_POLL_MAX=30    # max 30 × 60s = 30 min
OPENSEARCH_POLL_MAX=30
POLL_INTERVAL=60

# ── 1. Read outputs ────────────────────────────────────────────────────────────
echo "==> Reading Terraform outputs..."
OUTPUTS=$(terraform -chdir="${INFRA_DIR}" output -json)

SMOKE_NAME=$(echo "${OUTPUTS}" | jq -r '.smoke_probe_name.value')
VECTOR_NAME=$(echo "${OUTPUTS}" | jq -r '.vector_smoke_probe_name.value')
QUERY_NAME=$(echo "${OUTPUTS}" | jq -r '.query_lambda_name.value')
NEPTUNE_CLUSTER_ID=$(echo "${OUTPUTS}" | jq -r '.neptune_endpoint.value' \
  | sed 's|https://||' | sed 's|:8182||')

echo "  smoke_probe_name       = ${SMOKE_NAME}"
echo "  vector_smoke_probe_name = ${VECTOR_NAME}"
echo "  query_lambda_name       = ${QUERY_NAME}"

# ── 2. Wait for Neptune ────────────────────────────────────────────────────────
echo "==> Waiting for Neptune cluster to be available..."
NEPTUNE_READY=0
for i in $(seq 1 "${NEPTUNE_POLL_MAX}"); do
  STATUS=$(aws neptune describe-db-clusters \
    --query "DBClusters[?Endpoint=='${NEPTUNE_CLUSTER_ID}'].Status | [0]" \
    --output text 2>/dev/null || echo "unknown")
  echo "  [${i}/${NEPTUNE_POLL_MAX}] Neptune status = ${STATUS}"
  if [ "${STATUS}" = "available" ]; then
    NEPTUNE_READY=1
    break
  fi
  sleep "${POLL_INTERVAL}"
done
if [ "${NEPTUNE_READY}" -ne 1 ]; then
  echo "ERROR: Neptune cluster did not reach 'available' within $((NEPTUNE_POLL_MAX * POLL_INTERVAL / 60)) minutes" >&2
  exit 1
fi
echo "  Neptune is available."

# ── 3. Wait for OpenSearch ─────────────────────────────────────────────────────
echo "==> Waiting for OpenSearch domain to be available..."
OPENSEARCH_READY=0
for i in $(seq 1 "${OPENSEARCH_POLL_MAX}"); do
  PROCESSING=$(aws opensearch describe-domain \
    --domain-name "graphrag-vectors" \
    --query "DomainStatus.Processing" \
    --output text 2>/dev/null || echo "True")
  echo "  [${i}/${OPENSEARCH_POLL_MAX}] OpenSearch Processing = ${PROCESSING}"
  if [ "${PROCESSING}" = "False" ]; then
    OPENSEARCH_READY=1
    break
  fi
  sleep "${POLL_INTERVAL}"
done
if [ "${OPENSEARCH_READY}" -ne 1 ]; then
  echo "ERROR: OpenSearch domain did not reach Processing=False within $((OPENSEARCH_POLL_MAX * POLL_INTERVAL / 60)) minutes" >&2
  exit 1
fi
echo "  OpenSearch is available."

# ── 4. Invoke SmokeProbe (Neptune insert + retrieve + delete) ─────────────────
echo "==> Invoking SmokeProbe Lambda (${SMOKE_NAME})..."
aws lambda invoke \
  --function-name "${SMOKE_NAME}" \
  --log-type Tail \
  /tmp/probe_smoke_out.json \
  --query 'FunctionError' --output text > /tmp/probe_smoke_err.txt
if grep -q '"ok": true' /tmp/probe_smoke_out.json; then
  echo "  SmokeProbe: PASS"
else
  echo "FAIL: SmokeProbe did not return ok=true" >&2
  echo "Response: $(cat /tmp/probe_smoke_out.json)" >&2
  exit 1
fi

# ── 5. Invoke VectorSmokeProbe (embed → index → retrieve) ────────────────────
echo "==> Invoking VectorSmokeProbe Lambda (${VECTOR_NAME})..."
aws lambda invoke \
  --function-name "${VECTOR_NAME}" \
  --log-type Tail \
  /tmp/probe_vector_out.json \
  --query 'FunctionError' --output text > /tmp/probe_vector_err.txt
if grep -q '"ok": true' /tmp/probe_vector_out.json; then
  echo "  VectorSmokeProbe: PASS"
else
  echo "FAIL: VectorSmokeProbe did not return ok=true" >&2
  echo "Response: $(cat /tmp/probe_vector_out.json)" >&2
  exit 1
fi

# ── 6. Invoke QueryLambda via Lambda invoke API ───────────────────────────────
# Uses Lambda invoke (not direct Function URL HTTP) so no SigV4 signing is needed
# from bash. The Function URL auth_type=AWS_IAM is tested by the plan-assertion suite.
echo "==> Invoking QueryLambda (${QUERY_NAME})..."
# AWS CLI v2 requires --cli-binary-format for raw JSON payloads (not base64).
echo '{"query": "probe"}' > /tmp/probe_query_payload.json
aws lambda invoke \
  --function-name "${QUERY_NAME}" \
  --payload fileb:///tmp/probe_query_payload.json \
  --log-type Tail \
  /tmp/probe_query_out.json \
  --query 'FunctionError' --output text > /tmp/probe_query_err.txt
QUERY_FUNCTION_ERROR=$(cat /tmp/probe_query_err.txt)
if [ "${QUERY_FUNCTION_ERROR}" = "None" ] || [ -z "${QUERY_FUNCTION_ERROR}" ]; then
  echo "  QueryLambda: PASS"
else
  echo "FAIL: QueryLambda returned FunctionError=${QUERY_FUNCTION_ERROR}" >&2
  echo "Response: $(cat /tmp/probe_query_out.json)" >&2
  exit 1
fi

# ── 7. Report ──────────────────────────────────────────────────────────────────
echo ""
echo "==================================================="
echo " ALL PROBES PASSED"
echo "  SmokeProbe      (Neptune)    : PASS"
echo "  VectorSmokeProbe (OpenSearch) : PASS"
echo "  QueryLambda      (hybrid)     : PASS"
echo "==================================================="
echo ""
echo "Stack is functionally verified. Run 'terraform destroy' from apps/infra-tf/ to tear down."
