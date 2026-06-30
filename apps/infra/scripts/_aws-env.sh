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

# The CDK CLI is a Node program. On a long teardown (Neptune + OpenSearch + VPC/ENIs delete
# for >5 min) its CloudFormation stack-monitor long-polls; on an UNSUPPORTED Node major the
# bundled aws-sdk-js (@smithy) keep-alive socket gets dropped and the SDK reads a half-closed
# socket -> `read ENOTCONN`, killing the client mid-delete (CloudFormation still finishes
# server-side). The toolchain supports Node 20/22/24. Two guards:
#
#   1. If the active `node` is an unsupported major, prefer an already-installed supported one
#      (nvm or Homebrew node@NN) by prepending its bin dir to PATH; warn if none is found.
#   2. Disable SDK HTTP keep-alive reuse so each request opens a fresh socket — a half-open
#      pooled socket is never reused, which is the ENOTCONN-on-reuse failure mode.
_gr_node_major() { node --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/'; }
case "$(_gr_node_major)" in
  20 | 22 | 24) : ;;  # already a supported major
  *)
    for _gr_bin in "$HOME"/.nvm/versions/node/v2[024].*/bin /opt/homebrew/opt/node@2[024]/bin; do
      [ -x "$_gr_bin/node" ] || continue
      case "$("$_gr_bin/node" --version 2>/dev/null)" in
        v20.* | v22.* | v24.*) PATH="$_gr_bin:$PATH"; export PATH; break ;;
      esac
    done
    case "$(_gr_node_major)" in
      20 | 22 | 24) echo "info: pinned Node $(node --version) for the CDK toolchain (cdk-supported)." >&2 ;;
      *) echo "WARNING: Node $(node --version 2>/dev/null) is not CDK-supported (use 20/22/24). Long teardowns may drop the stack monitor (read ENOTCONN) — CloudFormation still completes server-side; re-issue + verify via the API. Install e.g. 'nvm install 22'." >&2 ;;
    esac
    ;;
esac
unset -f _gr_node_major 2>/dev/null || true
# Fresh socket per request (avoids reusing an idle-dropped keep-alive socket -> ENOTCONN).
export AWS_NODEJS_CONNECTION_REUSE_ENABLED=0
