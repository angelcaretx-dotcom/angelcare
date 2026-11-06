/**
 * Client for the facility portal API (ADR 0042), called same-origin through
 * this site's /api/ proxy like the rider pages (ADR 0031). Django checks
 * CSRF on every write, both sign-in steps included.
 */

import type { AddressParts } from "./addresses";
import type { ReturnRide, ServiceType } from "./api";
import { RiderApiError, type RiderTrip } from "./rider";

export interface FacilityProfile {
  email: string;
  full_name: string;
  facility: { name: string; address: string; phone: string } & AddressParts;
  idle_timeout_seconds: number;
}

/** A ride as the portal shows it: the rider's own fields plus who it's for and who booked it. */
export interface FacilityTrip extends RiderTrip {
  rider_name: string;
  booked_by: string;
}

type Side<S extends string> = { [P in keyof AddressParts as `${S}_${P}`]: string };

export interface FacilityBooking extends Side<"pickup">, Side<"dropoff"> {
  full_name: string;
  phone: string;
  requested_datetime: string;
  return_ride: ReturnRide;
  return_datetime: string | null;
  service_type: ServiceType;
  mobility_notes: string;
  additional_notes: string;
}

/** Same error shape as the rider API: `signedOut` means sign in again. */
export { RiderApiError as FacilityApiError };

const BASE = "/api/v1/facility";

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
  if (!response.ok) throw new RiderApiError(response.status, data);
  return data as T;
}

export const facilityApi = {
  /** Step one: a right password emails a 6-digit code. */
  login: (email: string, password: string) => call<{ code_sent: true }>("POST", "/login", { email, password }),
  /** Step two: the code signs in. */
  loginCode: (code: string) => call<FacilityProfile>("POST", "/login/code", { code }),
  logout: () => call<{ ok: true }>("POST", "/logout"),
  forgotPassword: (email: string) => call<{ ok: true }>("POST", "/forgot-password", { email }),
  setPassword: (uid: string, token: string, password: string) =>
    call<FacilityProfile>("POST", "/set-password", { uid, token, password }),
  me: () => call<FacilityProfile>("GET", "/me"),
  trips: () => call<{ trips: FacilityTrip[] }>("GET", "/trips").then((r) => r.trips),
  book: (booking: FacilityBooking) => call<FacilityTrip>("POST", "/book", booking),
  cancelTrip: (id: string) => call<FacilityTrip>("POST", `/trips/${id}/cancel`),
};
