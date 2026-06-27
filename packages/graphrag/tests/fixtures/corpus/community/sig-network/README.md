---
title: SIG Network Charter
sig: sig-network
---

# SIG Network

Covers Kubernetes networking — Services, Ingress, the network policy API, kube-proxy,
and DNS. The charter scopes the API surface the SIG owns and the subprojects it
sponsors.

## Scope

SIG Network is responsible for the components, APIs, and tooling that connect Pods
and Services across a cluster and to the outside world.

## Subprojects

The SIG sponsors subprojects such as external-dns and kube-proxy.

## Collaboration

SIG Network collaborates closely with SIG Node on node-local Service routing, where
endpoint topology and the Kubelet's view of the node meet.
