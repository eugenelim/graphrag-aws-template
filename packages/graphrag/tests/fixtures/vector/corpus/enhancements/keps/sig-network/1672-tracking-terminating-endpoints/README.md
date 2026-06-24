# KEP-1672: Tracking Terminating Endpoints in EndpointSlices

## Summary

Adds a `terminating` condition to EndpointSlice endpoints so that consumers like
kube-proxy can distinguish endpoints that are shutting down from ready ones.

## Motivation

During a rolling update, terminating Pods are removed from EndpointSlices abruptly,
which can drop in-flight connections; exposing terminating state lets proxies drain
connections gracefully.
