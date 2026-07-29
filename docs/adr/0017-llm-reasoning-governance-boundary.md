# ADR-0017: LLM reasoning governance boundary for synthesizer and strategy router

- **Status:** Proposed
- **Date:** 2026-07-29
- **Decision-makers:** eugenelim
- **Consulted:** — <!-- governance team / regulated customer representative needed to resolve open question -->
- **Related:** ADR-0013 (multi-strategy routing), ADR-0014 (MCP tool server)

## Context

Two components make direct boto3 Bedrock calls today:

- **`BedrockClaudeSynthesizer`** (`packages/graphrag/src/graphrag/synthesize.py`) — RAG synthesis via the Converse API, `claude-sonnet-4-6`, up to 2 000 output tokens; receives multi-chunk retrieved context + graph facts per call.
- **`BedrockQueryRouter`** (`packages/graphrag/src/graphrag/routing/_bedrock_router.py`) — strategy classification via `invoke_model`, `amazon.nova-lite-v1:0`, 64 max tokens, called only when the deterministic `RuleQueryRouter` returns `AMBIGUOUS`.

Neither call is yet wired into the MCP server (`mcp/_tools.py:124` holds a placeholder; `_ProductionStore.bedrock_client` is constructed but unconnected). This is a greenfield wiring decision.

Enterprise customers in regulated verticals (financial services, healthcare, government) increasingly require that LLM reasoning runs as a named, versioned, IAM-governed service with policy enforced *outside* the agent code — not inline in a protocol adapter. The specific concern is that when reasoning runs directly inside an MCP server process, governance of the reasoning step is entangled with governance of the MCP protocol layer.

**"Knowledge agent" is not a formal AWS service type.** The term appears colloquially in AWS blog posts to mean an AgentCore Runtime container that uses Bedrock Knowledge Bases as governed tools via AgentCore Gateway. There is no product SKU or deployment pattern called a "knowledge agent" in the AgentCore FAQs or documentation. Any AgentCore Runtime path is a container-based microservice, not a named AWS product category.

**AgentCore identity attribution requires explicit configuration.** By default, CloudTrail records the shared `agentcore-bot` service account, not the calling user or agent identity, unless `GetWorkloadAccessTokenForJWT` identity propagation is explicitly configured. Without this, an AgentCore Runtime path produces *worse* audit attribution than a correctly instrumented direct boto3 call. This is a design-time pre-requisite, not a deployment detail.

**No compliance framework mandates the architectural separation.** SOC2, ISO 27001, NIST AI RMF, and FedRAMP require evidence artifacts — audit logs, policy enforcement records, data residency controls. Both direct Bedrock and AgentCore Runtime can produce compliant evidence; AgentCore reduces the evidence-assembly burden but does not change the requirement.

## Decision

**Open — not yet resolved.** The decision turns on a single question that requires input from the governance team or regulated customer representative:

> Does the target deployment context require **execution isolation** (dedicated microVM per session, no cross-session contamination, policy enforced outside agent code), or does it require **audit evidence** (model invocation logs, Guardrails, versioned prompts, IAM-scoped model access)?

If execution isolation is required → Shape B or C (AgentCore Runtime for at least the router).
If audit evidence suffices → Shape A (inline Bedrock with governance controls) with no new service boundary.

The three candidate shapes evaluated are documented in Alternatives Considered below. Shape B is the natural split: the router (policy-sensitive classification step, 64-token payload, no retrieved context to serialize) gets the hard execution boundary; the synthesizer (latency-sensitive, large retrieved context per call) retains inline Bedrock with full governance controls.

Until the open question is resolved, no synthesis wiring should proceed in `mcp/_tools.py` — the wiring commits to one of these shapes.

## Decision drivers

1. **Governance auditability** — which shape produces sufficient evidence artifacts for NIST AI RMF, SOC2 Type II, and customer-specific governance requirements, with the least implementation risk.
2. **RAG query latency (p99)** — synthesis is the dominant latency term in the pipeline (500 ms – 5+ s LLM generation); any service boundary adds a fixed network round-trip before that step begins, compounding with the existing retrieval latency.
3. **Policy enforcement independence** — whether governance policy must be enforced outside the agent code (requiring AgentCore Gateway/Runtime) or whether developer-enforced per-call Guardrail identifiers are acceptable.
4. **Deployment independence of reasoning from serving** — the ability to update models, prompts, or guardrail versions without redeploying the MCP server.

## Consequences

Consequences are shape-dependent. The shared negatives across all shapes:

**All shapes:**
- AgentCore identity attribution (`GetWorkloadAccessTokenForJWT`) must be explicitly configured in any AgentCore path before the governance claim is valid — this is not automatic.
- The Guardrail uniformity gap (documented AWS limitation: orchestrating APIs do not carry the same `guardrailIdentifier` on every internal `InvokeModel` call) applies to all paths and is unresolved regardless of shape.

**Shape A (inline governance only):**
- Positive: no latency overhead, no new service to operate, governance evidence is sufficient for most compliance frameworks.
- Negative: policy enforcement depends on developer discipline (passing `guardrailIdentifier` on every Converse call); no execution isolation between sessions.

**Shape B (router to AgentCore, synthesizer inline):**
- Positive: the strategy classification step — the most governance-sensitive decision point — gets microVM isolation and versioned, IAM-governed endpoints; synthesizer latency is unchanged.
- Negative: two deployment surfaces to operate; router context serialization overhead is negligible (64-token output, no retrieved context), but the AgentCore Runtime ARM64 build and idle-timeout (15 min default) must be managed.

**Shape C (both to AgentCore Runtime):**
- Positive: hardest governance boundary; purest separation of reasoning from serving.
- Negative: retrieved context (potentially many chunks) must be serialized across a service boundary before every synthesis call, adding 100–500 ms fixed overhead per query on top of the dominant LLM step; highest ops complexity.

**Revisit if:** a regulated customer deployment requires FedRAMP High or equivalent, which mandates execution isolation rather than just audit evidence; or if AgentCore identity attribution is confirmed broken in our specific configuration after `GetWorkloadAccessTokenForJWT` setup; or if Bedrock Model Invocation Logging proves insufficient for a specific compliance audit.

## Confirmation

- **Mode:** reviewer-checked
- **Signal:** MCP synthesis wiring PR reviewed against the chosen shape before merge; Guardrails version is a numbered production version (never `DRAFT`) on all Converse calls.
- **Owner:** eugenelim at synthesis wiring PR review

## Alternatives considered

**Shape A — Inline governance (no new service boundary)**
Both `BedrockClaudeSynthesizer` and `BedrockQueryRouter` remain in the `graphrag` package. Governance controls added: Bedrock Guardrails (numbered production version) on every Converse call, Model Invocation Logging to S3, `bedrock:GuardrailIdentifier` IAM deny condition to enforce per-call guardrail attachment, Bedrock Prompt Management for versioned system prompts. The `BedrockQueryRouter` should be migrated from `invoke_model` (legacy API) to the Converse API as a prerequisite regardless of shape.

Rejected for execution-isolation-required contexts: policy enforcement depends on developer discipline; no microVM-per-session isolation.

**Shape B — Split boundary: router to AgentCore Runtime, synthesizer inline with governance**
`BedrockQueryRouter` runs as an AgentCore Runtime agent (ARM64 container, versioned endpoint, IAM-governed invocation). `BedrockClaudeSynthesizer` stays inline with Shape A governance controls. The router's classification payload is small (question text only, no retrieved context), so serialization overhead is negligible. This is the minimum viable execution boundary for organizations that require it.

Not yet rejected — this is the leading candidate if execution isolation is required.

**Shape C — Full separation: both calls to AgentCore Runtime**
Both synthesizer and router run as AgentCore Runtime agents. The retrieval layer serializes retrieved chunks + graph facts across the network boundary before every synthesis call. Governance is maximally separated from the serving layer.

Rejected as default: the synthesizer receives large retrieved context per call; the cross-service serialization overhead (100–500 ms) is additive on the dominant latency term and compounds with existing retrieval overhead. Reserved for deployments where execution isolation for synthesis is explicitly mandated.

**Direct Bedrock with no governance controls (status quo)**
Continue with the current pattern and wire synthesis without adding Guardrails, Model Invocation Logging, or IAM conditions.

Rejected: governance evidence is required in any production deployment. The current state (synthesis unwired) is not a valid long-term position.

## References

- Desk-research synthesis (2026-07-29): governance gaps in MCP-hosted LLM calls (Cloud Security Alliance, Zscaler NIST AI RMF mapping, Maxim AI MCP audit logging); AgentCore Runtime capabilities vs. direct Bedrock (Teleport identity attribution analysis); RAG pipeline latency benchmarks (Echelon Edge, ACM ISCA 2025 RAGO)
- [AgentCore identity attribution gap — Teleport (2025)](https://goteleport.com/blog/ai-agents-aws-agentcore/)
- [AgentCore Guardrails GA (June 2026) — AWS What's New](https://aws.amazon.com/about-aws/whats-new/2026/06/amazon-bedrock-agentcore-policy-guardrails-generally-available/)
- [Amazon Bedrock AgentCore Runtime dev guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html)
- Stage-0 concept: "LLM Reasoning Governance Boundary for GraphRAG" (same session, 2026-07-29)
