"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Alert, Loading, primaryButtonClass } from "@/components/account/ui";
import { facilityApi, type FacilityTrip } from "@/lib/facility";
import { FacilityNav } from "./FacilityNav";
import { useFacilitySession } from "./useFacilitySession";

const TIME_ZONE = "America/Chicago";

function when(iso: string): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: TIME_ZONE,
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(iso));
}

const STATUS_STYLE: Record<FacilityTrip["status"], string> = {
  received: "bg-sky-50 text-sky-900",
  contacted: "bg-sky-50 text-sky-900",
  confirmed: "bg-green-50 text-green-900",
  in_progress: "bg-amber-50 text-amber-900",
  completed: "bg-black/5 text-foreground/70",
  missed: "bg-red-50 text-red-900",
  declined: "bg-red-50 text-red-900",
  cancelled: "bg-black/5 text-foreground/70",
};

const ACTIVE = new Set<FacilityTrip["status"]>(["received", "contacted", "confirmed", "in_progress"]);

// Office wording is for riders; staff at a facility get the same facts, said for them.
function statusText(trip: FacilityTrip): string {
  if (trip.status === "received") return "Received — we'll confirm";
  if (trip.status === "contacted") return "Awaiting confirmation";
  return trip.status_text;
}

/** Every ride for the facility (ADR 0042): booked here by anyone at the facility, or by our office. */
export function FacilityRides() {
  const { profile, handleError, signOut } = useFacilitySession();
  const [trips, setTrips] = useState<FacilityTrip[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!profile) return;
    facilityApi
      .trips()
      .then(setTrips)
      .catch((err) => setError(handleError(err) || null));
  }, [profile, handleError]);

  async function cancel(trip: FacilityTrip) {
    setBusy(trip.id);
    setError(null);
    try {
      const updated = await facilityApi.cancelTrip(trip.id);
      setTrips((current) => current?.map((t) => (t.id === trip.id ? updated : t)) ?? null);
      setConfirming(null);
    } catch (err) {
      setError(handleError(err) || null);
    } finally {
      setBusy(null);
    }
  }

  if (!profile) return <Loading />;

  // Soonest first for what's coming; newest first for what's done.
  const pickup = (t: FacilityTrip) => t.confirmed_pickup ?? t.requested_pickup;
  const upcoming = (trips ?? []).filter((t) => ACTIVE.has(t.status)).sort((a, b) => pickup(a).localeCompare(pickup(b)));
  const past = (trips ?? []).filter((t) => !ACTIVE.has(t.status));

  return (
    <div className="space-y-8">
      <FacilityNav facilityName={profile.facility.name} onSignOut={signOut} />
      <Link href="/facility/book" className={`${primaryButtonClass} inline-block`}>
        Book a ride
      </Link>
      {error && <Alert kind="error">{error}</Alert>}
      {trips === null && !error && <Loading />}
      {trips !== null && trips.length === 0 && <p className="text-foreground/70">No rides for your facility yet.</p>}
      {(
        [
          ["Upcoming", upcoming],
          ["Past", past],
        ] as const
      ).map(([title, list]) =>
        list.length === 0 ? null : (
          <section key={title} aria-labelledby={`rides-${title}`}>
            <h2 id={`rides-${title}`} className="text-lg font-semibold">
              {title}
            </h2>
            <ul className="mt-3 space-y-4">
              {list.map((trip) => (
                <li key={trip.id} className="rounded-2xl border border-black/10 p-5" data-trip={trip.id}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="text-lg font-semibold">{trip.rider_name || "Rider"}</p>
                      <p className="text-sm">
                        {when(pickup(trip))} · {trip.confirmed_pickup ? "confirmed pickup" : "requested pickup"}
                      </p>
                      <p className="text-sm text-foreground/60">
                        {trip.service_type_text} · Ref {trip.reference}
                        {trip.booked_by ? ` · booked by ${trip.booked_by}` : ""}
                      </p>
                    </div>
                    <span className={`rounded-full px-3 py-1 text-sm font-medium ${STATUS_STYLE[trip.status]}`}>
                      {statusText(trip)}
                    </span>
                  </div>
                  <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
                    <div>
                      <dt className="text-foreground/60">From</dt>
                      <dd>{trip.pickup_address}</dd>
                    </div>
                    <div>
                      <dt className="text-foreground/60">To</dt>
                      <dd>{trip.dropoff_address}</dd>
                    </div>
                    {trip.return_ride && (
                      <div>
                        <dt className="text-foreground/60">Ride back</dt>
                        <dd>
                          {trip.return_ride.text}
                          {trip.return_ride.pickup ? ` · ${when(trip.return_ride.pickup)}` : ""}
                        </dd>
                      </div>
                    )}
                    {trip.driver_first_name && (
                      <div>
                        <dt className="text-foreground/60">Driver</dt>
                        <dd>
                          {trip.driver_first_name}
                          {trip.vehicle ? ` · ${trip.vehicle}` : ""}
                        </dd>
                      </div>
                    )}
                  </dl>
                  {trip.can_cancel && (
                    <div className="mt-4 flex flex-wrap items-center gap-3">
                      {confirming === trip.id ? (
                        <>
                          <span className="text-sm">Cancel this ride?</span>
                          <button
                            type="button"
                            disabled={busy === trip.id}
                            onClick={() => cancel(trip)}
                            className="rounded-full bg-red-700 px-5 py-2 text-sm font-semibold text-white disabled:opacity-60"
                          >
                            {busy === trip.id ? "Cancelling…" : "Yes, cancel it"}
                          </button>
                          <button type="button" onClick={() => setConfirming(null)} className="text-sm underline">
                            Keep it
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setConfirming(trip.id)}
                          className="text-sm font-medium text-red-700 underline"
                        >
                          Cancel ride
                        </button>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </section>
        ),
      )}
      <p className="text-sm text-foreground/60">
        Everyone with a portal account at {profile.facility.name} sees these rides. To change one, cancel it and
        book again, or call us; rides our office booked are changed by phone. You&apos;re signed out after{" "}
        {Math.round(profile.idle_timeout_seconds / 60)} minutes without activity.
      </p>
    </div>
  );
}
