import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { maybeSignedIn, riderApi, RiderApiError } from "./rider";

function respond(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body } as Response;
}

beforeEach(() => {
  document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("riderApi", () => {
  it("calls the API same-origin, fetching and sending the CSRF token on writes", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith("/csrf")) {
        document.cookie = "csrftoken=tok123; path=/";
        return respond(200, { ok: true });
      }
      return respond(200, { email: "a@example.com", addresses: [], idle_timeout_seconds: 900 });
    });
    vi.stubGlobal("fetch", fetchMock);

    await riderApi.login("a@example.com", "secret-pass");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/rider/csrf");
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/v1/rider/login");
    expect(init.credentials).toBe("same-origin");
    expect(init.headers["X-CSRFToken"]).toBe("tok123");
    expect(JSON.parse(init.body)).toEqual({ email: "a@example.com", password: "secret-pass" });
    expect(maybeSignedIn()).toBe(true);
  });

  it("reads trips without a CSRF round-trip", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, { trips: [{ id: "t1" }] }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await riderApi.trips()).toEqual([{ id: "t1" }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("turns errors into RiderApiError, and a signed-out answer clears the hint", async () => {
    window.localStorage.setItem("angelcare-rider", "1");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(403, { detail: "Not signed in." })));
    const error = await riderApi.me().catch((e) => e);
    expect(error).toBeInstanceOf(RiderApiError);
    expect(error.signedOut).toBe(true);
    expect(maybeSignedIn()).toBe(false);
  });

  it("exposes password rule failures as field errors", async () => {
    document.cookie = "csrftoken=tok; path=/";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(respond(400, { password: ["This password is too common."] })),
    );
    const error = await riderApi.setPassword("u", "t", "password").catch((e) => e);
    expect(error.fieldErrors.password).toEqual(["This password is too common."]);
  });
});
