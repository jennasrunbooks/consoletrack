import { buildChanges } from "../../mocks/fixtures";
import { chipCount, chipSelected, parseFilters, STATUS_CHIPS, toQuery } from "./filters";

const parse = (qs: string) => parseFilters(new URLSearchParams(qs));

describe("parseFilters", () => {
  it("reads every filter from the URL", () => {
    const f = parse(
      "status=reverted&status=accepted&risk=high&principal=jdev&service=IAM&since=2026-10-01&until=2026-10-17&q=sg&page=2",
    );
    expect(f).toEqual({
      status: ["reverted", "accepted"],
      risk: ["high"],
      principal: "jdev",
      service: "iam",
      since: "2026-10-01",
      until: "2026-10-17",
      q: "sg",
      page: 2,
    });
  });

  it("drops values the API would reject", () => {
    const f = parse("status=bogus&risk=extreme&service=ec2;drop&since=yesterday&page=-3");
    expect(f.status).toEqual([]);
    expect(f.risk).toEqual([]);
    expect(f.service).toBe("");
    expect(f.since).toBe("");
    expect(f.page).toBe(1);
  });
});

describe("toQuery", () => {
  it("sends only the filters that are set", () => {
    expect(toQuery(parse(""))).toEqual({ page: 1, page_size: 25 });
  });

  it("turns inclusive days into the API's [since, until) range in local time", () => {
    const q = toQuery(parse("since=2026-10-01&until=2026-10-17"));
    expect(q.since).toBe(new Date(2026, 9, 1).toISOString());
    expect(q.until).toBe(new Date(2026, 9, 18).toISOString());
  });
});

describe("status chips", () => {
  const counts = {
    by_status: {
      detected: 0,
      awaiting_review: 2,
      reverting: 1,
      reverted: 1,
      accepted: 2,
      codifying: 0,
      codified: 0,
      failed: 0,
    },
    total: 6,
    awaiting_review: 2,
    high_risk_open: 2,
    linked_to_incident: 1,
    closed_last_7_days: 3,
  };
  const chip = (key: string) => STATUS_CHIPS.find((c) => c.key === key)!;

  it("counts like the mockup: All (6), Awaiting review (2), Reverting (1), Closed (3)", () => {
    expect(["all", "awaiting_review", "reverting", "closed"].map((k) => chipCount(chip(k), counts))).toEqual([
      6, 2, 1, 3,
    ]);
    expect(buildChanges()).toHaveLength(6);
  });

  it("selects Closed only for exactly reverted + accepted + codified", () => {
    expect(chipSelected(chip("closed"), ["codified", "reverted", "accepted"])).toBe(true);
    expect(chipSelected(chip("closed"), ["reverted"])).toBe(false);
    expect(chipSelected(chip("all"), [])).toBe(true);
  });
});
