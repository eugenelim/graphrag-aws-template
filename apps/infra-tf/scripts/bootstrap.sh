#!/usr/bin/env bash
# bootstrap.sh — one-time S3 state-bucket creation + terraform init.
#
# Run once per AWS account before any terraform apply. Safe to re-run —
# all bucket hardening calls are idempotent.
# Prerequisites: AWS CLI configured with credentials for the target account.
#
# Usage:
#   ./scripts/bootstrap.sh <bucket-name> [region]
#
# Example:
#   ./scripts/bootstrap.sh my-tf-state-bucket us-east-1

set -euo pipefail

BUCKET="${1:?Usage: bootstrap.sh <bucket-name> [region]}"
REGION="${2:-us-east-1}"

# Validate inputs before touching any AWS resources.
if ! [[ "${BUCKET}" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]]; then
  echo "ERROR: Bucket name '${BUCKET}' is not a valid S3 bucket name." >&2
  exit 1
fi
if ! [[ "${REGION}" =~ ^[a-z]{2}-[a-z]+-[0-9]$ ]]; then
  echo "ERROR: Region '${REGION}' does not match a commercial AWS region (e.g. us-east-1)." >&2
  echo "       GovCloud and China regions are not supported by this demo." >&2
  exit 1
fi

# Guard: confirm AWS credentials are configured before touching anything.
echo "Verifying AWS credentials..."
ACCOUNT=$(aws sts get-caller-identity --output text --query 'Account' 2>/dev/null || true)
if [ -z "${ACCOUNT}" ]; then
  echo "ERROR: AWS credentials are not configured or are expired." >&2
  echo "       Run 'aws configure' or set AWS_PROFILE / AWS_ACCESS_KEY_ID." >&2
  exit 1
fi
echo "Account: ${ACCOUNT}, region: ${REGION}"

# Create the S3 state bucket if it does not already exist.
# head-bucket returns: 0 (exists + accessible), 404 (does not exist), 403 (exists but taken by another account).
HEAD_EXIT=0
aws s3api head-bucket --bucket "${BUCKET}" 2>/dev/null || HEAD_EXIT=$?
if [ "${HEAD_EXIT}" -eq 0 ]; then
  echo "Bucket s3://${BUCKET} already exists."
elif [ "${HEAD_EXIT}" -eq 403 ]; then
  echo "ERROR: Bucket name '${BUCKET}' already exists in another AWS account." >&2
  echo "       S3 bucket names are globally unique. Choose a different name." >&2
  exit 1
else
  echo "Creating s3://${BUCKET} in ${REGION}..."
  if [ "${REGION}" = "us-east-1" ]; then
    # us-east-1 must NOT include --create-bucket-configuration (AWS API quirk).
    aws s3api create-bucket \
      --bucket "${BUCKET}" \
      --region "${REGION}"
  else
    aws s3api create-bucket \
      --bucket "${BUCKET}" \
      --region "${REGION}" \
      --create-bucket-configuration "LocationConstraint=${REGION}"
  fi
  echo "Bucket created."
fi

# Apply hardening idempotently on every run — covers re-runs, partial failures,
# and buckets that pre-existed without these controls.

echo "Applying bucket hardening..."

# Enable versioning so state history is preserved.
aws s3api put-bucket-versioning \
  --bucket "${BUCKET}" \
  --versioning-configuration Status=Enabled

# Block all public access.
aws s3api put-public-access-block \
  --bucket "${BUCKET}" \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# Enforce SSE-S3 encryption at rest for all objects.
aws s3api put-bucket-encryption \
  --bucket "${BUCKET}" \
  --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'

# Enforce TLS-only access (deny any request over HTTP).
aws s3api put-bucket-policy \
  --bucket "${BUCKET}" \
  --policy "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{
      \"Sid\": \"DenyNonTLS\",
      \"Effect\": \"Deny\",
      \"Principal\": \"*\",
      \"Action\": \"s3:*\",
      \"Resource\": [
        \"arn:aws:s3:::${BUCKET}\",
        \"arn:aws:s3:::${BUCKET}/*\"
      ],
      \"Condition\": {\"Bool\": {\"aws:SecureTransport\": \"false\"}}
    }]
  }"

echo "Bucket hardening applied."

# Write a backend.hcl for terraform init (derived from arguments; not committed).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_HCL="${SCRIPT_DIR}/../backend.hcl"
cat > "${BACKEND_HCL}" <<EOF
bucket = "${BUCKET}"
key    = "graphrag-aws-template/terraform.tfstate"  # mirrored in backend.hcl.example
region = "${REGION}"
EOF
echo "Wrote ${BACKEND_HCL}"

# Initialise Terraform with the S3 backend.
cd "${SCRIPT_DIR}/.."
terraform init -backend-config=backend.hcl
echo "terraform init complete."
