# KEP-0009: Node Allocatable Resources (legacy design proposal)

**Authors:** Tim Hockin

> Pre-`kep.yaml` design proposal: metadata lives in prose by display name.

## Summary

Reserves a portion of node resources for system daemons and the Kubelet so user Pods
cannot starve the node, exposing the remainder as "allocatable" capacity to the
scheduler.
