#!/usr/bin/env bash
# Shared setup for the deploy/destroy/status scripts. Source it, don't run it.
#
# Lesson baked in (learned the hard way): a per-command credential_process / SSO
# provider gets RATE-LIMITED if every aws/cdk invocation re-resolves credentials.
# So resolve ONCE into a session cache file (mode 600) and source the static keys
# for the rest of the session. Refresh only when the cache is missing or older
# than 50 min (session tokens are typically ~1h). Set REFRESH_CREDS=1 to force.
set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CREDS_CACHE="${CREDS_CACHE:-${TMPDIR:-/tmp}/gr-aws-creds.env}"

if [ "${REFRESH_CREDS:-0}" = "1" ] || [ ! -s "$CREDS_CACHE" ] \
   || [ -n "$(find "$CREDS_CACHE" -mmin +50 2>/dev/null)" ]; then
  # One upstream auth resolution for the whole session.
  ( umask 077; aws configure export-credentials --format env > "$CREDS_CACHE" )
fi
# shellcheck disable=SC1090
. "$CREDS_CACHE"
unset AWS_PROFILE 2>/dev/null || true   # static keys win; an empty profile confuses cdk

export AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export CDK_DEFAULT_REGION="$AWS_REGION"
export CDK_DEFAULT_ACCOUNT="${CDK_DEFAULT_ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}"
export JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1

VENV="${VENV:-$INFRA_DIR/../../.venv}"
CDK_APP="${CDK_APP:-$VENV/bin/python $INFRA_DIR/app.py}"
STACK="${STACK:-GraphragSlice1}"
