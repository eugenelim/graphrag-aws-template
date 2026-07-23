# Architecture Decision Records

> Immutable records of architectural decisions — *why* we chose X over Y. See
> [`../CONVENTIONS.md`](../CONVENTIONS.md) for what goes here and what doesn't.
> ADRs are frozen once Accepted; a later decision supersedes, it never edits.

| #    | Title                                                                 | Status   |
| ---- | --------------------------------------------------------------------- | -------- |
| [0001](0001-hybrid-orchestration-seed-and-expand.md) | Hybrid retrieval is one *seed-and-expand* orchestration, not single-direction or parallel-merge | Superseded by RFC-0004 |
| [0002](0002-ephemeral-vpc-store-topology.md) | The demo stack is an ephemeral, teardown-first VPC topology | Accepted |
| [0003](0003-iac-tool-aws-cdk-python.md) | Infrastructure-as-code tool is AWS CDK (Python) | Accepted |
| [0004](0004-text2cypher-read-only-guard.md) | Read-only guard for LLM-authored openCypher: IAM data-action scoping over a read-replica endpoint | Superseded by ADR-0011 |
| [0005](0005-community-detection-in-fargate-louvain.md) | Community detection runs in the Fargate ingest task (Louvain via networkx), not a standing Neptune Analytics service | Accepted |
| [0006](0006-schema-guided-llm-extraction-guard.md) | Schema-guided LLM extraction is guarded by a closed schema + entity-grounding, runs at ingest, and is distinguishable in the graph | Accepted |
| [0007](0007-silver-cache-content-and-config-addressed.md) | Silver cache addressing — content-and-config over content-only | Superseded by ADR-0016 |
| [0008](0008-automatic-engine-routing-local-vs-global.md) | Automatic Local-vs-Global engine routing is a `mode="auto"` selector (deterministic + Bedrock twin), not a new retrieval engine | Superseded by RFC-0004 |
| [0009](0009-access-control-synthetic-labels-not-real-authz.md) | Access-control depth — synthetic visibility labels over real authorization | Accepted |
| [0010](0010-terraform-migration.md) | Migrate infrastructure from AWS CDK (Python) to Terraform (HCL) | Accepted |
| [0011](0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) | Neptune SPARQL/RDF over openCypher/LPG: named-graph primitive and Text2SPARQL read-only guard | Accepted |
| [0012](0012-owl-schema-only-and-named-graph-partition.md) | OWL schema-only + named-graph partition: typed RDF corpus with asymmetric retrieval semantics | Accepted |
| [0013](0013-multi-strategy-server-side-routing.md) | Multi-strategy server-side routing: rules-first cascade over named-graph partitions | Accepted |
| 0014 | MCP tool server as the primary interface | _(reserved — pending wave 4)_ |
| 0015 | OTEL observability | _(reserved — pending wave 5)_ |
| [0016](0016-git-ingestion-commit-sha-delta-medallion.md) | Git ingestion: commit-SHA delta + medallion over CodePipeline/S3-mirror bronze source | Accepted |

## Adding a new ADR

Copy the lean MADR-aligned shape from an existing ADR (title names *problem +
chosen solution*; sections: Context, Decision, Decision drivers, Consequences,
Confirmation, Alternatives considered, References). Use the next zero-padded
ordinal and a kebab-case title.
