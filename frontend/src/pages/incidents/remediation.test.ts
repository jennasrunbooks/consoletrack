import type { Remediation } from "../../api";
import { remediationResult, valueChanges } from "./remediation";

const step = (name: string, ok: boolean, detail = "") => ({ step: name, at: "2026-10-21T00:00:00Z", ok, detail });
const autoFix = {
  status: "succeeded",
  mode: "auto",
  before: { limits: { memory: "128Mi" }, requests: { memory: "64Mi" } },
  after: { limits: { memory: "256Mi" } },
  steps: [],
} satisfies Pick<Remediation, "status" | "mode" | "before" | "after" | "steps">;

describe("valueChanges", () => {
  it("names the memory values and keeps other paths as they are", () => {
    const after = { limits: { memory: "256Mi", cpu: "1" }, requests: { memory: "256Mi" } };
    expect(valueChanges(autoFix.before, after)).toEqual([
      { path: "limits.memory", label: "memory limit", before: "128Mi", after: "256Mi" },
      { path: "limits.cpu", label: "limits.cpu", before: null, after: "1" },
      { path: "requests.memory", label: "memory request", before: "64Mi", after: "256Mi" },
    ]);
  });
});

describe("remediationResult", () => {
  it("says an auto fix was fixed automatically, with before and after", () => {
    expect(remediationResult(autoFix)).toEqual({
      kind: "fixed",
      text: "Fixed automatically: memory limit 128Mi → 256Mi",
    });
  });

  it("says Fixed for a fix an admin approved", () => {
    expect(remediationResult({ ...autoFix, mode: "manual" })?.text).toBe("Fixed: memory limit 128Mi → 256Mi");
  });

  it("shows the rollback, or that the rollback failed", () => {
    const failed = { ...autoFix, status: "failed" as const };
    const rolledBack = [step("verify", false), step("rollback", true, "memory limit back to 128Mi")];
    expect(remediationResult({ ...failed, steps: rolledBack })).toEqual({
      kind: "rolled_back",
      text: "Rolled back: memory limit back to 128Mi",
    });
    const stuck = [step("rollback", false, "rollback failed, a human must fix it: 503")];
    expect(remediationResult({ ...failed, steps: stuck })?.kind).toBe("rollback_failed");
  });

  it("has nothing to say before the fix finishes, or when it failed before patching", () => {
    expect(remediationResult({ ...autoFix, status: "running" })).toBeNull();
    expect(remediationResult({ ...autoFix, status: "failed", steps: [step("validate", false, "too high")] })).toBeNull();
  });
});
