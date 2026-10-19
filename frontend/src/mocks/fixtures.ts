// Sample data from the demo scenarios (docs/mockups/change-log.png), typed against the generated
// schema so a contract change that breaks it fails `npm run typecheck`. Times are relative to
// when the module loads, so "4 min ago" stays true in `npm run dev:mock`. The account ID is the
// documentation placeholder (AGENTS.md → Hard rules).
import type { ChangeSummary, IntakeHealth, User } from "../api";

const ACCOUNT = "123456789012";
const DEVELOPER = `arn:aws:sts::${ACCOUNT}:assumed-role/ct-demo-developer/jdev`;
const SG_ARN = `arn:aws:ec2:us-east-1:${ACCOUNT}:security-group/sg-0a1b2c3d4e5f60718`;
const ROLE_ARN = `arn:aws:iam::${ACCOUNT}:role/demo-app/demo-app`;

const minute = 60_000;
export const ago = (ms: number, now = Date.now()): string => new Date(now - ms).toISOString();

export const mockUsers: Record<string, { password: string; user: User }> = {
  admin: { password: "admin", user: { username: "admin", role: "admin" } },
  viewer: { password: "viewer", user: { username: "viewer", role: "viewer" } },
};

function change(c: Omit<ChangeSummary, "service" | "detected_at"> & { service?: string }): ChangeSummary {
  return {
    ...c,
    service: c.service ?? "ec2",
    detected_at: new Date(Date.parse(c.event_time) + 2 * minute).toISOString(),
  };
}

export function buildChanges(now = Date.now()): ChangeSummary[] {
  return [
    change({
      id: 42,
      title: "SSH opened to the internet on the demo security group",
      event_name: "AuthorizeSecurityGroupIngress",
      event_time: ago(4 * minute, now),
      resource_arn: SG_ARN,
      resource_name: "demo-app-sg",
      tf_address: "aws_security_group.demo_app",
      principal_arn: DEVELOPER,
      session_name: "jdev",
      via: "console",
      risk: "high",
      status: "awaiting_review",
      linked_incidents: [],
    }),
    change({
      id: 41,
      title: "s3:GetObject removed from the demo app role",
      event_name: "PutRolePolicy",
      service: "iam",
      event_time: ago(18 * minute, now),
      resource_arn: ROLE_ARN,
      resource_name: "demo-app",
      tf_address: "aws_iam_role_policy.demo_app",
      principal_arn: DEVELOPER,
      session_name: "jdev",
      via: "cli",
      risk: "high",
      status: "reverting",
      linked_incidents: [
        { id: 12, alertname: "CrashLoopBackOff", namespace: "demo", workload: "demo-app", status: "remediating" },
      ],
    }),
    change({
      id: 40,
      title: "Trust policy edited on the demo app role",
      event_name: "UpdateAssumeRolePolicy",
      service: "iam",
      event_time: ago(62 * minute, now),
      resource_arn: ROLE_ARN,
      resource_name: "demo-app",
      tf_address: "aws_iam_role.demo_app",
      principal_arn: `arn:aws:sts::${ACCOUNT}:assumed-role/AWSReservedSSO_AdministratorAccess_0f1e2d3c4b5a6978/jsprattler`,
      session_name: "jsprattler",
      via: "terraform_outside_pipeline",
      risk: "medium",
      status: "awaiting_review",
      linked_incidents: [],
    }),
    change({
      id: 39,
      title: "Tags added to the demo bucket",
      event_name: "PutBucketTagging",
      service: "s3",
      event_time: ago(26 * 60 * minute, now),
      resource_arn: "arn:aws:s3:::demo-app-data",
      resource_name: "demo-app-data",
      tf_address: "aws_s3_bucket.demo_app",
      principal_arn: `arn:aws:sts::${ACCOUNT}:assumed-role/finops-automation/cost-tagger`,
      session_name: "cost-tagger",
      via: "sdk",
      risk: "low",
      status: "accepted",
      linked_incidents: [],
    }),
    change({
      id: 38,
      title: "ReadOnlyAccess attached to the demo app role",
      event_name: "AttachRolePolicy",
      service: "iam",
      event_time: ago(29 * 60 * minute, now),
      resource_arn: ROLE_ARN,
      resource_name: "demo-app",
      tf_address: "aws_iam_role.demo_app",
      principal_arn: DEVELOPER,
      session_name: "jdev",
      via: "console",
      risk: "high",
      status: "reverted",
      linked_incidents: [],
    }),
    change({
      id: 37,
      title: "Port 8080 opened to the office range",
      event_name: "AuthorizeSecurityGroupIngress",
      event_time: ago(5 * 24 * 60 * minute, now),
      resource_arn: SG_ARN,
      resource_name: "demo-app-sg",
      tf_address: "aws_security_group.demo_app",
      principal_arn: DEVELOPER,
      session_name: "jdev",
      via: "cloudshell",
      risk: "medium",
      status: "accepted",
      linked_incidents: [],
    }),
  ];
}

export function buildIntakeHealth(overrides: Partial<IntakeHealth> = {}, now = Date.now()): IntakeHealth {
  return {
    state: "live",
    reasons: [],
    region: "us-east-1",
    checked_at: ago(0, now),
    last_event_at: ago(2 * minute, now),
    last_canary_at: ago(6 * minute, now),
    queue_backlog: 0,
    oldest_message_age_seconds: 0,
    dead_letter_count: 0,
    trail_logging: true,
    grafana_url: "http://localhost:3000/d/consoletrack-intake/intake-health",
    ...overrides,
  };
}
