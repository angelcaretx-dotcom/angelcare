import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, submitTripRequest, type TripRequestInput } from "./api";

const input: TripRequestInput = {
  full_name: "Jane Doe",
  phone: "817-555-0100",
  email: "jane@example.com",
  pickup_address: "1 Main St, Fort Worth, TX",
  dropoff_address: "2 Clinic Rd, Fort Worth, TX",
  requested_datetime: "2026-11-02T15:30:00.000Z",
  service_type: "wheelchair",
};

function mockFetch(response: Partial<Response>) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("submitTripRequest", () => {
  it("POSTs JSON to the versioned trip-requests endpoint", async () => {
    const fetchMock = mockFetch({ ok: true, json: async () => ({ id: "abc" }) });

    await expect(submitTripRequest(input)).resolves.toEqual({ id: "abc" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/v1/trip-requests/");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(JSON.parse(init.body)).toEqual(input);
  });

  it("throws ApiError carrying the backend's field errors on a 400", async () => {
    mockFetch({
      ok: false,
      status: 400,
      json: async () => ({ email: ["Enter a valid email address."] }),
    });

    const error = await submitTripRequest(input).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(400);
    expect(error.fieldErrors).toEqual({ email: ["Enter a valid email address."] });
  });

  it("throws ApiError without field errors when the error body isn't JSON", async () => {
    mockFetch({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError("Unexpected token <");
      },
    });

    const error = await submitTripRequest(input).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(502);
    expect(error.fieldErrors).toBeUndefined();
  });
});
