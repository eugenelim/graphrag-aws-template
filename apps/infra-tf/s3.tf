# s3.tf — the corpus snapshot bucket: private, encrypted, TLS-enforced, teardown-removable.
#
# Translated from apps/infra/stacks/graphrag_stack.py `_corpus_bucket()` (:475):
#   block_public_access=BLOCK_ALL   -> aws_s3_bucket_public_access_block (all 4 = true)
#   encryption=S3_MANAGED           -> aws_s3_bucket_server_side_encryption_configuration (AES256)
#   enforce_ssl=True                -> aws_s3_bucket_policy (Deny on aws:SecureTransport=false)
#   auto_delete_objects=True        -> force_destroy = true (destroy leaves nothing billable)
#   removal_policy=DESTROY          -> no prevent_destroy (teardown-first, ADR-0002)

resource "aws_s3_bucket" "corpus" {
  bucket_prefix = "graphrag-corpus-"
  force_destroy = true # empties + removes the bucket on `terraform destroy`
}

resource "aws_s3_bucket_public_access_block" "corpus" {
  bucket                  = aws_s3_bucket.corpus.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "corpus" {
  bucket = aws_s3_bucket.corpus.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# TLS-deny bucket policy (matches CDK enforce_ssl=True): deny every S3 action on the
# bucket + its objects when the request is not over TLS. This is a Deny statement, so
# the Principal:"*" is required and legitimate (it denies, never grants — the AC8
# no-wildcard scan must scope to data-plane Allow statements, not this policy).
resource "aws_s3_bucket_policy" "corpus_tls" {
  bucket = aws_s3_bucket.corpus.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.corpus.arn,
        "${aws_s3_bucket.corpus.arn}/*",
      ]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })

  # The public-access block must exist first: a bucket policy applied before the block
  # is configured can be transiently rejected on live apply.
  depends_on = [aws_s3_bucket_public_access_block.corpus]
}
