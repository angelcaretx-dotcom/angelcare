import type { Metadata } from "next";
import { FacilityForgotPasswordForm } from "@/components/facility/PasswordForms";

export const metadata: Metadata = {
  title: "Forgot portal password",
  description: "Get a link to choose a new facility portal password.",
};

export default function Page() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Forgot your password?</h1>
      <p className="mt-4 text-foreground/70">Enter your work email and we&apos;ll send you a link to choose a password.</p>
      <div className="mt-10">
        <FacilityForgotPasswordForm />
      </div>
    </div>
  );
}
