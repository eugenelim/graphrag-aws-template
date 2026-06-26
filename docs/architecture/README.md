# Architecture

How the code is *currently* organized. Not why (that's in
[`../adr/`](../adr/)) and not what we want (that's in
[`../rfc/`](../rfc/)) — **what is**.

- [`overview.md`](overview.md) — the map of the monorepo. What's in
  `apps/`, `packages/`, `tools/`, `packs/`, and how they relate.
  Read this first.
- `<subsystem>.md` — one file per non-trivial subsystem (add as the repo
  grows). Each describes the structure, the entry points, and links to
  the ADRs that explain why.
- [`deployment-and-verification.md`](deployment-and-verification.md) — how each slice
  is verified live (smoke probes, dual-write, per-slice live-run records).
- [`deployment-timing.md`](deployment-timing.md) — indicative wall-clock timing for
  the deploy → verify → teardown cycle (living; refine M-values as runs measure them).
- [`develop-and-test-offline.md`](develop-and-test-offline.md) — how to build/exercise
  the stack with no AWS, and the text2cypher offline-execution decision (no local Neptune).

Architecture docs are the *rolled-up snapshot* — the answer to "what
does this codebase look like today" without replaying ADR history.
Lifecycle: living. Update whenever the layout or major dependencies
change.

Note for contributors: the bundle's source-of-truth split (skills,
agents, hooks, commands, hook-wiring, and pack seeds all live under
`packs/<pack>/`) is described in
[`../CONVENTIONS.md` § Pack source-of-truth split](../CONVENTIONS.md#pack-source-of-truth-split).
Anything in this directory documents the *projected* layout adopters
end up with; the pack-side authoring rules are in CONVENTIONS.
