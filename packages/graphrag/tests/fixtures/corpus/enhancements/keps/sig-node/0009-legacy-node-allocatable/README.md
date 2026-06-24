# KEP-0009: Node Allocatable Resources (legacy design proposal)

**Authors:** Tim Hockin

> This is a pre-`kep.yaml` design proposal: its metadata lives only in the prose,
> by display name, not as a `@handle` in a structured file. It exercises the
> alias table (display-name ↔ handle) the de-risk verdict flagged. The KEP number
> (0009) and owning SIG (sig-node) are derived from the directory path.

## Summary

Reserves a portion of node resources for system daemons and the Kubelet so that
user Pods cannot starve the node, exposing the remainder as "allocatable".
