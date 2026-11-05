/**
 * Client for the rider account API (ADR 0031), called same-origin through
 * this site's /api/ proxy so the session cookie is first-party. Django
 * checks CSRF on every write: fetch the token cookie once, send it back
 * as X-CSRFToken.
 */

import type { AddressParts } from "./addresses";

/** `address` is the one-line form; the parts are blank for addresses saved before ADR 0032. */
export interface SavedAddress extends AddressParts {
  id: string;
  label: string;
  address: string;
}

export interface RiderProfile {
  email: string;
  full_name: string;
  phone: string;
  mobility_notes: string;
  addresses: SavedAddress[];
  idle_timeout_seconds: number;
}

export interface RiderTrip {
  id: string;
  reference: string;
  status: "received" | "contacted" | "confirmed" | "in_progress" | "completed" | "missed" | "declined" | "cancelled";
  status_text: string;
  service_type: string;
  service_type_text: string;
  requested_pickup: string;
  confirmed_pickup: string | null;
  pickup_address: string;
  dropoff_address: string;
  /** The parts as booked, for "Book again"; null for requests made before ADR 0032. */
  pickup: AddressParts | null;
  dropoff: AddressParts | null;
  return_ride: { kind: ReturnRide; text: string; pickup: string | null } | null;
  mobility_notes: string;
  driver_first_name: string | null;
  vehicle: string | null;
  can_cancel: boolean;
}

export type ReturnRide = "none" | "scheduled" | "will_call" | "wait";

export class RiderApiError extends Error {
  status: number;
  fieldErrors: Record<string, string[]>;

  constructor(status: number, body: Record<string, unknown> | null) {
    const detail = typeof body?.detail === "string" ? body.detail : null;
    super(detail ?? "Something went wrong. Please try again or call us.");
    this.name = "RiderApiError";
    this.status = status;
    this.fieldErrors = {};
    for (const [key, value] of Object.entries(body ?? {})) {
      if (Array.isArray(value)) this.fieldErrors[key] = value.map(String);
    }
  }

  /** Not signed in, or signed out after being idle. */
  get signedOut(): boolean {
    return this.status === 401 || this.status === 403;
  }
}

const BASE = "/api/v1/rider";

// A hint only (the session cookie is httpOnly): lets the public booking
// form skip asking the API "who am I?" for visitors who never signed in.
const HINT_KEY = "angelcare-rider";

export function maybeSignedIn(): boolean {
  try {
    return window.localStorage.getItem(HINT_KEY) === "1";
  } catch {
    return false;
  }
}

function setHint(signedIn: boolean) {
  try {
    if (signedIn) window.localStorage.setItem(HINT_KEY, "1");
    else window.localStorage.removeItem(HINT_KEY);
  } catch {
    // storage unavailable: the hint is optional
  }
}

function readCookie(name: string): string | null {
  const match = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

async function csrfToken(): Promise<string> {
  let token = readCookie("csrftoken");
  if (!token) {
    await fetch(`${BASE}/csrf`, { credentials: "same-origin" });
    token = readCookie("csrftoken");
  }
  return token ?? "";
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-CSRFToken"] = await csrfToken();
  }
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers,
    credentials: "same-origin",
    cache: "no-store",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data: Record<string, unknown> | null = null;
  try {
    data = await response.json();
  } catch {
    // no JSON body
  }
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) setHint(false);
    throw new RiderApiError(response.status, data);
  }
  return data as T;
}

function signedIn(profile: RiderProfile): RiderProfile {
  setHint(true);
  return profile;
}

export const riderApi = {
  login: (email: string, password: string) =>
    call<RiderProfile>("POST", "/login", { email, password }).then(signedIn),
  logout: () => {
    setHint(false);
    return call<{ ok: true }>("POST", "/logout");
  },
  forgotPassword: (email: string) => call<{ ok: true }>("POST", "/forgot-password", { email }),
  setPassword: (uid: string, token: string, password: string) =>
    call<RiderProfile>("POST", "/set-password", { uid, token, password }).then(signedIn),
  me: () => call<RiderProfile>("GET", "/me"),
  updateMe: (changes: Partial<Pick<RiderProfile, "full_name" | "phone" | "mobility_notes">>) =>
    call<RiderProfile>("PATCH", "/me", changes),
  addAddress: (label: string, parts: AddressParts) => call<RiderProfile>("POST", "/addresses", { label, ...parts }),
  removeAddress: (id: string) => call<RiderProfile>("DELETE", `/addresses/${id}`),
  trips: () => call<{ trips: RiderTrip[] }>("GET", "/trips").then((r) => r.trips),
  cancelTrip: (id: string) => call<RiderTrip>("POST", `/trips/${id}/cancel`),
};
