import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContactForm } from "./ContactForm";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

async function fillIn() {
  const user = userEvent.setup();
  render(<ContactForm />);
  await user.type(screen.getByLabelText(/Your name/), "Pat Rivera");
  await user.type(screen.getByLabelText(/Your email address/), "pat@example.com");
  await user.type(screen.getByLabelText(/Message/), "Do you cover Dallas?");
  return user;
}

describe("ContactForm", () => {
  it("confirms the email with a code, then sends the message", async () => {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url.endsWith("/verify/start/")) return { ok: true, json: async () => ({ verification_id: "v1" }) };
      if (url.endsWith("/verify/check/")) {
        return { ok: true, json: async () => ({ token: "tok", expires_at: "2099-01-01T00:00:00Z" }) };
      }
      return { ok: true, json: async () => ({ id: "m1" }) };
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = await fillIn();

    await user.click(screen.getByRole("button", { name: "Send message" }));
    await user.type(await screen.findByLabelText(/Code from the email/), "654321");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    expect((await screen.findByRole("status")).textContent).toContain("Message sent.");
    const sent = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/contact/"))!;
    expect(JSON.parse(String(sent[1]!.body))).toEqual({
      name: "Pat Rivera",
      email: "pat@example.com",
      phone: "",
      message: "Do you cover Dallas?",
      verification_token: "tok",
      company_website: "",
    });
  });

  it("asks for the required fields before sending any code", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<ContactForm />);
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("lets the person back out and change the email", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => ({ verification_id: "v1" }) })),
    );
    const user = await fillIn();
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await user.click(await screen.findByRole("button", { name: /Change the email address/ }));
    expect(screen.queryByLabelText(/Code from the email/)).toBeNull();
    expect((screen.getByRole("button", { name: "Send message" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
