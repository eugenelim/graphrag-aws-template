# KEP-3331: Structured Authentication Configuration

## Summary

Replaces API server authentication command-line flags with a versioned configuration
file, starting with multiple JWT authenticators for OIDC.

## Motivation

OIDC authentication was configured through many `--oidc-*` flags that allowed only one
provider and could not be changed without an API server restart; a structured config
file supports multiple providers and clearer validation.
