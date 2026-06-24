---
title: broken
this is: [not valid: yaml: at all
---

# Body

A doc whose front matter is malformed YAML — the parser must skip the front
matter with a warning, not crash the ingest run.
