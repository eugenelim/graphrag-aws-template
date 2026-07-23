provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Environment = var.environment
      Project     = var.project
      Department  = var.department
      Application = var.application
      User        = var.user
    }
  }
}
