import { http, HttpResponse } from "msw";
import { mockUsers } from "../mocks/fixtures";
import { errorResponse, mockSession } from "../mocks/handlers";
import { server } from "../mocks/server";
import { recordRequests } from "../test/render";
import { ApiError, getCurrentUser, listChanges, login, setUnauthorizedListener } from "./client";

describe("api client", () => {
  it("repeats array query parameters (status=a&status=b)", async () => {
    mockSession.user = mockUsers.admin!.user;
    const seen = recordRequests("/changes");
    await listChanges({ status: ["reverted", "accepted"], risk: ["high"], page: 1 });
    expect(seen[0]!.searchParams.getAll("status")).toEqual(["reverted", "accepted"]);
    expect(seen[0]!.searchParams.get("risk")).toBe("high");
  });

  it("throws ApiError with the Error body", async () => {
    await expect(login("admin", "nope")).rejects.toMatchObject({ status: 401, code: "unauthorized" });
  });

  it("reads Retry-After on 429", async () => {
    server.use(
      http.post("*/api/v1/auth/login", () =>
        errorResponse(429, "too_many_requests", "slow down", { headers: { "Retry-After": "240" } }),
      ),
    );
    const err = await login("admin", "x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).retryAfter).toBe(240);
  });

  it("copes with a non-JSON error (a proxy's 502 page)", async () => {
    mockSession.user = mockUsers.admin!.user;
    server.use(http.get("*/api/v1/changes", () => new HttpResponse("<html>Bad Gateway</html>", { status: 502 })));
    await expect(listChanges({})).rejects.toMatchObject({ status: 502, code: "http_error" });
  });

  it("reports a network failure as status 0", async () => {
    mockSession.user = mockUsers.admin!.user;
    server.use(http.get("*/api/v1/changes", () => HttpResponse.error()));
    await expect(listChanges({})).rejects.toMatchObject({ status: 0, code: "network_error" });
  });

  it("returns null from getCurrentUser when logged out, without signalling expiry", async () => {
    const listener = vi.fn();
    setUnauthorizedListener(listener);
    await expect(getCurrentUser()).resolves.toBeNull();
    expect(listener).not.toHaveBeenCalled();
    setUnauthorizedListener(null);
  });

  it("signals expiry on a 401 from a data call", async () => {
    const listener = vi.fn();
    setUnauthorizedListener(listener);
    await expect(listChanges({})).rejects.toMatchObject({ status: 401 });
    expect(listener).toHaveBeenCalledOnce();
    setUnauthorizedListener(null);
  });
});
