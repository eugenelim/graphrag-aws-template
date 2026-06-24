# graphrag-aws-template

A clone-and-deploy AWS **reference template** that shows when graph-augmented
retrieval beats plain vector search for grounding an LLM on organizational
knowledge, over the public Kubernetes `community` + `enhancements` corpus. See
[`docs/CHARTER.md`](docs/CHARTER.md) for the mission, scope, and principles.

> **Status:** slice 1 (`graph-ingestion-resolution`) has landed — the **graph
> half**: parse → extract → cross-source entity resolution → multi-hop graph query.
> Vector (slice 2), hybrid (slice 3), and the two enterprise-concern slices follow.
> See the [brief](docs/product/briefs/graphrag-aws-demo.md) Spec map.

## Quickstart (local, no AWS)

```bash
pip install -e ".[dev]"

C=packages/graphrag/tests/fixtures/corpus

# Parse both sources, resolve shared entities, and narrate the merges:
graphrag ingest --community $C/community --enhancements $C/enhancements

# Multi-hop graph query with a visible trace — "KEPs owned by the SIG @thockin tech-leads":
graphrag graph-query --community $C/community --enhancements $C/enhancements \
    --start @thockin --steps "TECH_LEADS>,OWNS>"

# The de-risk "open confirmation": resolver precision/recall vs a labeled sample:
graphrag resolve-eval --sample packages/graphrag/tests/fixtures/labeled_sample.yaml
```

The CLI runs against an in-memory graph offline; point it at a deployed Neptune
cluster with `--neptune-endpoint https://… --region …`.

## Deploy on AWS

The topology (no-NAT VPC, Neptune Serverless, single-node OpenSearch k-NN, the
`bedrock-runtime` endpoint, Fargate ingestion, two in-VPC smoke probes, Budgets
alarm) is an AWS CDK app. The full **deploy → verify → teardown** runbook with
steps is in [`apps/infra/README.md`](apps/infra/README.md) (deploy/destroy scripts,
the in-VPC smoke probes that verify the live graph + vector stores, and the
teardown); the rationale + verification ladder is in
[`docs/architecture/deployment-and-verification.md`](docs/architecture/deployment-and-verification.md),
and the live inventory + idle-cost view is the
[infrastructure lens](docs/architecture/infrastructure.md).

**Teardown is a feature — mind the idle cost.** Two managed stores carry **standing
cost even while idle** (neither scales to zero): **Neptune Serverless** (the
min-NCU floor is the dominant idle line) and the **single-node OpenSearch domain**
(`t3.small.search` + a small EBS volume); the `bedrock-runtime` interface endpoint
also bills hourly per AZ. There is **no NAT gateway** (egress is via VPC endpoints),
so that hourly cost is avoided. `scripts/destroy.sh` removes every billable
resource, and a `$150/mo` Budgets alarm guards the cloned-and-forgotten footgun.
Verify the exact figures against current AWS pricing (it drifts) — see the
[infrastructure lens](docs/architecture/infrastructure.md#standing-idle-cost-posture).

## Develop

```bash
pip install -e ".[dev,infra]"
ruff check packages apps && ruff format --check packages apps   # lint (incl. security S rules)
mypy packages/graphrag/src apps                                 # types
pytest                                                          # tests
python tools/hooks/pre-pr.py                                    # all gates (run before a PR)
```

Repo conventions: [`AGENTS.md`](AGENTS.md) · [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md).
Architecture: [`docs/architecture/overview.md`](docs/architecture/overview.md) ·
[`docs/architecture/security.md`](docs/architecture/security.md).
