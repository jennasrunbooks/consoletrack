# Eval results

`make local-eval-oom` (on kind, about 75 minutes for the default 5 runs) writes one Markdown file here per
evaluation, `eval-oom-<UTC timestamp>.md`: a summary line (`eval-oom: 5/5 PASS`) and one row per run
with the agent's diagnosis, proposal, gate mode, remediation outcome, model, tool calls, tokens, and
the time from incident open to resolved. A run passes when the proposal is `set_memory_limit` →
`256Mi` for `demo/demo-app/demo-app` and the fix succeeded.

The files are gitignored: paste the table into the PR (or the issue) that needs the evidence, as
[docs/remediation-graduation.md](../../docs/remediation-graduation.md) describes.
