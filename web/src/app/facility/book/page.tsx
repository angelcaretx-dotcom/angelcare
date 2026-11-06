import type { Metadata } from "next";
import { FacilityBookingForm } from "@/components/facility/FacilityBookingForm";

export const metadata: Metadata = {
  title: "Book a ride",
  description: "Request a ride for a patient or resident.",
};

export default function Page() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Book a ride</h1>
      <p className="mt-4 text-foreground/70">Request a ride for a patient or resident. Our office confirms it and assigns the driver.</p>
      <div className="mt-10">
        <FacilityBookingForm />
      </div>
    </div>
  );
}
