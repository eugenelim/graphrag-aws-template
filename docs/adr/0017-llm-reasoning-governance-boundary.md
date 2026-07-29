# ADR-0017: LLM reasoning governance boundary for synthesizer and strategy router

- **Status:** Proposed
- **Date:** 2026-07-29
- **Decision-makers:** eugenelim
- **Consulted:** — <!-- governance team / regulated customer representative needed to resolve open question -->
- **Related:** ADR-0013 (multi-strategy routing), ADR-0014 (MCP tool server)

## Context

Two components make direct boto3 Bedrock calls today:

- **`BedrockClaudeSynthesizer`** (`packages/graphrag/src/graphrag/synthesize.py`) — RAG synthesis via the Converse API, `us.anthropic.claude-sonnet-4-6` (cross-region US inference profile), up to 2 000 output tokens; receives multi-chunk retrieved context + graph facts per call.
- **`BedrockQueryRouter`** (`packages/graphrag/src/graphrag/routing/_bedrock_router.py`) — strategy classification via `invoke_model`, `amazon.nova-lite-v1:0`, 64 max tokens, called only when the deterministic `RuleQueryRouter` returns `AMBIGUOUS`.

Neither call is yet wired into the MCP server (`mcp/_tools.py:124` holds a placeholder; `_ProductionStore.bedrock_client` is constructed but unconnected). This is a greenfield wiring decision.

Enterprise customers in regulated verticals (financial services, healthcare, government) increasingly require that LLM reasoning runs as a named, versioned, IAM-governed service with policy enforced *outside* the agent code — not inline in a protocol adapter. The specific concern is that when reasoning runs directly inside an MCP server process, governance of the reasoning step is entangled with governance of the MCP protocol layer.

**"Knowledge agent" is not a formal AWS service type.** The term appears colloquially in AWS blog posts to mean an AgentCore Runtime container that uses Bedrock Knowledge Bases as governed tools via AgentCore Gateway. There is no product SKU or deployment pattern called a "knowledge agent" in the AgentCore FAQs or documentation. Any AgentCore Runtime path is a container-based microservice, not a named AWS product category.

**AgentCore identity attribution requires explicit configuration.** By default, CloudTrail records the shared `agentcore-bot` service account, not the calling user or agent identity, unless `GetWorkloadAccessTokenForJWT` identity propagation is explicitly configured. Without this, an AgentCore Runtime path produces *worse* audit attribution than a correctly instrumented direct boto3 call. This is a design-time pre-requisite, not a deployment detail.

**No compliance framework mandates the architectural separation.** SOC2, ISO 27001, NIST AI RMF, and FedRAMP require evidence artifacts — audit logs, policy enforcement records, data residency controls. Both direct Bedrock and AgentCore Runtime can produce compliant evidence; AgentCore reduces the evidence-assembly burden but does not change the requirement.

## Decision

**Open — not yet resolved.** The decision turns on three separable governance properties — previously collapsed into a false binary ("execution isolation OR audit evidence"). Shape A's IAM deny condition on `bedrock:GuardrailIdentifier` already enforces policy outside agent code; the only property exclusive to Shape C is microVM per-session isolation:

| Property | Shape A | Shape C |
|---|---|---|
| Audit evidence (model invocation logs, Guardrails, versioned prompts) | ✓ via MIL + Guardrails | ✓ via AgentCore Observability |
| Policy enforced outside agent code | ✓ via IAM `bedrock:GuardrailIdentifier` deny condition | ✓ via AgentCore Gateway policy + AgentCore Guardrails GA (June 2026) |
| MicroVM per-session execution isolation (no cross-session contamination) | ✗ | ✓ |

The decision therefore reduces to: **does the target deployment require microVM per-session execution isolation?**

If yes → Shape C (both calls to AgentCore Runtime).
If no → Shape A (inline Bedrock with governance controls).

The governance-relevant call is the **synthesizer**, not the router. The synthesizer processes retrieved chunks and graph facts (the sensitive data surface), produces user-facing output (the data egress surface), and is the primary prompt-injection target when retrieved content is tainted. The router maps a question string to a strategy name — no sensitive data is processed, no user-facing output is produced, and a misbehaving router falls back to `hybrid_graph` by design (`routing/_bedrock_router.py:165`). Governing only the router while leaving the synthesizer inline does not add meaningful governance; it governs the trivial call and leaves the critical one untouched. The real choice is therefore Shape A or Shape C.

The **call-site seam** in `mcp/_tools.py` (inline Bedrock vs AgentCore Runtime endpoint) must not be wired until this is resolved — that wiring commits to a shape. Shape-independent substrate work is common to both shapes and may proceed now: Guardrails configuration (numbered production version, not DRAFT), Model Invocation Logging bucket + IAM policy, `bedrock:GuardrailIdentifier` IAM deny condition, Bedrock Prompt Management for versioned system prompts. See OQ-2 for resolution owner and default posture.

## Decision drivers

1. **Governance auditability** — which shape produces sufficient evidence artifacts for NIST AI RMF, SOC2 Type II, and customer-specific governance requirements, with the least implementation risk.
2. **RAG query latency (p99)** — synthesis is the dominant latency term in the pipeline (500 ms – 5+ s LLM generation); any service boundary adds a fixed network round-trip before that step begins, compounding with the existing retrieval latency.
3. **Policy enforcement independence** — whether governance policy must be enforced outside the agent code (requiring AgentCore Gateway/Runtime) or whether developer-enforced per-call Guardrail identifiers are acceptable.
4. **Deployment independence of reasoning from serving** — the ability to update models, prompts, or guardrail versions without redeploying the MCP server.

## Consequences

Consequences are shape-dependent. The shared negatives across all shapes:

**All shapes:**
- AgentCore identity attribution (`GetWorkloadAccessTokenForJWT`) must be explicitly configured in any AgentCore path before the governance claim is valid — this is not automatic.
- The Guardrail uniformity gap (documented AWS limitation: orchestrating APIs do not carry the same `guardrailIdentifier` on every internal `InvokeModel` call) applies to AgentCore-orchestrated paths. A direct Shape A single Converse call carries `guardrailIdentifier` explicitly on every call and is not subject to this gap.
- **Model Invocation Logging (MIL) creates a sensitive-data surface in both shapes.** MIL captures full prompts — retrieved chunks and graph facts, the same data the ADR governs. The MIL S3 bucket requires SSE-KMS, a least-privilege bucket policy scoped to the audit reader role, and a retention/expiry lifecycle aligned to the applicable compliance window. The MIL bucket is in-scope for any data residency mandate that applies to the inference calls (see OQ-1).

**Shape A (inline governance only):**
- Positive: no latency overhead, no new service to operate, governance evidence is sufficient for most compliance frameworks; IAM deny condition enforces guardrail presence at the AWS API boundary independent of developer discipline.
- Negative: IAM enforces guardrail *presence* (every Converse call must carry a `guardrailIdentifier`) but not guardrail *version* correctness — using a stale or incorrect guardrail version remains a developer responsibility; no microVM per-session isolation.

**Shape B (router to AgentCore, synthesizer inline) — not a governance option:**
- Governs the wrong call. The synthesizer is where sensitive data is processed and user-facing output is produced; the router handles only question-to-strategy classification with no sensitive payload.
- Only valid driver for Shape B is operational: independent deployment lifecycle for the router (update strategy/model without a graphrag package release). That is a release-engineering argument, not a governance argument, and does not satisfy an execution-isolation requirement.
- Not considered further as a governance shape.

**Shape C (both to AgentCore Runtime):**
- Positive: microVM per-session isolation (no cross-session contamination, architecturally enforced); purest separation of reasoning from serving; versioned endpoints enable model/prompt rollback independent of MCP server releases; AgentCore Policy Guardrails GA (June 2026) enforces Guardrails at the Gateway layer outside agent code, covering tool inputs and model outputs consistently.
- Negative: network round-trip + AgentCore session cold-start (potentially 1–5 s on first request to a new session) overhead added to the dominant LLM step; an additional availability dependency on the synthesis critical path; retrieved chunks and graph facts cross a service boundary (new sensitive-data-in-transit surface requiring TLS + IAM auth on the AgentCore invocation); highest ops complexity.

**Revisit if:** a regulated customer deployment confirms microVM execution isolation is required (resolves the open decision toward Shape C); or if AgentCore identity attribution is confirmed non-functional after `GetWorkloadAccessTokenForJWT` configuration; or if Bedrock Model Invocation Logging proves insufficient for a specific compliance audit; or if the p99 synthesis latency budget (see OQ-3) rules out Shape C's cold-start overhead.

## Confirmation

- **Mode:** reviewer-checked
- **Signal:** MCP synthesis wiring PR reviewed against the chosen shape before merge; Guardrails version is a numbered production version (never `DRAFT`) on all Converse calls.
- **Owner:** eugenelim at synthesis wiring PR review

## Alternatives considered

**Shape A — Inline governance (no new service boundary)**
Both `BedrockClaudeSynthesizer` and `BedrockQueryRouter` remain in the `graphrag` package. Governance controls added: Bedrock Guardrails (numbered production version) on every Converse call, Model Invocation Logging to S3, `bedrock:GuardrailIdentifier` IAM deny condition to enforce per-call guardrail attachment, Bedrock Prompt Management for versioned system prompts. The `BedrockQueryRouter` should be migrated from `invoke_model` (legacy API) to the Converse API as a prerequisite regardless of shape.

Rejected for execution-isolation-required contexts: policy enforcement depends on developer discipline; no microVM-per-session isolation.

**Shape B — Split boundary: router to AgentCore Runtime, synthesizer inline**
`BedrockQueryRouter` runs as an AgentCore Runtime agent; `BedrockClaudeSynthesizer` stays inline with Shape A governance controls.

Rejected as a governance option: governing only the router provides no meaningful execution isolation for the data that actually matters — retrieved chunks, graph facts, and user-facing output all remain in the inline synthesizer path. Shape B only makes sense as a release-engineering decision (independent deployment of the router), not a governance one. If execution isolation is genuinely required, Shape C is the minimum viable boundary.

**Shape C — Full separation: both calls to AgentCore Runtime**
Both synthesizer and router run as AgentCore Runtime agents. Retrieved chunks + graph facts cross a service boundary before every synthesis call; AgentCore Guardrails GA (June 2026) enforces policy at the Gateway layer outside agent code. Governance is maximally separated from the serving layer.

Rejected as default: network + AgentCore session cold-start overhead (potentially 1–5 s on first request) is additive on the dominant LLM step; adds an availability dependency on the synthesis critical path; retrieved context in-transit is a new sensitive-data surface. Reserved for deployments where microVM per-session isolation is explicitly mandated.

**Direct Bedrock with no governance controls (status quo)**
Continue with the current pattern and wire synthesis without adding Guardrails, Model Invocation Logging, or IAM conditions.

Rejected: governance evidence is required in any production deployment. The current state (synthesis unwired) is not a valid long-term position.

## Open questions

Design review (2026-07-29) identified the following judgment calls that must be resolved to close this ADR. Mechanical issues have been corrected above; these require human input.

**OQ-1 — Data residency (resolved).** Deployment is US-only. The `us.anthropic.claude-sonnet-4-6` cross-region US inference profile is acceptable; no single-region residency mandate applies. The MIL S3 bucket should be deployed in a US region consistent with the workload region. No further action required on this item.

**OQ-2 — Resolution owner, deadline, and default posture (major).** The named decision-maker is `eugenelim`, but resolution requires the governance team or a regulated customer representative (Consulted field, currently unnamed). No deadline or default posture is recorded. *Question: who owns the governance consultation, by when, and what is the default posture if no consultation materialises by that date — proceed with Shape A substrate as the reversible baseline, or block entirely?* *Needs input from: eugenelim + governance team.*

**OQ-3 — p99 synthesis latency budget (minor).** RAG query latency (p99) is a named decision driver, but no budget is stated. Shape C adds network + session cold-start overhead (potentially 1–5 s on first request); Shape A adds zero. *Question: what is the p99 synthesis-path latency budget the product/UX team has committed to?* Without a number, the Shape A vs Shape C latency comparison cannot be adjudicated. *Needs input from: product / UX stakeholder.*

## References

- Desk-research synthesis (2026-07-29): governance gaps in MCP-hosted LLM calls (Cloud Security Alliance, Zscaler NIST AI RMF mapping, Maxim AI MCP audit logging); AgentCore Runtime capabilities vs. direct Bedrock (Teleport identity attribution analysis); RAG pipeline latency benchmarks (Echelon Edge, ACM ISCA 2025 RAGO)
- [AgentCore identity attribution gap — Teleport (2025)](https://goteleport.com/blog/ai-agents-aws-agentcore/)
- [AgentCore Guardrails GA (June 2026) — AWS What's New](https://aws.amazon.com/about-aws/whats-new/2026/06/amazon-bedrock-agentcore-policy-guardrails-generally-available/)
- [Amazon Bedrock AgentCore Runtime dev guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html)
- Stage-0 concept: "LLM Reasoning Governance Boundary for GraphRAG" (same session, 2026-07-29)
