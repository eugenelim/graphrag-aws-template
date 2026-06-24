# KEP-2400: Kubelet Swap Memory Support

## Summary

Lets the Kubelet run with swap enabled on the node so that workloads can use swap
memory, instead of requiring swap to be disabled cluster-wide.

## Motivation

Many production workloads tolerate swap and node operators want to use it for memory
overcommit and graceful handling of memory pressure, but Kubernetes historically
required swap off. This KEP defines swap behavior per QoS class.
