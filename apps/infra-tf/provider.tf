provider "aws" {
  region = var.aws_region

  default_tags {
    # The asset-inventory attribution tag is merged in with an operator-supplied KEY as
    # well as value, so no deployer-specific tagging convention is baked into this
    # template. Unset (the default) emits nothing rather than an empty-valued tag, which
    # some inventory tools score as worse than absent. See variables.tf.
    tags = merge(
      {
        Environment = var.environment
        Project     = var.project
        Department  = var.department
        Application = var.application
        User        = var.user
      },
      var.asset_inventory_tag_key == "" ? {} : {
        (var.asset_inventory_tag_key) = var.asset_inventory_tag_value
      },
    )
  }
}
