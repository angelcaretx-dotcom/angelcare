import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
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
  await user.type(screen.getByLabelText(/Pickup address/), "1 Main St");
  await user.type(screen.getByLabelText(/Drop-off address/), "2 Clinic Rd");
  // user-event can't type into date/time inputs in jsdom; set them directly.
  fireEvent.change(screen.getByLabelText(/Pickup date/), { target: { value: "2026-11-02" } });
  fireEvent.change(screen.getByLabelText(/Pickup time/), { target: { value: "09:30" } });
  await user.selectOptions(screen.getByLabelText(/Service type/), "wheelchair");
  await user.type(screen.getByLabelText(/Mobility requirements/), "Manual wheelchair");
  return user;
}

const submitButton = () => screen.getByRole("button", { name: /Submit/ });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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
      pickup_address: "1 Main St",
      dropoff_address: "2 Clinic Rd",
      requested_datetime: EXPECTED_UTC,
      service_type: "wheelchair",
      mobility_notes: "Manual wheelchair",
      additional_notes: "",
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
