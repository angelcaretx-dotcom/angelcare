import type { Metadata } from "next";
import { Suspense } from "react";
import { FacilityLoginForm } from "@/components/facility/FacilityLoginForm";

export const metadata: Metadata = {
  title: "Facility portal sign-in",
  description: "Sign in to book and track your facility's rides.",
};

export default function Page() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Facility portal</h1>
      <p className="mt-4 text-foreground/70">Book rides for your patients and residents, and see every ride for your facility.</p>
      <div className="mt-10">
        <Suspense><FacilityLoginForm /></Suspense>
      </div>
    </div>
  );
}
