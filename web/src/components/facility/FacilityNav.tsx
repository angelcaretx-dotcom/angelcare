import Link from "next/link";

export function FacilityNav({ facilityName, onSignOut }: { facilityName: string; onSignOut: () => void }) {
  return (
    <nav aria-label="Facility portal" className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm font-medium">
      <span className="text-foreground/60">{facilityName}</span>
      <Link href="/facility" className="text-brand-blue-dark hover:underline">
        Rides
      </Link>
      <Link href="/facility/book" className="text-brand-blue-dark hover:underline">
        Book a ride
      </Link>
      <button type="button" onClick={onSignOut} className="text-foreground/70 hover:underline">
        Sign out
      </button>
    </nav>
  );
}
