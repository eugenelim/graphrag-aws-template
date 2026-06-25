# Incremental delta re-ingest — presenter script

The enterprise concern most RAG demos dodge: **the corpus changes**. This script walks a
presenter through the before/after delta demo — show that re-ingesting only the delta keeps
**both** stores (graph + vector) consistent, removes orphans, and never re-embeds unchanged
documents. Slice 5 of the demo ([`incremental-delta-reingest`](../../specs/incremental-delta-reingest/spec.md)).

- **The stable key is `doc path + content hash`.** A manifest (`doc id → hash`) records what is
  ingested; a delta diffs the new snapshot's manifest against it and classifies every change as
  **add / change / delete / move** (a move = same hash, new path). This is content-hash based, so
  it runs inside the no-NAT, S3-snapshot topology — no live `git clone` in the VPC (ADR-0002).
- **Orphan removal is provenance-set reference counting.** Each graph node/edge carries the set of
  documents that contribute it; a node survives a delta *iff* at least one surviving document still
  contributes it. The teaching beat: a SIG node *survives* deletion of its README because a KEP
  still references it — while a node whose last document is gone is removed (and the cascade can
  remove a person who only authored the deleted KEP).
- **Offline by default:** the demo runs against the bundled fixture corpus with in-memory stores +
  the offline non-semantic embedder — reproducible and credential-free. The same `MODE=delta`
  Fargate task runs it live against Neptune + OpenSearch.

## Drive it from real git history

`scripts/delta-demo.sh` takes a corpus git repo and two refs, checks each out into a throwaway
worktree, and runs the demo over the two real-commit snapshots:

```bash
scripts/delta-demo.sh <corpus-repo> <base-ref> <new-ref>
```

A worked example over the fixture corpus (add a KEP, delete a KEP, move a KEP):

```bash
CORPUS=$(mktemp -d) && cp -r packages/graphrag/tests/fixtures/corpus/* "$CORPUS"/
git -C "$CORPUS" init -q -b main && git -C "$CORPUS" add -A && git -C "$CORPUS" commit -qm base
BASE=$(git -C "$CORPUS" rev-parse HEAD)
# ... add enhancements/keps/sig-node/4242-brand-new/, rm the 1880 KEP dir,
#     git mv the 1287 KEP dir to a new name ...
git -C "$CORPUS" add -A && git -C "$CORPUS" commit -qm delta
scripts/delta-demo.sh "$CORPUS" "$BASE" HEAD
```

## What to point at in the trace

```
== delta demo ==
(synthetic teaching demo — ... BOTH stores updated from one pass, kept consistent by stable key ...)
BEFORE (base snapshot): nodes=22 edges=28 chunks=13
== delta re-ingest ==
(incremental)
added: 2  changed: 0  deleted: 2  moved: 2
  + enhancements/keps/sig-node/4242-brand-new/kep.yaml
  - enhancements/keps/sig-network/1880-multiple-service-cidrs/kep.yaml
  > .../1287-in-place-update-pod-resources/kep.yaml -> .../1287-in-place-pod-resize/kep.yaml
orphans removed: 2
nodes: 22 -> 21   edges: 28 -> 25   chunks: 13 -> 12
re-embedded chunks (delta only): 3
```

1. **The classified delta** — `add / change / delete / move`, each named. The move is detected as a
   same-hash-at-a-new-path, *not* a delete+add (charter principle 1 — no black-box hop).
2. **`orphans removed`** — the deleted KEP's node and the edges/person that *only* it contributed
   are gone; nothing a surviving document still references is touched.
3. **`re-embedded chunks (delta only)`** — only the added/changed/moved documents are re-embedded;
   the unchanged corpus is never sent to the embedder (the freshness cost saver).
4. **Both stores moved together** — the node/edge counts (graph) and the chunk count (vector)
   change in one pass, so the two stores never diverge.

The escape hatch: `graphrag rebuild --community … --enhancements …` clears both stores and
re-ingests from scratch — the ground-truth reset whenever you want to be sure.
