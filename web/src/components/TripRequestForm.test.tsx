import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TripRequestForm } from "./TripRequestForm";

// TZ is pinned to America/Chicago in vitest.config.mts, so a 9:30 AM
// pickup on 2026-11-02 (after DST ends) must be sent as 15:30 UTC.
const EXPECTED_UTC = "2026-11-02T15:30:00.000Z";

function mockFetch(response: Partial<Response> | Promise<never>) {
  const fetchMock =
    response instanceof Promise
      ? vi.fn().mockReturnValue(response)
      : vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function fillForm() {
  const user = userEvent.setup();
  render(<TripRequestForm />);
  await user.type(screen.getByLabelText(/Full name/), "Jane Doe");
  await user.type(screen.getByLabelText(/Phone number/), "817-555-0100");
  await user.type(screen.getByLabelText(/Email address/), "jane@example.com");
  const pickup = within(screen.getByRole("group", { name: /Pickup address/ }));
  await user.type(pickup.getByLabelText(/Street address/), "1 Main St");
  await user.type(pickup.getByLabelText(/Apt/), "Apt 4");
  await user.type(pickup.getByLabelText(/City/), "Fort Worth");
  await user.type(pickup.getByLabelText(/ZIP/), "76104");
  const dropoff = within(screen.getByRole("group", { name: /Drop-off address/ }));
  await user.type(dropoff.getByLabelText(/Street address/), "2 Clinic Rd");
  await user.type(dropoff.getByLabelText(/City/), "Arlington");
  await user.type(dropoff.getByLabelText(/ZIP/), "76010");
  // user-event can't type into date/time inputs in jsdom; set them directly.
  fireEvent.change(screen.getByLabelText(/Pickup date/), { target: { value: "2026-11-02" } });
  fireEvent.change(screen.getByLabelText(/Pickup time/), { target: { value: "09:30" } });
  await user.selectOptions(screen.getByLabelText(/Service type/), "wheelchair");
  await user.type(screen.getByLabelText(/Mobility requirements/), "Manual wheelchair");
  return user;
}

const submitButton = () => screen.getByRole("button", { name: /Submit/ });

/** This tab already confirmed jane@example.com with a code (ADR 0033). */
function rememberConfirmedEmail() {
  const expiresAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();
  sessionStorage.setItem(
    "angelcare-email-tokens",
    JSON.stringify({ "jane@example.com": { token: "test-token", expiresAt } }),
  );
}

beforeEach(rememberConfirmedEmail);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("TripRequestForm", () => {
  it("sends exactly the payload the API expects and shows the confirmation", async () => {
    const fetchMock = mockFetch({ ok: true, json: async () => ({ id: "abc" }) });
    const user = await fillForm();

    await user.click(submitButton());

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      full_name: "Jane Doe",
      phone: "817-555-0100",
      email: "jane@example.com",
      pickup_street: "1 Main St",
      pickup_unit: "Apt 4",
      pickup_city: "Fort Worth",
      pickup_state: "TX",
      pickup_zip: "76104",
      dropoff_street: "2 Clinic Rd",
      dropoff_unit: "",
      dropoff_city: "Arlington",
      dropoff_state: "TX",
      dropoff_zip: "76010",
      requested_datetime: EXPECTED_UTC,
      return_ride: "none",
      return_datetime: null,
      service_type: "wheelchair",
      mobility_notes: "Manual wheelchair",
      additional_notes: "",
      booked_for: "self",
      verification_token: "test-token",
    });
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("Request received.");
    // Must never read as a confirmed booking.
    expect(status.textContent).toContain("not a confirmed booking");
    expect(screen.queryByRole("button", { name: /Submit/ })).toBeNull();
  });

  it("shows the backend's field errors next to the matching fields", async () => {
    mockFetch({
      ok: false,
      status: 400,
      json: async () => ({
        email: ["Enter a valid email address."],
        requested_datetime: ["Pickup time must be in the future."],
      }),
    });
    const user = await fillForm();

    await user.click(submitButton());

    expect((await screen.findByRole("alert")).textContent).toContain("couldn't submit");
    const email = screen.getByLabelText(/Email address/);
    expect(email.getAttribute("aria-invalid")).toBe("true");
    expect(email.parentElement?.textContent).toContain("Enter a valid email address.");
    // requested_datetime has no input of its own; it's shown under the date.
    expect(screen.getByLabelText(/Pickup date/).parentElement?.textContent).toContain(
      "Pickup time must be in the future.",
    );
    // The entered values are kept so the user can fix and resubmit.
    expect((screen.getByLabelText(/Full name/) as HTMLInputElement).value).toBe("Jane Doe");
  });

  it("shows a fallback message when the request never reaches the API", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const user = await fillForm();

    await user.click(submitButton());

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Something went wrong submitting your request",
    );
  });

  it("disables the button while the request is in flight", async () => {
    mockFetch(new Promise<never>(() => {}));
    const user = await fillForm();

    await user.click(submitButton());

    const button = submitButton() as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.textContent).toBe("Submitting…");
  });

  it("sends who the ride is for, and only promises an account for non-facility bookings", async () => {
    const fetchMock = mockFetch({ ok: true, json: async () => ({ id: "abc" }) });
    const user = await fillForm();
    await user.click(screen.getByLabelText(/I work at a care facility/));
    await user.click(submitButton());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).booked_for).toBe("facility");
    const status = await screen.findByRole("status");
    expect(status.textContent).not.toMatch(/set up an account/);
  });

  it("mentions the account set-up email after an ordinary booking", async () => {
    mockFetch({ ok: true, json: async () => ({ id: "abc" }) });
    const user = await fillForm();
    await user.click(submitButton());
    expect((await screen.findByRole("status")).textContent).toMatch(/set up an account/);
  });

  it("never asks the API who is signed in unless this browser has signed in", async () => {
    const fetchMock = mockFetch({ ok: true, json: async () => ({ id: "abc" }) });
    render(<TripRequestForm />);
    await Promise.resolve();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows address errors on the matching part", async () => {
    mockFetch({ ok: false, status: 400, json: async () => ({ dropoff_zip: ["Enter a 5-digit ZIP code."] }) });
    const user = await fillForm();
    await user.click(submitButton());
    await screen.findByRole("alert");
    const zip = within(screen.getByRole("group", { name: /Drop-off address/ })).getByLabelText(/ZIP/);
    expect(zip.getAttribute("aria-invalid")).toBe("true");
    expect(zip.parentElement?.textContent).toContain("Enter a 5-digit ZIP code.");
  });

  it("books the ride back at a set time on the same day", async () => {
    const fetchMock = mockFetch({ ok: true, json: async () => ({ id: "abc" }) });
    const user = await fillForm();
    await user.click(screen.getByLabelText(/Yes, at a set time/));
    fireEvent.change(screen.getByLabelText(/Return pickup time/), { target: { value: "13:00" } });
    await user.click(submitButton());
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.return_ride).toBe("scheduled");
    expect(body.return_datetime).toBe("2026-11-02T19:00:00.000Z");
  });

  it("can ask the driver to wait", async () => {
    const fetchMock = mockFetch({ ok: true, json: async () => ({ id: "abc" }) });
    const user = await fillForm();
    // No prices on the website (owner, 2026-11-05).
    expect(screen.queryByText(/\$\d/)).toBeNull();
    await user.click(screen.getByLabelText(/the driver waits for me/));
    await user.click(submitButton());
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect([body.return_ride, body.return_datetime]).toEqual(["wait", null]);
  });

  it("doesn't ask about a ride back for equipment deliveries", async () => {
    const user = await fillForm();
    expect(screen.getByRole("group", { name: /ride back/ })).toBeTruthy();
    await user.selectOptions(screen.getByLabelText(/Service type/), "equipment_delivery");
    expect(screen.queryByRole("group", { name: /ride back/ })).toBeNull();
  });

  it("emails a code first when this email isn't confirmed yet, then books", async () => {
    sessionStorage.clear();
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body));
      if (url.endsWith("/verify/start/")) {
        expect(body).toMatchObject({ email: "jane@example.com", purpose: "booking", company_website: "" });
        return { ok: true, json: async () => ({ verification_id: "v1" }) };
      }
      if (url.endsWith("/verify/check/")) {
        expect(body).toEqual({ verification_id: "v1", code: "123456" });
        return { ok: true, json: async () => ({ token: "fresh-token", expires_at: "2099-01-01T00:00:00Z" }) };
      }
      return { ok: true, json: async () => ({ id: "abc" }) };
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = await fillForm();

    await user.click(submitButton());
    const panel = await screen.findByRole("region", { name: /Check your email/ });
    expect(panel.textContent).toContain("jane@example.com");
    expect((submitButton() as HTMLButtonElement).disabled).toBe(true);
    await user.type(screen.getByLabelText(/Code from the email/), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await screen.findByRole("status");
    const booking = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/trip-requests/"))!;
    expect(JSON.parse(String(booking[1].body)).verification_token).toBe("fresh-token");
  });

  it("shows why a code couldn't be sent", async () => {
    sessionStorage.clear();
    mockFetch({
      ok: false,
      status: 429,
      json: async () => ({ detail: "Too many codes for this address today. Please call us instead." }),
    });
    const user = await fillForm();
    await user.click(submitButton());
    expect((await screen.findByRole("alert")).textContent).toContain("Too many codes");
  });

  it("offers exactly the four confirmed services", () => {
    render(<TripRequestForm />);
    const values = Array.from(
      (screen.getByLabelText(/Service type/) as HTMLSelectElement).options,
    )
      .map((option) => option.value)
      .filter(Boolean);
    expect(values).toEqual(["ambulatory", "wheelchair", "stretcher", "equipment_delivery"]);
  });
});
