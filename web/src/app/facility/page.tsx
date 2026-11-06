import type { Metadata } from "next";
import { FacilityRides } from "@/components/facility/FacilityRides";

export const metadata: Metadata = {
  title: "Facility rides",
  description: "Your facility's AngelCare Transit rides.",
};

export default function Page() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Facility rides</h1>
      <p className="mt-4 text-foreground/70">Every ride for your facility: upcoming soonest first, then past rides.</p>
      <div className="mt-10">
        <FacilityRides />
      </div>
    </div>
  );
}
