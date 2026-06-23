# Architecture Decision Records

> Immutable records of architectural decisions — *why* we chose X over Y. See
> [`../CONVENTIONS.md`](../CONVENTIONS.md) for what goes here and what doesn't.
> ADRs are frozen once Accepted; a later decision supersedes, it never edits.

| #    | Title                                                                 | Status   |
| ---- | --------------------------------------------------------------------- | -------- |
| [0001](0001-hybrid-orchestration-seed-and-expand.md) | Hybrid retrieval is one *seed-and-expand* orchestration, not single-direction or parallel-merge | Accepted |
| [0002](0002-ephemeral-vpc-store-topology.md) | The demo stack is an ephemeral, teardown-first VPC topology | Accepted |

## Adding a new ADR

Copy the lean MADR-aligned shape from an existing ADR (title names *problem +
chosen solution*; sections: Context, Decision, Decision drivers, Consequences,
Confirmation, Alternatives considered, References). Use the next zero-padded
ordinal and a kebab-case title.
