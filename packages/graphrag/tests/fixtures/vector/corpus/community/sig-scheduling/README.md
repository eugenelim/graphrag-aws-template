---
title: SIG Scheduling Charter
sig: sig-scheduling
---

# SIG Scheduling

Covers the kube-scheduler and the scheduling framework: placement of Pods onto Nodes,
priorities, preemption, and scheduling plugins.

## Scope

SIG Scheduling owns how the control plane decides which Node runs each Pod, including
gating a Pod's readiness to be scheduled at all.
