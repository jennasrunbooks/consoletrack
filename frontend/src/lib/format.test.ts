import { changeLabel, duration, incidentTitle, principalLabel, relativeTime, safeHttpUrl } from "./format";

describe("changeLabel", () => {
  it("pads to four digits", () => {
    expect(changeLabel(42)).toBe("CHG-0042");
    expect(changeLabel(12345)).toBe("CHG-12345");
  });
});

describe("incidentTitle", () => {
  it("joins workload and alert name", () => {
    expect(
      incidentTitle({ id: 12, alertname: "DemoAppHighErrorRate", namespace: "demo", workload: "demo-app", status: "open" }),
    ).toBe("INC-12: demo-app DemoAppHighErrorRate");
    expect(incidentTitle({ id: 3, alertname: "NodeDown", namespace: null, workload: null, status: "open" })).toBe(
      "INC-3: NodeDown",
    );
  });
});

describe("principalLabel", () => {
  const acct = "123456789012";
  it("shows the session and role for an assumed role", () => {
    expect(principalLabel(`arn:aws:sts::${acct}:assumed-role/ct-demo-developer/jdev`, "jdev")).toEqual({
      who: "jdev",
      via: "ct-demo-developer",
    });
  });
  it("names the permission set for an SSO role", () => {
    expect(
      principalLabel(`arn:aws:sts::${acct}:assumed-role/AWSReservedSSO_AdministratorAccess_0f1e2d3c4b5a6978/js`, null),
    ).toEqual({ who: "js", via: "AdministratorAccess (SSO)" });
  });
  it("handles IAM users and root", () => {
    expect(principalLabel(`arn:aws:iam::${acct}:user/ops/alice`, null)).toEqual({ who: "alice", via: "IAM user" });
    expect(principalLabel(`arn:aws:iam::${acct}:root`, null)).toEqual({ who: "root", via: "Account root user" });
  });
});

describe("relativeTime", () => {
  const now = new Date(2026, 9, 17, 15, 0).getTime();
  const at = (...args: [number, number, number, number, number]) => new Date(...args).toISOString();
  it("matches the mockup's wording", () => {
    expect(relativeTime(at(2026, 9, 17, 14, 59), now)).toBe("1 min ago");
    expect(relativeTime(at(2026, 9, 17, 14, 56), now)).toBe("4 min ago");
    expect(relativeTime(at(2026, 9, 17, 13, 58), now)).toBe("1 h ago");
    expect(relativeTime(at(2026, 9, 16, 9, 0), now)).toBe("Yesterday");
    expect(relativeTime(at(2026, 9, 12, 9, 0), now)).toBe("Oct 12");
    expect(relativeTime(at(2025, 9, 12, 9, 0), now)).toBe("Oct 12, 2025");
    expect(relativeTime(new Date(now - 10_000).toISOString(), now)).toBe("just now");
  });
});

describe("duration", () => {
  it("picks a unit", () => {
    expect(duration(0)).toBe("0 s");
    expect(duration(720)).toBe("12 min");
    expect(duration(7200)).toBe("2 h");
  });
});

describe("safeHttpUrl", () => {
  it("allows only http(s)", () => {
    expect(safeHttpUrl("https://grafana.example.com/d/x")).toBe("https://grafana.example.com/d/x");
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("not a url")).toBeNull();
    expect(safeHttpUrl(null)).toBeNull();
  });
});
