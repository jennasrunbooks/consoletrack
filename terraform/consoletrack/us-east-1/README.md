# terraform/consoletrack/us-east-1

Regional platform in the ConsoleTrack hub account, created and destroyed freely (about 15 to 20
minutes each way): a private VPC with one NAT gateway, EKS with a Graviton node group and its
managed add-ons, Pod Identity roles, the change intake (CloudTrail → EventBridge → SQS), the
canary security group, and, with `DOMAIN`, the ACM certificate. Terraform manages AWS only;
every chart in the cluster comes from Helmfile (`helm/helmfile.yaml.gotmpl`).

Spec: `product-spec.md` → AWS design and cost, Infrastructure as code, Identity and access,
Workflow and data model → What counts as out-of-band, Intake health and canary, Custom domain.
State key: `consoletrack/us-east-1/terraform.tfstate`. Names: `ct-<component>-use1-<suffix>`.

## Resources

| File | Resource | When | Name |
|---|---|---|---|
| `vpc.tf` | VPC, 2 private + 2 public subnets in 2 AZs, internet gateway | always | `ct-vpc-use1-<suffix>` |
| `vpc.tf` | One NAT gateway + Elastic IP, routes for both private subnets | always | |
| `vpc.tf` | S3 gateway endpoint on the private route table | always | `ct-s3-endpoint-use1-<suffix>` |
| `eks.tf` | EKS cluster (API auth mode, upgrade policy STANDARD, no IRSA, no KMS key, no control-plane logs) | always | `ct-eks-use1-<suffix>` |
| `eks.tf` | Managed add-ons: `eks-pod-identity-agent` and `vpc-cni` (before nodes), `kube-proxy`, `coredns`, `aws-ebs-csi-driver` | always | |
| `eks.tf` | Managed node group: `NODE_INSTANCE_TYPE` (arm64 AL2023), Spot when `USE_SPOT`, 2 nodes (1–3), private subnets | always | `ct-nodes-use1-<suffix>` |
| `eks.tf` | Cluster and node IAM roles under `/ct/` with the boundary | always | `ct-eks-{cluster,node}-use1-<suffix>` |
| `eks.tf` | Access entries: the operator (cluster admin); CI's deploy role → Kubernetes group `consoletrack-deployers` | deploy entry with `ENABLE_GITHUB_OIDC=true` | |
| `pod-identity.tf` | Pod Identity roles + associations (below) | | `ct-<component>-use1-<suffix>` |
| `intake.tf` | CloudTrail trail: single region, write management events, global service events, log file validation | always | `ct-trail-use1-<suffix>` |
| `intake.tf` | Trail log bucket: private, SSE-S3, TLS only, logs expire after 14 days | always | `ct-trail-logs-use1-<suffix>` |
| `intake.tf` | SQS change queue + DLQ (`maxReceiveCount = 5`, SSE), queue policy: only the rule may send | always | `ct-changes-use1-<suffix>`, `ct-changes-dlq-use1-<suffix>` |
| `intake.tf` | EventBridge rule on the default bus, pattern in `patterns/changes.json` | always | `ct-changes-use1-<suffix>` |
| `canary.tf` | Empty security group the canary tags (`ignore_changes` on `ct:canary`) | always | `ct-canary-sg-use1-<suffix>` |
| `acm.tf` | ACM certificate for `DOMAIN`, DNS-validated in the global root's zone (found by name) | `DOMAIN` set | |

A first `plan` with the defaults creates **88** resources; `EXPOSURE=alb` and `DOMAIN` add 11
(the LBC and ExternalDNS roles, policies, and associations, and the certificate with its
validation record).

### Pod Identity roles

Every role is under `/ct/`, carries the `ct-boundary-<suffix>` boundary, trusts only
`pods.eks.amazonaws.com`, and is bound to one `namespace:serviceaccount`. The node role has only
`AmazonEKSWorkerNodePolicy` and ECR read-only; pods can't reach IMDS (hop limit 1).

| Role | Service account | Permissions |
|---|---|---|
| `ct-vpc-cni-use1-<suffix>` | `kube-system:aws-node` | Module's VPC CNI policy (IPv4). The add-on accepts Pod Identity, so no node-role exception |
| `ct-ebs-csi-use1-<suffix>` | `kube-system:ebs-csi-controller-sa` | Module's EBS CSI policy |
| `ct-lbc-use1-<suffix>` (`EXPOSURE=alb`) | `kube-system:aws-load-balancer-controller` | Module's Load Balancer Controller policy (tag-scoped) |
| `ct-external-dns-use1-<suffix>` (`DOMAIN`) | `kube-system:external-dns` | `route53:ChangeResourceRecordSets` on the `DOMAIN` zone; list/read on zones |
| `ct-aws-mcp-use1-<suffix>` | `consoletrack:aws-mcp` | `cloudtrail:LookupEvents` and `ec2:DescribeSecurityGroups` (account-wide, the two accepted gaps); IAM get/list and `SimulatePrincipalPolicy` under `WATCHED_IAM_PATHS`; `eks:DescribePodIdentityAssociation` on this cluster; `s3:GetObject` on `demo-app/us-east-1/terraform.tfstate` only |
| `ct-worker-use1-<suffix>` | `consoletrack:worker` | Receive, delete, change visibility on the change queue; `GetQueueAttributes` on both queues; `cloudtrail:GetTrailStatus` on the trail |
| `ct-canary-use1-<suffix>` | `consoletrack:canary` | `ec2:CreateTags` on the canary group only, tag key `ct:canary` only |

### For the Helm + deploy issue

- Bind the group `consoletrack-deployers` (output `deploy_kubernetes_group`) with RoleBindings in
  the `consoletrack` and `demo` namespaces only.
- The boundary has no WAF, Shield, or Cognito: run the Load Balancer Controller with
  `enableWaf=false`, `enableWafv2=false`, and `enableShield=false`.
- Service account names must match the table above.

## The event pattern

`patterns/changes.json` is the rule's whole pattern. It keeps write calls (`readOnly: false`) to
IAM, EC2, S3, KMS, and EKS, and drops three kinds of caller:

- **Pipeline sessions**: `*:assumed-role/ct-gh-*/gh-*`. The CI roles' trust policies require a
  `gh-` session name, so this matches only GitHub runs. A pipeline role used under any other session
  name still comes through.
- **AWS services acting on your behalf**: any event with `userIdentity.invokedBy`.
- **ConsoleTrack's platform roles**: the cluster and node roles (`ct-eks-*`), CNI, EBS CSI, Load
  Balancer Controller, and ExternalDNS. `ct-canary` is deliberately not excluded, and neither is the
  demo developer role.

`tests/test_event_pattern.py` (`make test`) runs recorded events in `tests/fixtures/events/` through
a local matcher. To check one against the real service:

```sh
aws events test-event-pattern \
  --event-pattern file://terraform/consoletrack/us-east-1/patterns/changes.json \
  --event file://terraform/consoletrack/us-east-1/tests/fixtures/events/matched/canary-create-tags.json
```

## Cost while up

About **$0.20/hour** with the defaults, about $0.24 with `EXPOSURE=alb` (the ALB itself comes from
the Load Balancer Controller, not Terraform).

| Item | $/hour |
|---|---|
| EKS control plane, standard support | 0.100 |
| 2× t4g.medium Spot nodes | 0.040 |
| One NAT gateway + its public IPv4 (plus $0.045/GB processed) | 0.050 |
| EBS gp3, about 60 GB | 0.007 |
| **Total** | **about 0.20** |

The trail's first copy of management events is free; 14 days of logs and SQS polling cost cents.

## Apply and destroy order

Apply: `consoletrack/global` (once, by a human), then this root, then `demo-app/us-east-1`.
Destroy in reverse: `demo-app/us-east-1` **before** this root (it finds the cluster and VPC by
name), and remove the Helm releases first so the Load Balancer Controller deletes its ALB and
security groups before the VPC goes. `make down` does this; `global` and the state bucket stay.

```sh
make tf-init ROOT=consoletrack/us-east-1
make tf-plan ROOT=consoletrack/us-east-1   # compare with the counts above
make tf-apply ROOT=consoletrack/us-east-1  # humans only
```

Leave `OPERATOR_ARN` set before CI applies this root, or cluster admin would move to the CI role.

## Checks without AWS

```sh
make lint-terraform tflint tf-validate tf-test test-terraform
```

`make tf-test` runs `tests/us-east-1.tftest.hcl`: names, the pattern file wiring, the policies
(no `"*"` beyond the two accepted reads, aws-mcp read-only and path-scoped, canary tag-only), the
feature switches, and input validation. Every module and data source that would call AWS is
overridden.
