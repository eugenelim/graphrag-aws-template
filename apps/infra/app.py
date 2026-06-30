#!/usr/bin/env python3
"""CDK app entrypoint -- `cdk deploy` / `cdk destroy` (ADR-0003).

Deploy then push the ingestion image and run the task once; one `cdk destroy`
removes every billable resource (charter principle 4). See apps/infra/README.md.

The stack applies the org's five governance tags to all taggable resources; see
`GraphragStack`. Override any via `cdk deploy -c <key>=<value>` (the deploy script
fills `user` from the caller identity).
"""

from __future__ import annotations

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks
from stacks.graphrag_stack import GraphragStack, add_nag_suppressions

app = cdk.App()
stack = GraphragStack(app, "GraphragSlice1")
# cdk-nag HARD gate (security-hardening-followups AC4): an unsuppressed AwsSolutions finding
# fails `cdk synth` / CI. The accepted residuals carry reason-signed suppressions; the bespoke
# IAM/topology assertions in apps/infra/tests/test_stack.py are the independent guard.
add_nag_suppressions(stack)
cdk.Aspects.of(stack).add(AwsSolutionsChecks())
app.synth()
