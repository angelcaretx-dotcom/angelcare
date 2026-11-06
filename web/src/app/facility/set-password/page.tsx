import type { Metadata } from "next";
import { Suspense } from "react";
import { FacilitySetPasswordForm } from "@/components/facility/PasswordForms";

export const metadata: Metadata = {
  title: "Set up your portal account",
  description: "Choose a password for your facility portal account.",
};

export default function Page() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Set up your portal account</h1>
      <p className="mt-4 text-foreground/70">Choose a password. Each time you sign in we&apos;ll also email you a 6-digit code.</p>
      <div className="mt-10">
        <Suspense><FacilitySetPasswordForm /></Suspense>
      </div>
    </div>
  );
}
