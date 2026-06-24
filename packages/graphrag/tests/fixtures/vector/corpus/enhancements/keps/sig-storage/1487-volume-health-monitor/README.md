# KEP-1487: CSI Volume Health Monitoring

## Summary

Adds a mechanism for CSI drivers to report the health of provisioned volumes, surfacing
abnormal volume conditions as events on the PersistentVolumeClaim and the Pod.

## Motivation

A volume can become unhealthy after provisioning — the backing disk fails, fills up, or
is detached out of band — and today Kubernetes has no signal for it. Health monitoring
detects and reports these conditions.
