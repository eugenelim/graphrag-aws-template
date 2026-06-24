#!/usr/bin/env python3
"""CDK app entrypoint — `cdk deploy` / `cdk destroy` (ADR-0003).

Deploy then push the ingestion image and run the task once; one `cdk destroy`
removes every billable resource (charter principle 4). See apps/infra/README.md.
"""

from __future__ import annotations

import aws_cdk as cdk
from stacks.graphrag_stack import GraphragStack

app = cdk.App()
GraphragStack(app, "GraphragSlice1")
app.synth()
