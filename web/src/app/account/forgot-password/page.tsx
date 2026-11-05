import type { Metadata } from "next";
import { ForgotPasswordForm } from "@/components/account/PasswordForms";

export const metadata: Metadata = {
  title: "Forgot password",
  description: "Get a link to choose a new password for your AngelCare Transit account.",
};

export default function Page() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Forgot your password?</h1>
      <p className="mt-4 text-foreground/70">Enter the email you booked with and we&apos;ll send you a link to choose a password.</p>
      <div className="mt-10">
        <ForgotPasswordForm />
      </div>
    </div>
  );
}
