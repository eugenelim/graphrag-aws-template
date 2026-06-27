# KEP-1287: In-place Update of Pod Resources

## Summary

Allows a Pod's CPU and memory `resources` to be changed without restarting the
Pod's containers, by making `resources` mutable and adding an actuated state the
Kubelet reconciles.

## Risks and Mitigations

In-place resize interacts with the scheduler, the eviction manager, and the
container runtime; the KEP bounds the feature behind a feature gate and a
per-container resize policy.

This proposal supersedes the earlier legacy node-allocatable design proposal,
KEP-0009, whose static reservation model it replaces.
