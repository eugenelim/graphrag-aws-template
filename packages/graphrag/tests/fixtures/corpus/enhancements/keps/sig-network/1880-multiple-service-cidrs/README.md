# KEP-1880: Multiple Service CIDRs

## Summary

Allows a cluster to define more than one Service IP range (CIDR), so operators can
grow the Service network without recreating the cluster, using new `ServiceCIDR`
and `IPAddress` API objects.

## Motivation

The Service CIDR is fixed at cluster creation today; exhausting it is operationally
painful. This KEP makes the range extensible at runtime.
