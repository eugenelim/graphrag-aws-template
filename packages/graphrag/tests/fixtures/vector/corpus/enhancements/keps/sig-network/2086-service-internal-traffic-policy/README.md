# KEP-2086: Service Internal Traffic Policy

## Summary

A new field `internalTrafficPolicy` on Services lets cluster operators restrict internal
traffic to endpoints on the same node as the originating Pod, reducing cross-node hops
for node-local workloads.

## Motivation

Some workloads only want to talk to node-local backends, for example a per-node logging
agent. Today a Service routes to all ready endpoints cluster-wide; this KEP adds a
topology-aware internal routing knob.
