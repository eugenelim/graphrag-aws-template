# Fixture corpus — provenance

This is a small, **deterministic** slice of two real public repos, used so the
parse → extract → resolve → query → eval pipeline runs offline. The metadata
(SIG slugs, leadership GitHub handles, KEP numbers, `owning-sig`, authors,
approvers) is **verbatim from the sources** so the resolver eval (AC5) is empirical
against real data, not invented — that is the de-risk verdict's "open confirmation".

| Source | Repo | Files (trimmed) | Fetched |
| --- | --- | --- | --- |
| `community/` | [`kubernetes/community`](https://github.com/kubernetes/community) (`master`) | `sigs.yaml` (sig-network + sig-node), `sig-network/README.md`, `sig-node/README.md` | 2026-06-23 |
| `enhancements/` | [`kubernetes/enhancements`](https://github.com/kubernetes/enhancements) (`master`) | `keps/sig-network/2086-…`, `keps/sig-network/1880-…`, `keps/sig-node/1287-…` (each `kep.yaml` + `README.md`), plus a synthetic legacy prose-only KEP `keps/sig-node/0009-…` | 2026-06-23 |

## What each file is chosen to demonstrate

- **Cross-source overlap (the punchline):** `@thockin`, `@aojea`, `@dchen1107`,
  `@derekwaynecarr` appear in *both* `sigs.yaml` leadership and `kep.yaml`
  author/approver lists → single Person nodes after resolution.
- **Handle normalization:** KEP-2086 lists `thockin` (bare) as a reviewer and
  `@thockin` as an approver; `sigs.yaml` has `SergeyKanzhelev` (mixed case).
- **Alias table (prose ↔ handle):** the legacy KEP-0009 names its author in prose
  as `Tim Hockin`, which `aliases.yaml` maps to `thockin`.
- **Correct query scoping:** `@thockin` tech-leads `sig-network` (owns KEP-2086,
  KEP-1880) but only *approves* the sig-node KEP-1287 — so the entity-led query
  "KEPs owned by the SIG @thockin tech-leads" must return {2086, 1880} and **not**
  1287.

To extend the empirical confirmation to the whole corpus, see the deferred
`graph-ingestion-resolution-full-corpus-eval` backlog item.
