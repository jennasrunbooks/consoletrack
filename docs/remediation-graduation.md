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
| Eval | 5 of 5 `make local-eval-oom` runs propose `set_memory_limit` → 256Mi for `demo/demo-app/demo-app`. Met: 5/5 on Sonnet and 5/5 on Haiku with the fix applied and verified (`minConfidence: medium`); 10/10 correct proposals earlier, held at `high` | #60, PR #95; [tables below](#eval-results-and-the-confidence-floor) |

**Phase 1 evidence: `make local-eval-oom` on kind (Oct 24): 0/5 on Haiku and 1/5 on Sonnet at
`minConfidence: high`, then 5/5 on both Sonnet and Haiku at `minConfidence: medium`. The policy ships with
`medium`. The tables and the reasoning are in
[Eval results and the confidence floor](#eval-results-and-the-confidence-floor).** The eval ran after
`oom-memory-bump` had already shipped as `mode: auto`, an exception to criterion 5 for the course
timeline. The kill switch and the automatic rollback are what made that acceptable.

### Eval results and the confidence floor

`make local-eval-oom` (#60, PR #95) runs the OOM scenario N times from a clean demo-app and a fresh
database. A run **passes** when the proposal is `set_memory_limit` → `256Mi` for
`demo/demo-app/demo-app` **and** the auto fix succeeded, which also needs the gate to let it
through. Results are written to [eval/results/](../eval/results/README.md).

**Summary of the four evaluations (Oct 24).** Per-run tables follow.

| Model | `minConfidence` | Result | Note |
|---|---|---|---|
| Haiku | high | 0/5 | proposal correct 5/5; confidence `medium` every run, so the gate sent all to manual |
| Sonnet | high | 1/5 | proposal correct 5/5; only one run self-rated `high` |
| Sonnet | medium | **5/5** | 3:00 to 4:30 from open to resolved |
| Haiku | medium | **5/5** | 3:00 to 3:30; the default `TRIAGE_MODEL` |

The proposal (`set_memory_limit` → `256Mi` for `demo/demo-app/demo-app`) was correct in **20 of 20**
runs across both models. The graduation evidence covers both models. EKS runs the default (Haiku),
so its 5/5 is the evidence for the shipped configuration; Sonnet's 5/5 shows the result doesn't
depend on the model. The result files are gitignored (see [eval/results/](../eval/results/README.md)),
so the per-run tables below are the record.

**Next (Phase 2).** Gate on evidence (`OOMKilled` read through `k8s-mcp`) instead of self-reported
confidence ([#100](https://github.com/jennasrunbooks/consoletrack/issues/100)), and a prompt v5
that defines what `low`, `medium`, and `high` mean.

**Haiku (`claude-haiku-5-5`, the default `TRIAGE_MODEL`): 0/5 PASS**

| Run | Result | Diagnosis | Confidence | Proposal | Mode | Remediation | Model | Escalated | Tool calls | Input tokens | Output tokens | Incident open → resolved |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | FAIL | The demo-app container is OOM-killed during startup (its S3 read and report load) because its 128Mi memory limit is too small for its peak usage. The limit has… | medium | set_memory_limit → 256Mi (demo/demo-app/demo-app) | manual | proposed | claude-haiku-5-5 | n | 7 | 36633 | 3241 | - |
| 2 | FAIL | The demo-app container is OOM-killed (exit 137, reason OOMKilled) about 17 seconds after start, while it loads its S3 object into memory. Its 128Mi memory limi… | medium | set_memory_limit → 256Mi (demo/demo-app/demo-app) | manual | proposed | claude-haiku-5-5 | n | 7 | 89087 | 7215 | - |
| 3 | FAIL | The demo-app container is OOMKilled (exit 137) shortly after start, because its 128Mi memory limit is too small for its startup work (the S3 report read and bu… | medium | set_memory_limit → 256Mi (demo/demo-app/demo-app) | manual | proposed | claude-haiku-5-5 | n | 7 | 39384 | 3026 | - |
| 4 | FAIL | The demo-app container's 128Mi memory limit is below its working set during report generation. The kubelet OOM-kills it (exit 137, reason OOMKilled), so the De… | medium | set_memory_limit → 256Mi (demo/demo-app/demo-app) | manual | proposed | claude-haiku-5-5 | n | 7 | 63438 | 3339 | - |
| 5 | FAIL | The demo-app container exceeds its 128Mi memory limit during startup and is OOM-killed (exit 137, reason OOMKilled). The limit was never changed (one revision)… | medium | set_memory_limit → 256Mi (demo/demo-app/demo-app) | manual | proposed | claude-haiku-5-5 | n | 7 | 41628 | 3054 | - |

`eval-oom: 0/5 PASS`

**Sonnet (`claude-sonnet-5-5`): 1/5 PASS**

| Run | Result | Diagnosis | Confidence | Proposal | Mode | Remediation | Model | Escalated | Tool calls | Input tokens | Output tokens | Incident open → resolved |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | FAIL | The demo-app container is repeatedly killed (exit 137, most likely OOM) at its unchanged 128Mi memory limit, shortly after start. Its memory demand, probably t… | medium | set_memory_limit → 256Mi (demo/demo-app/demo-app) | manual | proposed | claude-sonnet-5-5 | n | 6 | 27593 | 2232 | - |
| 2 | FAIL | The demo-app container exceeds its 128Mi memory limit shortly after starting and is OOMKilled (exit 137). It restarts in a loop and returns connection-refused … | medium | set_memory_limit → 256Mi (demo/demo-app/demo-app) | manual | proposed | claude-sonnet-5-5 | n | 5 | 26563 | 1923 | - |
| 3 | FAIL | The demo-app container exceeds its 128Mi memory limit shortly after start and is OOMKilled (exit 137), which causes a CrashLoopBackOff and failed requests. The… | medium | set_memory_limit → 256Mi (demo/demo-app/demo-app) | manual | proposed | claude-sonnet-5-5 | n | 5 | 28461 | 1956 | - |
| 4 | FAIL | The demo-app container exceeds its 128Mi memory limit shortly after start and is OOMKilled (exit 137). The limit has been the same since revision 1, so the wor… | medium | set_memory_limit → 256Mi (demo/demo-app/demo-app) | manual | proposed | claude-sonnet-5-5 | n | 5 | 30727 | 1952 | - |
| 5 | PASS | The demo-app container exceeds its 128Mi memory limit and is OOMKilled (exit 137) in a restart loop. The limit has not changed since revision 1, so the workloa… | high | set_memory_limit → 256Mi (demo/demo-app/demo-app) | auto | succeeded | claude-sonnet-5-5 | n | 5 | 32659 | 2018 | 3:00 |

`eval-oom: 1/5 PASS`

(One earlier single run, `make local-oom-run` on Sonnet, also reached `high` and passed.)

**Sonnet (`claude-sonnet-5-5`) with `minConfidence: medium`: 5/5 PASS** (Oct 24, same harness and
fault, after the policy change)

| Run | Result | Diagnosis | Confidence | Proposal | Mode | Remediation | Model | Escalated | Tool calls | Input tokens | Output tokens | Incident open → resolved |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | PASS | The demo-app container exceeds its 128Mi memory limit shortly after startup and is OOMKilled (exit 137). The limit is unchanged since revision 1, so the worklo… | high | set_memory_limit → 256Mi (demo/demo-app/demo-app) | auto | succeeded | claude-sonnet-5-5 | n | 5 | 32300 | 1881 | 3:00 |
| 2 | PASS | The demo-app container exceeds its 128Mi memory limit shortly after startup and is OOMKilled (exit 137). The limit was not changed in any revision, so the work… | high | set_memory_limit → 256Mi (demo/demo-app/demo-app) | auto | succeeded | claude-sonnet-5-5 | n | 5 | 31636 | 1812 | 3:00 |
| 3 | PASS | The demo-app container exceeds its 128Mi memory limit shortly after startup and is OOMKilled (exit 137). The workload has outgrown its limit, and no revision l… | medium | set_memory_limit → 256Mi (demo/demo-app/demo-app) | auto | succeeded | claude-sonnet-5-5 | n | 5 | 31018 | 1974 | 3:30 |
| 4 | PASS | The demo-app container exceeds its 128Mi memory limit shortly after startup and is OOM-killed (exit 137). The resulting restart loop makes the service refuse c… | high | set_memory_limit → 256Mi (demo/demo-app/demo-app) | auto | succeeded | claude-sonnet-5-5 | n | 5 | 30027 | 1875 | 4:30 |
| 5 | PASS | The demo-app container is OOMKilled (exit 137) at its 128Mi memory limit shortly after startup. The limit has not changed across revisions, so the workload's m… | high | set_memory_limit → 256Mi (demo/demo-app/demo-app) | auto | succeeded | claude-sonnet-5-5 | n | 5 | 31015 | 1796 | 3:00 |

`eval-oom: 5/5 PASS`. Every run proposed `set_memory_limit` → `256Mi` for `demo/demo-app/demo-app`,
the gate applied it automatically, the fix succeeded (verify passed), and the incident resolved
3–4.5 minutes after it opened. Run 3 reached only `medium` and passed, which is the case the
`medium` floor exists for; at `high` it would have waited for a human.

**Haiku (`claude-haiku-5-5`) with `minConfidence: medium`: 5/5 PASS** (Oct 24, same harness and
fault)

| Run | Result | Diagnosis | Confidence | Proposal | Mode | Remediation | Model | Escalated | Tool calls | Input tokens | Output tokens | Incident open → resolved |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | PASS | The demo-app container's 128Mi memory limit is too low for its startup or report workload. Its memory spikes past the limit and the kernel OOM-kills it (OOMKil… | medium | set_memory_limit → 256Mi (demo/demo-app/demo-app) | auto | succeeded | claude-haiku-5-5 | n | 7 | 39638 | 3075 | 3:30 |
| 2 | PASS | The demo-app container exceeds its 128Mi memory limit during startup/report generation and is OOM-killed (exit 137) repeatedly. The limit was not changed in an… | medium | set_memory_limit → 256Mi (demo/demo-app/demo-app) | auto | succeeded | claude-haiku-5-5 | n | 7 | 46602 | 2837 | 3:30 |
| 3 | PASS | The demo-app container outgrows its 128Mi memory limit. The kernel OOM-kills it (OOMKilled, exit 137) during startup or early operation. The limit has been unc… | high | set_memory_limit → 256Mi (demo/demo-app/demo-app) | auto | succeeded | claude-haiku-5-5 | n | 7 | 46924 | 2979 | 3:30 |
| 4 | PASS | The demo-app container's 128Mi memory limit is too low for its working set. The container is OOM-killed (exit 137, reason OOMKilled) and restarts in a loop. Th… | medium | set_memory_limit → 256Mi (demo/demo-app/demo-app) | auto | succeeded | claude-haiku-5-5 | n | 7 | 48910 | 3140 | 3:30 |
| 5 | PASS | The demo-app container is OOM-killed (exit 137, no liveness failure) because its 128Mi memory limit is below its runtime memory use. The limit has been unchang… | medium | set_memory_limit → 256Mi (demo/demo-app/demo-app) | auto | succeeded | claude-haiku-5-5 | n | 7 | 48148 | 3101 | 3:00 |

`eval-oom: 5/5 PASS`. Four of five runs reached only `medium`, so at `high` this would have been
1 of 5, as before. Cost per run is about 40–49k input and 3k output tokens on Haiku, against
30–32k and about 1.9k on Sonnet (Haiku makes 7 tool calls to Sonnet's 5).

**What the runs show.** The proposal was `set_memory_limit` → `256Mi` for the right target in 10 of
10 runs on both models. The only thing that failed was the confidence floor: `high` in 1 of 10, and
in the Sonnet runs the same prompt and the same fault gave `medium` four times and `high` once. So
the agent's self-reported `high` vs `medium` is not a signal that separates a good proposal from a
bad one here; it varies between identical runs. The failing diagnoses hedge ("most likely OOM",
"probably"), and the passing one doesn't, but the evidence behind each wasn't saved
(the database is reset between runs); the harness now saves it for failing runs.

**Why the floor is `medium` for `oom-memory-bump`.** With `high`, the policy would never apply a
fix that was right 10 times in 10, and the auto mode would exist only on paper. What makes the fix
safe doesn't depend on the agent's confidence:

- the proposal schema has no free-form patch, and the validator allows only the container's memory
  limit (and request), at most 2x the current limit and at most 512Mi, in an allowlisted namespace;
- a server-side dry run checks the live object first;
- verify (rollout, no new `OOMKilled`, incident resolved or error ratio below 0.05) and the
  automatic rollback undo the one patch if it doesn't hold;
- one auto fix per workload per hour, no further auto attempts after a failure, and the kill switch.

`low` confidence still goes to a human, and the setting applies to this policy only: the `default`
policy stays manual and no other kind can be `auto`.

**What this doesn't show.**
- **The model:** both models passed 5/5 at the `medium` floor, so the evidence covers the default
  `TRIAGE_MODEL` (Haiku) and Sonnet. Haiku reaches `high` rarely (1 of 5), which makes the floor
  what lets it work at all; with `high`, only Sonnet-class runs would sometimes apply.
- **Sample size:** 5 runs on one fault on kind. It meets the criterion; it doesn't prove the rate is
  100%. The kill switch and the rollback remain the backstop.
- **The weak input:** confidence is the one gate input the agent controls itself, and the floor
  didn't make it more reliable: a confidently wrong proposal passes `high` as easily as `medium`.
  Phase 2 replaces it with a check of the evidence itself
  ([#100](https://github.com/jennasrunbooks/consoletrack/issues/100), the evidence-based gate).

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
5. the agent's confidence is at least the policy's `minConfidence` (`medium` for `oom-memory-bump`). After an escalation
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
