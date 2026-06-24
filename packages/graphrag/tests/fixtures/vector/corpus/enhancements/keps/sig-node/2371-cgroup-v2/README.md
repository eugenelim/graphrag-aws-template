# KEP-2371: cgroup v2 Support

## Summary

Moves Kubernetes node resource management onto the cgroup v2 unified hierarchy,
replacing the cgroup v1 controllers the Kubelet uses for CPU and memory accounting.

## Motivation

cgroup v1 is being deprecated by Linux distributions; cgroup v2 offers better memory
isolation (including a memory QoS via memory.high) and a single unified hierarchy.
