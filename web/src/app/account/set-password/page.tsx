import type { Metadata } from "next";
import { Suspense } from "react";
import { SetPasswordForm } from "@/components/account/PasswordForms";

export const metadata: Metadata = {
  title: "Set up your account",
  description: "Choose a password for your AngelCare Transit account.",
};

export default function Page() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Set up your account</h1>
      <p className="mt-4 text-foreground/70">Choose a password to finish setting up your account. You&apos;ll use it with your email to sign in.</p>
      <div className="mt-10">
        <Suspense><SetPasswordForm /></Suspense>
      </div>
    </div>
  );
}
