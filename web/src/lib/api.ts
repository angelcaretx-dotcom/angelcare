/**
 * Thin client for the AngelCare Transit backend API (api/, Django).
 * Base URL is configured per-environment via NEXT_PUBLIC_API_URL so the
 * frontend never hard-codes a backend host.
 */

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ??
  "http://localhost:8000";

export type ServiceType =
  | "ambulatory"
  | "wheelchair"
  | "equipment_delivery";

/** Who made the booking (ADR 0031); facility bookings get no rider account. */
export type BookedFor = "self" | "caregiver" | "facility";

export type ReturnRide = "none" | "scheduled" | "will_call" | "wait";

/** Addresses go as parts (ADR 0032): pickup_street, pickup_unit, ... dropoff_zip. */
type AddressPartsInput<Side extends string> = {
  [Part in "street" | "unit" | "city" | "state" | "zip" as `${Side}_${Part}`]: string;
};

export interface TripRequestInput extends AddressPartsInput<"pickup">, AddressPartsInput<"dropoff"> {
  full_name: string;
  phone: string;
  email: string;
  requested_datetime: string; // ISO 8601
  return_ride?: ReturnRide;
  return_datetime?: string | null; // ISO 8601, "at a set time" only
  service_type: ServiceType;
  mobility_notes?: string;
  additional_notes?: string;
  booked_for?: BookedFor;
  /** From the emailed code or a signed-in rider account (ADR 0033). */
  verification_token: string;
}

export class ApiError extends Error {
  status: number;
  fieldErrors?: Record<string, string[]>;

  constructor(
    message: string,
    status: number,
    fieldErrors?: Record<string, string[]>,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

export async function submitTripRequest(
  input: TripRequestInput,
): Promise<{ id: string }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/trip-requests/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });

  if (!response.ok) {
    let fieldErrors: Record<string, string[]> | undefined;
    try {
      fieldErrors = await response.json();
    } catch {
      // response had no JSON body — leave fieldErrors undefined
    }
    throw new ApiError(
      "We couldn't submit your request. Please check the form and try again.",
      response.status,
      fieldErrors,
    );
  }

  return response.json();
}

export interface ContactMessageInput {
  name: string;
  email: string;
  phone: string;
  message: string;
  verification_token: string;
  company_website: string;
}

/** The contact form (ADR 0033); replaces the published email address. */
export async function submitContactMessage(input: ContactMessageInput): Promise<{ id: string }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/contact/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    let fieldErrors: Record<string, string[]> | undefined;
    try {
      fieldErrors = await response.json();
    } catch {
      // no JSON body
    }
    throw new ApiError(
      "We couldn't send your message. Please check the form and try again.",
      response.status,
      fieldErrors,
    );
  }
  return response.json();
}
