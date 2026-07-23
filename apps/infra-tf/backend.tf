terraform {
  backend "s3" {
    # Values supplied via -backend-config=backend.hcl at terraform init time.
    # Never hardcoded here. See backend.hcl.example for the required keys.
    encrypt      = true
    use_lockfile = true # native S3 state locking (Terraform >= 1.11, ADR-0010); explicit opt-in required
  }
}
