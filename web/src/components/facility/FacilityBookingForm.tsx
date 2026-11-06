"use client";

import Link from "next/link";
import { useId, useState, type FormEvent } from "react";
import { AddressFields } from "@/components/AddressFields";
import { Alert, inputClass, Loading, primaryButtonClass } from "@/components/account/ui";
import { readParts } from "@/lib/addresses";
import type { ReturnRide, ServiceType } from "@/lib/api";
import { facilityApi, FacilityApiError, type FacilityTrip } from "@/lib/facility";
import type { SavedAddress } from "@/lib/rider";
import { FacilityNav } from "./FacilityNav";
import { useFacilitySession } from "./useFacilitySession";

const SERVICES: { value: ServiceType; label: string }[] = [
  { value: "ambulatory", label: "Ambulatory" },
  { value: "wheelchair", label: "Wheelchair" },
  { value: "stretcher", label: "Stretcher" },
  { value: "equipment_delivery", label: "Medical Supply & Equipment Delivery" },
];

const RETURNS: { value: ReturnRide; label: string; hint?: string }[] = [
  { value: "none", label: "No, one way" },
  { value: "scheduled", label: "Yes, at a set time" },
  { value: "will_call", label: "Yes, the facility will call when they're ready" },
  { value: "wait", label: "Yes, the driver waits", hint: "The driver stays nearby and brings them back." },
];

function isoAt(date: string, time: string): string {
  return date && time ? new Date(`${date}T${time}`).toISOString() : "";
}

function Errors({ messages }: { messages?: string[] }) {
  return messages?.length ? <p className="mt-1 text-sm text-red-700">{messages.join(" ")}</p> : null;
}

/** A ride request from the facility (ADR 0042). Our office confirms it, like any booking. */
export function FacilityBookingForm() {
  const id = useId();
  const { profile, handleError, signOut } = useFacilitySession();
  const [service, setService] = useState("");
  const [returnRide, setReturnRide] = useState<ReturnRide>("none");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [booked, setBooked] = useState<FacilityTrip | null>(null);

  if (!profile) return <Loading />;

  const facility = profile.facility;
  // The facility's own address, offered for pickup and drop-off.
  const saved: SavedAddress[] = facility.street
    ? [{ id: "facility", label: facility.name, address: facility.address, street: facility.street,
        unit: facility.unit, city: facility.city, state: facility.state, zip: facility.zip }]
    : [];
  const offersReturn = service !== "equipment_delivery";

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const day = String(data.get("pickup_date") ?? "");
    const ride: ReturnRide = offersReturn ? returnRide : "none";
    const pickup = readParts(data, "pickup_");
    const dropoff = readParts(data, "dropoff_");
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      const trip = await facilityApi.book({
        full_name: String(data.get("full_name") ?? ""),
        phone: String(data.get("phone") ?? ""),
        pickup_street: pickup.street,
        pickup_unit: pickup.unit,
        pickup_city: pickup.city,
        pickup_state: pickup.state,
        pickup_zip: pickup.zip,
        dropoff_street: dropoff.street,
        dropoff_unit: dropoff.unit,
        dropoff_city: dropoff.city,
        dropoff_state: dropoff.state,
        dropoff_zip: dropoff.zip,
        requested_datetime: isoAt(day, String(data.get("pickup_time") ?? "")),
        return_ride: ride,
        return_datetime: ride === "scheduled" ? isoAt(day, String(data.get("return_time") ?? "")) || null : null,
        service_type: String(data.get("service_type") ?? "") as ServiceType,
        mobility_notes: String(data.get("mobility_notes") ?? ""),
        additional_notes: String(data.get("additional_notes") ?? ""),
      });
      setBooked(trip);
      form.reset();
      setService("");
      setReturnRide("none");
    } catch (err) {
      if (err instanceof FacilityApiError && !err.signedOut) {
        setFieldErrors(err.fieldErrors);
        setError(Object.keys(err.fieldErrors).length ? "Please check the highlighted fields." : err.message);
      } else {
        setError(handleError(err) || null);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-8">
      <FacilityNav facilityName={facility.name} onSignOut={signOut} />
      {booked && (
        <Alert kind="success">
          Ride request {booked.reference} for {booked.rider_name} sent. Our office will confirm it; you&apos;ll see
          the confirmed time and driver on the <Link href="/facility" className="underline">rides page</Link>.
          Book another below.
        </Alert>
      )}
      <form onSubmit={onSubmit} className="space-y-6">
        {error && <Alert kind="error">{error}</Alert>}
        <div className="grid gap-6 sm:grid-cols-2">
          <div>
            <label htmlFor={`${id}-name`} className="block text-sm font-medium">
              Patient or resident&apos;s full name <span aria-hidden="true">*</span>
            </label>
            <input id={`${id}-name`} name="full_name" required autoComplete="off" className={inputClass} />
            <Errors messages={fieldErrors.full_name} />
          </div>
          <div>
            <label htmlFor={`${id}-phone`} className="block text-sm font-medium">
              Phone for this ride <span aria-hidden="true">*</span>
            </label>
            <input
              id={`${id}-phone`}
              name="phone"
              type="tel"
              required
              defaultValue={facility.phone}
              autoComplete="off"
              className={inputClass}
            />
            <p className="mt-1 text-xs text-foreground/60">The number our driver or office should call.</p>
            <Errors messages={fieldErrors.phone} />
          </div>
        </div>

        <AddressFields id={`${id}-pickup`} legend="Pickup address" prefix="pickup_" section="pickup" required
          errors={fieldErrors} saved={saved} />
        <AddressFields id={`${id}-dropoff`} legend="Drop-off address" prefix="dropoff_" section="dropoff" required
          errors={fieldErrors} saved={saved} />

        <div className="grid gap-6 sm:grid-cols-2">
          <div>
            <label htmlFor={`${id}-date`} className="block text-sm font-medium">
              Pickup date <span aria-hidden="true">*</span>
            </label>
            <input id={`${id}-date`} name="pickup_date" type="date" required className={inputClass} />
            <Errors messages={fieldErrors.requested_datetime} />
          </div>
          <div>
            <label htmlFor={`${id}-time`} className="block text-sm font-medium">
              Pickup time <span aria-hidden="true">*</span>
            </label>
            <input id={`${id}-time`} name="pickup_time" type="time" required className={inputClass} />
          </div>
        </div>

        <div>
          <label htmlFor={`${id}-service`} className="block text-sm font-medium">
            Service type <span aria-hidden="true">*</span>
          </label>
          <select id={`${id}-service`} name="service_type" required value={service}
            onChange={(e) => setService(e.target.value)} className={inputClass}>
            <option value="" disabled>
              Select a service…
            </option>
            {SERVICES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
          <Errors messages={fieldErrors.service_type} />
        </div>

        {offersReturn && (
          <fieldset>
            <legend className="block text-sm font-medium">Ride back?</legend>
            <div className="mt-2 space-y-2">
              {RETURNS.map((option) => (
                <label key={option.value} className="flex items-start gap-2 text-sm">
                  <input type="radio" name="return_ride" value={option.value} checked={returnRide === option.value}
                    onChange={() => setReturnRide(option.value)} className="mt-0.5" />
                  <span>
                    {option.label}
                    {option.hint && <span className="block text-xs text-foreground/60">{option.hint}</span>}
                  </span>
                </label>
              ))}
            </div>
            {returnRide === "scheduled" && (
              <div className="mt-4 sm:w-1/2">
                <label htmlFor={`${id}-return`} className="block text-sm font-medium">
                  Return pickup time (same day) <span aria-hidden="true">*</span>
                </label>
                <input id={`${id}-return`} name="return_time" type="time" required className={inputClass} />
              </div>
            )}
            <Errors messages={fieldErrors.return_datetime ?? fieldErrors.return_ride} />
          </fieldset>
        )}

        <div>
          <label htmlFor={`${id}-mobility`} className="block text-sm font-medium">
            Mobility needs (optional)
          </label>
          <textarea id={`${id}-mobility`} name="mobility_notes" rows={3} className={inputClass}
            placeholder="e.g. power wheelchair, needs help transferring, oxygen" />
        </div>
        <div>
          <label htmlFor={`${id}-notes`} className="block text-sm font-medium">
            Notes for our office (optional)
          </label>
          <textarea id={`${id}-notes`} name="additional_notes" rows={3} className={inputClass}
            placeholder="e.g. discharge from room 412, Medicaid ID to follow" />
        </div>

        <p className="text-xs text-foreground/60">
          This sends a ride request; our office confirms it and assigns the driver. Not for emergencies: call 911.
        </p>
        <button type="submit" disabled={busy} className={primaryButtonClass}>
          {busy ? "Sending…" : "Send ride request"}
        </button>
      </form>
    </div>
  );
}
