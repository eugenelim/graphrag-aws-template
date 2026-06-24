#!/usr/bin/env bash
# Shared bootstrap for the deploy/destroy/status scripts. Source it, don't run it.
#
# Two responsibilities, both LOGIC -- every tunable lives in config.env:
#   1. Load build parameters & resource names: config.local.env (if present), then
#      config.env. Precedence: explicit env var > config.local.env > config.env.
#   2. Resolve AWS credentials ONCE into a session cache (mode 600, via umask 077), so
#      a per-command credential_process / SSO provider is not re-resolved on every
#      aws/cdk call -- which RATE-LIMITS the auth provider. Refresh only when the cache
#      is missing or older than CREDS_MAX_AGE_MIN minutes (session tokens are ~1h). Set
#      REFRESH_CREDS=1 to force a refresh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Build parameters & resource names. config.local.env is sourced first (and only if it
# exists -- the guard keeps a missing file from aborting under `set -e`); both files use
# `: "${VAR:=...}"`, so the precedence above holds.
if [ -f "$SCRIPT_DIR/config.local.env" ]; then
  # shellcheck source=/dev/null
  . "$SCRIPT_DIR/config.local.env"
fi
# shellcheck source=apps/infra/scripts/config.env
. "$SCRIPT_DIR/config.env"

if [ "${REFRESH_CREDS:-0}" = "1" ] || [ ! -s "$CREDS_CACHE" ] \
   || [ -n "$(find "$CREDS_CACHE" -mmin "+${CREDS_MAX_AGE_MIN}" 2>/dev/null)" ]; then
  # One upstream auth resolution for the whole session.
  ( umask 077; aws configure export-credentials --format env > "$CREDS_CACHE" )
fi
# shellcheck disable=SC1090
. "$CREDS_CACHE"
unset AWS_PROFILE 2>/dev/null || true   # static keys win; an empty profile confuses cdk

# Export the vars the aws/cdk child processes read. config.env owns AWS_REGION's value
# and fallback chain; here we only export it (a sourced `:=` assigns but does not export).
export AWS_REGION
export CDK_DEFAULT_REGION="$AWS_REGION"
export CDK_DEFAULT_ACCOUNT="${CDK_DEFAULT_ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}"
export JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1
