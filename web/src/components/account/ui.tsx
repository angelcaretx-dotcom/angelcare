import Link from "next/link";
import { type ReactNode } from "react";

export const inputClass =
  "mt-1 w-full rounded-lg border border-black/15 px-3 py-2 text-sm focus:border-brand-teal-dark focus:outline-none";
export const primaryButtonClass =
  "rounded-full bg-brand-blue-dark px-6 py-3 text-sm font-semibold text-white transition-colors hover:brightness-90 disabled:cursor-not-allowed disabled:opacity-60";
export const secondaryButtonClass =
  "rounded-full border border-brand-blue-dark px-5 py-2 text-sm font-semibold text-brand-blue-dark transition-colors hover:bg-brand-blue-dark/5 disabled:opacity-60";

export function Alert({ kind, children }: { kind: "error" | "info" | "success"; children: ReactNode }) {
  const styles = {
    error: "border-red-600/30 bg-red-50 text-red-900",
    info: "border-amber-600/30 bg-amber-50 text-amber-900",
    success: "border-green-600/30 bg-green-50 text-green-900",
  }[kind];
  return (
    <div role={kind === "error" ? "alert" : "status"} className={`rounded-lg border p-4 text-sm ${styles}`}>
      {children}
    </div>
  );
}

export function AccountNav({ onSignOut }: { onSignOut: () => void }) {
  return (
    <nav aria-label="Account" className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm font-medium">
      <Link href="/account" className="text-brand-blue-dark hover:underline">
        My trips
      </Link>
      <Link href="/account/profile" className="text-brand-blue-dark hover:underline">
        Profile &amp; addresses
      </Link>
      <Link href="/request" className="text-brand-blue-dark hover:underline">
        Book a ride
      </Link>
      <button type="button" onClick={onSignOut} className="text-foreground/70 hover:underline">
        Sign out
      </button>
    </nav>
  );
}

export function Loading() {
  return <p className="text-sm text-foreground/60">Loading…</p>;
}
