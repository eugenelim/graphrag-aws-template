# dynamodb.tf — the ingestion status registry table (infra-tf-p0-gap-remediation AC4).
#
# design.md § "Ingestion status registry and failure alerting": one on-demand
# table, single string PK. Run items (`run#<pipeline_execution_id>`) and document
# items (`doc#<doc_uri>`) share it. This slice provisions the table only — the
# entrypoint writes are the AC8 deferral (workspace.toml backlog:
# ingestion-status-registry-app-wiring).
#
# Fixed name (matches the fixed OpenSearch domain / ECS cluster convention) so the
# task env var and any operator CLI lookups are predictable.
#
# Reversibility: reversible — the registry holds derivable operational state
# (re-ingest regenerates it); no deletion protection, teardown-first (ADR-0002).
#
# Teardown residual: the FIXED name means a table left behind by a stalled
# destroy blocks the next apply with ResourceInUseException — sweep it manually
# (`aws dynamodb delete-table --table-name graphrag-ingestion-status`) before
# re-applying, the same class as the fixed OpenSearch domain name.

resource "aws_dynamodb_table" "ingestion_status" {
  name         = "graphrag-ingestion-status"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  deletion_protection_enabled = false
}
