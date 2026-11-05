/**
 * Email codes for the website forms (ADR 0033). The API emails a 6-digit
 * code; entering it earns a token the booking and contact forms send
 * along. The token covers that email for a couple of hours, so it's kept
 * for this browser tab (sessionStorage) and reused, e.g. after fixing a
 * form error or booking several rides.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ?? "http://localhost:8000";

export type VerifyPurpose = "booking" | "contact";

/** Name of the hidden trap field bots fill in (must match the API). */
export const TRAP_FIELD = "company_website";

export class VerifyError extends Error {
  fieldErrors: Record<string, string[]>;

  constructor(message: string, fieldErrors: Record<string, string[]> = {}) {
    super(message);
    this.name = "VerifyError";
    this.fieldErrors = fieldErrors;
  }
}

async function post<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/verify/${path}/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new VerifyError("Couldn't reach us. Check your connection and try again.");
  }
  let data: Record<string, unknown> = {};
  try {
    data = await response.json();
  } catch {
    // no JSON body
  }
  if (!response.ok) {
    const fieldErrors: Record<string, string[]> = {};
    for (const [key, value] of Object.entries(data)) {
      if (Array.isArray(value)) fieldErrors[key] = value.map(String);
    }
    const detail = typeof data.detail === "string" ? data.detail : Object.values(fieldErrors).flat()[0];
    throw new VerifyError(detail ?? "Something went wrong. Please try again or call us.", fieldErrors);
  }
  return data as T;
}

export function startVerification(input: {
  email: string;
  purpose: VerifyPurpose;
  elapsedMs: number;
  turnstileToken: string;
  trap: string;
}): Promise<{ verification_id: string }> {
  return post("start", {
    email: input.email,
    purpose: input.purpose,
    elapsed_ms: Math.round(input.elapsedMs),
    turnstile_token: input.turnstileToken,
    [TRAP_FIELD]: input.trap,
  });
}

export function checkCode(verificationId: string, code: string): Promise<{ token: string; expires_at: string }> {
  return post("check", { verification_id: verificationId, code });
}

const STORE_KEY = "angelcare-email-tokens";

function readStore(): Record<string, { token: string; expiresAt: string }> {
  try {
    return JSON.parse(window.sessionStorage.getItem(STORE_KEY) ?? "{}");
  } catch {
    return {};
  }
}

/** A still-valid token for this email from earlier in this tab, if any. */
export function savedToken(email: string): string | null {
  const entry = readStore()[email.trim().toLowerCase()];
  // A minute's margin so it doesn't expire between here and the API.
  if (!entry || new Date(entry.expiresAt).getTime() - 60_000 < Date.now()) return null;
  return entry.token;
}

export function saveToken(email: string, token: string, expiresAt: string) {
  try {
    const store = readStore();
    store[email.trim().toLowerCase()] = { token, expiresAt };
    window.sessionStorage.setItem(STORE_KEY, JSON.stringify(store));
  } catch {
    // storage unavailable: they'll be asked for a code again next time
  }
}

export function forgetToken(email: string) {
  try {
    const store = readStore();
    delete store[email.trim().toLowerCase()];
    window.sessionStorage.setItem(STORE_KEY, JSON.stringify(store));
  } catch {
    // nothing to forget
  }
}
