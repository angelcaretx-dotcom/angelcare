"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { riderApi, type RiderTrip } from "@/lib/rider";
import { AccountNav, Alert, Loading, secondaryButtonClass } from "./ui";
import { useRiderSession } from "./useRiderSession";

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

const STATUS_STYLE: Record<RiderTrip["status"], string> = {
  received: "bg-sky-50 text-sky-900",
  contacted: "bg-sky-50 text-sky-900",
  confirmed: "bg-green-50 text-green-900",
  in_progress: "bg-amber-50 text-amber-900",
  completed: "bg-black/5 text-foreground/70",
  missed: "bg-red-50 text-red-900",
  declined: "bg-red-50 text-red-900",
  cancelled: "bg-black/5 text-foreground/70",
};

// Still to happen (or happening now) vs. finished one way or another.
const ACTIVE = new Set<RiderTrip["status"]>(["received", "contacted", "confirmed", "in_progress"]);

export function MyTrips() {
  const { profile, handleError, signOut } = useRiderSession();
  const [trips, setTrips] = useState<RiderTrip[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!profile) return;
    riderApi
      .trips()
      .then(setTrips)
      .catch((err) => setError(handleError(err) || null));
  }, [profile, handleError]);

  async function cancel(trip: RiderTrip) {
    setBusy(trip.id);
    setError(null);
    try {
      const updated = await riderApi.cancelTrip(trip.id);
      setTrips((current) => current?.map((t) => (t.id === trip.id ? updated : t)) ?? null);
      setConfirming(null);
    } catch (err) {
      setError(handleError(err) || null);
    } finally {
      setBusy(null);
    }
  }

  if (!profile) return <Loading />;

  const upcoming = trips?.filter((t) => ACTIVE.has(t.status)) ?? [];
  const past = trips?.filter((t) => !ACTIVE.has(t.status)) ?? [];

  return (
    <div className="space-y-8">
      <AccountNav onSignOut={signOut} />
      {error && <Alert kind="error">{error}</Alert>}
      {trips === null && !error && <Loading />}
      {trips !== null && trips.length === 0 && (
        <p className="text-foreground/70">
          No rides yet. <Link href="/request" className="text-brand-blue-dark underline">Book a ride</Link>.
        </p>
      )}
      {[
        ["Upcoming", upcoming],
        ["Past", past],
      ].map(([title, list]) =>
        (list as RiderTrip[]).length === 0 ? null : (
          <section key={title as string} aria-labelledby={`trips-${title}`}>
            <h2 id={`trips-${title}`} className="text-lg font-semibold">
              {title as string}
            </h2>
            <ul className="mt-3 space-y-4">
              {(list as RiderTrip[]).map((trip) => (
                <li key={trip.id} className="rounded-2xl border border-black/10 p-5" data-trip={trip.id}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="text-lg font-semibold">
                        {when(trip.confirmed_pickup ?? trip.requested_pickup)}
                      </p>
                      <p className="text-sm text-foreground/60">
                        {trip.confirmed_pickup ? "Confirmed pickup" : "Requested pickup"} · {trip.service_type_text} ·
                        Ref {trip.reference}
                      </p>
                    </div>
                    <span className={`rounded-full px-3 py-1 text-sm font-medium ${STATUS_STYLE[trip.status]}`}>
                      {trip.status_text}
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
                    {trip.driver_first_name && (
                      <div>
                        <dt className="text-foreground/60">Your driver</dt>
                        <dd>
                          {trip.driver_first_name}
                          {trip.vehicle ? ` · ${trip.vehicle}` : ""}
                        </dd>
                      </div>
                    )}
                  </dl>
                  <div className="mt-4 flex flex-wrap items-center gap-3">
                    <Link href={`/request?again=${trip.id}`} className={secondaryButtonClass}>
                      Book again
                    </Link>
                    {trip.can_cancel &&
                      (confirming === trip.id ? (
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
                      ))}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ),
      )}
      <p className="text-sm text-foreground/60">
        To change a ride, cancel it and book again, or call us. You&apos;re signed out automatically after{" "}
        {Math.round(profile.idle_timeout_seconds / 60)} minutes without activity.
      </p>
    </div>
  );
}
