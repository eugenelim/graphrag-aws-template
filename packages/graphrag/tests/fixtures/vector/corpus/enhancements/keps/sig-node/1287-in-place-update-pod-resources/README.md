# KEP-1287: In-place Update of Pod Resources

## Summary

Allows a Pod's CPU and memory `resources` to be changed without restarting the Pod's
containers, by making `resources` mutable and adding an actuated state the Kubelet
reconciles against the container runtime.

## Risks and Mitigations

In-place resize interacts with the scheduler, the eviction manager, and the container
runtime. A resize can be rejected or deferred if the node lacks capacity; the feature
is bounded behind a feature gate and a per-container resize policy so a failed actuation
degrades gracefully rather than restarting the workload.
