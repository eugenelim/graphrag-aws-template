# KEP-3521: Pod Scheduling Readiness

## Summary

Adds `schedulingGates` to a Pod so the scheduler skips it until all gates are cleared,
giving external controllers a way to hold a Pod back before placement.

## Motivation

Workloads sometimes need quota, dependencies, or topology to be ready before a Pod
should be considered for scheduling; without a gate the scheduler keeps retrying a Pod
that can never be placed yet.
