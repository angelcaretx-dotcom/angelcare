/**
 * Thin client for the AngelCare Transit backend API (api/, Django).
 * Base URL is configured per-environment via NEXT_PUBLIC_API_URL so the
 * frontend never hard-codes a backend host.
 */

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ??
  "http://localhost:8000";

export type ServiceType = "ambulatory" | "wheelchair" | "stretcher";

export interface TripRequestInput {
  full_name: string;
  phone: string;
  email: string;
  pickup_address: string;
  dropoff_address: string;
  requested_datetime: string; // ISO 8601
  service_type: ServiceType;
  mobility_notes?: string;
  additional_notes?: string;
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
