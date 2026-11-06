import type { Metadata } from "next";
import { Suspense } from "react";
import { FacilitySetPasswordForm } from "@/components/facility/PasswordForms";

export const metadata: Metadata = {
  title: "Reset portal password",
  description: "Choose a new password for your facility portal account.",
};

export default function Page() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Choose a new password</h1>
      <p className="mt-4 text-foreground/70">Choose a new password for your facility portal account.</p>
      <div className="mt-10">
        <Suspense><FacilitySetPasswordForm /></Suspense>
      </div>
    </div>
  );
}
