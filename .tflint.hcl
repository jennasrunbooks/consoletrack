# tflint for every Terraform root: `make tflint`. Only the bundled terraform ruleset, so it
# runs offline with no `tflint --init` plugin download.
config {
  call_module_type = "local"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

rule "terraform_naming_convention" {
  enabled = true
}

rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_documented_outputs" {
  enabled = true
}
