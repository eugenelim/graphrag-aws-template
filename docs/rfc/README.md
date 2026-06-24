# Requests For Comments

> Forward-looking governance — *should we change this?* See
> [`../CONVENTIONS.md` § 3](../CONVENTIONS.md) for what goes here and what
> doesn't. RFCs are in-flight while open and freeze to history on
> acceptance/rejection; they never get edited in place after that.

Lifecycle: `Draft → Open → Final Comment Period → Accepted | Rejected | Withdrawn`
(an experiment-bearing RFC may sit in `Experimental` while a trial runs).

| #    | Title                                          | Status |
| ---- | ---------------------------------------------- | ------ |
| [0001](0001-adopt-project-charter.md) | Adopt the project charter (mission, scope, principles, architecture patterns) | Draft |

## Adding a new RFC

Use the next zero-padded ordinal and a kebab-case title
(`NNNN-kebab-case-title.md`). In Claude Code, run `/new-rfc "<title>"`; the
template lives at `assets/rfc.md` in the `new-rfc` skill. An RFC may carry a
sibling `NNNN-notes/` folder for promoted research and supporting material.
