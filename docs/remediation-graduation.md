# Remediation graduation

How a remediation kind earns `auto` mode, which kinds have, and why AWS-side fixes don't. The
spec's view is [`product-spec.md`](../product-spec.md) → Remediation modes; this file is the
record of what ships. Read it before changing the policy gate or adding a remediation kind.

The agent never applies anything (AGENTS.md rule 7). It proposes one narrow kind through
`submit_analysis`; the worker's policy gate decides `manual` or `auto`; the backend validates and
applies under its own `remediator` ServiceAccount. `auto` only means the gate approves on an
admin's behalf. Every check below runs for a human's approval too.

```text
agent ──submit_analysis──▶ proposed ──gate──▶ manual: waits for an admin ──approve──┐
                                       └────▶ auto: approved by policy:<name> ────────┤
                                                                                      ▼
            backend apply loop: validate → dry run → patch → rollout → verify ─▶ succeeded
                                    │ refused (nothing applied)          │ failed
                                    ▼                                    ▼
                    auto: back to manual, reason kept     rollback (inverse patch) → failed,
                    manual: failed                        incident back to awaiting_review
```

## Phases

| Phase | `auto` kinds | Status |
| --- | --- | --- |
| 0 | None: every proposal waits for an admin | Shipped with the agent loop (#57) |
| 1 | `set_memory_limit` in `demo` (policy `oom-memory-bump`) | This issue (#34) |
| Later candidates | `rollback` (rollout undo) after a bad image or a lowered limit; probe fixes | The agent already proposes `rollback` (prompt v3, #60), but it stays manual and the backend can't apply it yet: approving answers `501`. Each needs every criterion below |
| Never in Phase 1 | `revert_workflow`, `codify_pr`, anything that changes AWS | See [Why AWS-side fixes stay manual](#why-aws-side-fixes-stay-manual) |

## Graduation criteria

A kind moves to `auto` only through a reviewed PR that changes its policy's `mode`, with the eval
output attached, and only when all of these hold:

1. **Deterministic validator.** Plain code decides whether the proposal is allowed; no model is
   asked. The same rules run in the gate and again against the live object before applying.
2. **Bounded change.** The kind can change one named field set, by a bounded amount, in an
   allowlisted namespace. The proposal schema has no free-form patch to fill in.
3. **Verifiable outcome.** The backend can tell, within minutes and without the agent, whether
   the fix worked.
4. **Automatic rollback.** If verify fails, the backend undoes exactly what it applied, and the
   workload gets no further auto attempts.
5. **A passing eval.** The agent picks this kind with the correct values in 5 of 5 runs of the
   scenario on kind (`make local-eval-oom`, #60; the full `make eval` in Phase 2).

### `set_memory_limit` against the criteria

| Criterion | How it's met | Where |
| --- | --- | --- |
| Validator | Namespace in `K8S_ALLOWED_NAMESPACES`; the object is a Deployment; the named container exists; only its `resources.limits.memory` changes, plus `requests.memory` if the request would exceed the new limit; the new limit is above the current one, at most 2x it, and at most 512Mi. Unparseable quantities are refused | `backend/app/services/remediation_validator.py` |
| Bounded | `allowedChanges` and `bounds` in the policy may only be tighter than the validator's own 2x and 512Mi; a looser config stops the backend and worker from starting, and fails `helm template` | `remediation_policy.py`, chart `_helpers.tpl` |
| Dry run | A server-side `dryRun=All` patch first; its result may differ from the live pod template only in that container's memory, so a mutating webhook or a merge surprise is caught before anything changes | `remediation_apply.py` |
| Verify | Rollout complete within `rolloutTimeout` (3m); then within `waitForResolved` (5m): no new `OOMKilled` in a pod running the new limit, and the incident resolved (Alertmanager) or the `DemoAppHighErrorRate` ratio below 0.05 (Prometheus) | `remediation_apply.py`, chart `values.yaml` |
| Rollback | The inverse strategic-merge patch: the previous limit and request back, which makes the pod template equal to the previous revision's, so the Deployment returns to that ReplicaSet as `kubectl rollout undo` would, without touching anything else. The incident goes back to `awaiting_review` with every step recorded | `remediation_apply.py` |
| Eval | 5 of 5 `make local-eval-oom` runs propose `set_memory_limit` → 256Mi for `demo/demo-app/demo-app` | #60; attach the output to the PR that sets `mode: auto` |

**Phase 1 evidence: 3 runs on kind (Oct 21); `make local-eval-oom` results are written to
[eval/results/](../eval/results/README.md) and pasted in #95 (#60). If it
fails, the policy reverts to manual.** `oom-memory-bump` ships as `mode: auto` before the eval (#60 item 5)
has run, which is an exception to criterion 5 for the course timeline. It's acceptable because
the kill switch and the automatic rollback are in place.

### Which OOMs reach `set_memory_limit`

Since prompt v3 (#60) the agent picks the kind from the rollout history. When the workload
outgrew a limit that didn't change (the demo fault: traffic fills a report cache past the
unchanged 128Mi limit, with no new revision), it proposes `set_memory_limit`, and this policy
can apply it. When the newest revision *lowered* the limit, it proposes `rollback` to the
previous revision instead. That one goes to the `default` policy, so an admin undoes the revision
by hand or rejects the proposal. Since prompt v4 (#79), a revision marked `remediation` in the rollout
history (the apply loop's own fix or rollback, from the `consoletrack.io/remediation` and
`consoletrack.io/action` annotations) is neither a human change nor a revision to roll back to.

## The gate's checks

`backend/app/services/policy_gate.py`, in this order. The first that fails sends the proposal to
a human, with the reason stored as `remediations.gate_reason`:

1. the matching policy (first match in `remediation.policies`) is `auto`;
2. the kind isn't AWS-side (config validation already refuses that; checked again here);
3. the kill switch: `remediation.autoEnabled` in the policies file **and** `AUTOFIX_ENABLED`;
4. the incident is still `awaiting_review`;
5. the agent's confidence is at least the policy's `minConfidence` (`high`). After an escalation
   (#60) that's the escalated run's analysis, the incident's latest;
6. no earlier auto fix of this workload failed;
7. the rate limit: at most `rateLimit.perWorkload` (1) auto fixes per workload per
   `rateLimit.window` (1h);
8. the validator's cluster-free checks (namespace, allowed fields, ceiling).

Then the remediation is `approved` with `decided_by = policy:oom-memory-bump`, the incident moves
to `remediating`, and the backend's apply loop picks it up. If the live check, the validator, or
the dry run refuses it, nothing has been applied, so it goes back to `proposed` in `manual` mode
with the reason, and the incident returns to `awaiting_review`.

## Turning auto off

- **Without a release:** `kubectl -n consoletrack set env deploy/worker AUTOFIX_ENABLED=false`
  (the gate reads it; the backend doesn't need it). Locally: `AUTOFIX_ENABLED=false` in `.env`,
  then `make local`.
- **For good:** `remediation.autoEnabled: false`, or the policy back to `mode: manual`, in the
  chart's `values.yaml`, through a PR.
- **After a failure:** automatic. A failed auto fix (verify failed and was rolled back, the
  rollout timed out, or the patch failed) turns `auto` off for that workload: every later
  proposal for it waits for a human. The block is the failed row itself, so it survives
  restarts; there is no switch to lift it in Phase 1, by design: a regression means the kind
  goes back through review.

## Why AWS-side fixes stay manual

- **No bounded, verifiable fix.** A cluster memory bump is one field, bounded by 2x and a
  ceiling, verified in minutes, and undone with one patch. An AWS revert re-applies Terraform:
  its blast radius is whatever the plan contains, and an IAM or security-group change can break
  something unrelated without any alert ConsoleTrack watches.
- **The identity boundary.** The backend has no AWS role at all (`docs/permissions.md`). Reverts
  run in GitHub Actions under the `consoletrack-revert` environment, which requires a named
  reviewer; that human approval is the control, and auto mode would remove it.
- **Rollback is a second apply.** Undoing a bad revert is another Terraform run, not an inverse
  patch the backend can apply on its own.
- **Enforced, not just documented.** `revert_workflow` and `codify_pr` can't be set to `auto`:
  the backend and worker refuse to start with such a policy, and the chart fails to render.

A later phase could allow auto-revert for narrow, high-risk patterns (for example SSH opened to
`0.0.0.0/0`), under the same criteria, with its own validator and verify.

## Adding a kind

1. A narrow proposal model in `backend/app/agents/schema.py` (a new `SCHEMA_VERSION`).
2. Its validator in `remediation_validator.py`, with unit tests for every refusal.
3. Its apply, verify, and rollback in `remediation_apply.py` (`APPLY_KINDS`), with unit tests
   for the verify-fails → rollback path.
4. Any RBAC it needs on `remediator`, least privilege, with `kubectl auth can-i` checks in
   `scripts/test-k8s.sh` and the table in `docs/permissions.md`.
5. A `manual` policy in the chart. It graduates later, by its own PR, once the eval passes.
