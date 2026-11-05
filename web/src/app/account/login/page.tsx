import type { Metadata } from "next";
import { Suspense } from "react";
import { LoginForm } from "@/components/account/LoginForm";

export const metadata: Metadata = {
  title: "Sign in",
  description: "Sign in to see and manage your AngelCare Transit rides.",
};

export default function Page() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Sign in</h1>
      <p className="mt-4 text-foreground/70">See your rides, book again and cancel online.</p>
      <div className="mt-10">
        <Suspense><LoginForm /></Suspense>
      </div>
    </div>
  );
}
