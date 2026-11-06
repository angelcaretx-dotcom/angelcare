import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { facilityApi, FacilityApiError } from "./facility";

function respond(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body } as Response;
}

beforeEach(() => {
  document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("facilityApi", () => {
  it("signs in in two steps, same-origin, with the CSRF token on both", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith("/csrf")) {
        document.cookie = "csrftoken=tok456; path=/";
        return respond(200, { ok: true });
      }
      if (url.endsWith("/login")) return respond(200, { code_sent: true });
      return respond(200, { email: "p@h.example.com", facility: { name: "Harris" }, idle_timeout_seconds: 900 });
    });
    vi.stubGlobal("fetch", fetchMock);

    expect(await facilityApi.login("p@h.example.com", "pw")).toEqual({ code_sent: true });
    const me = await facilityApi.loginCode("123456");

    expect(me.facility.name).toBe("Harris");
    const urls = fetchMock.mock.calls.map((call) => call[0]);
    expect(urls).toEqual(["/api/v1/facility/csrf", "/api/v1/facility/login", "/api/v1/facility/login/code"]);
    for (const [, init] of fetchMock.mock.calls.slice(1)) {
      expect(init.credentials).toBe("same-origin");
      expect(init.headers["X-CSRFToken"]).toBe("tok456");
    }
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({ code: "123456" });
  });

  it("reports a signed-out answer so the page can send the user to sign in", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(403, { detail: "Not signed in." })));
    const error = await facilityApi.trips().catch((e) => e);
    expect(error).toBeInstanceOf(FacilityApiError);
    expect(error.signedOut).toBe(true);
  });

  it("passes booking field errors through", async () => {
    document.cookie = "csrftoken=tok; path=/";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(400, { pickup_zip: ["Enter a 5-digit ZIP code."] })));
    const error = await facilityApi.book({} as never).catch((e) => e);
    expect(error.fieldErrors.pickup_zip).toEqual(["Enter a 5-digit ZIP code."]);
  });
});
