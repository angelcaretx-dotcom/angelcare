"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useId, useState, type FormEvent } from "react";
import { riderApi, RiderApiError } from "@/lib/rider";
import { Alert, inputClass, primaryButtonClass } from "./ui";

/** Only ever return to a page on this site. */
function safeNext(value: string | null): string {
  return value && value.startsWith("/account") && !value.startsWith("//") ? value : "/account";
}

export function LoginForm() {
  const id = useId();
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      await riderApi.login(String(data.get("email") ?? ""), String(data.get("password") ?? ""));
      router.replace(safeNext(params.get("next")));
    } catch (err) {
      setError(err instanceof RiderApiError ? err.message : "Couldn't reach us. Please try again.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-5">
      {params.get("expired") && (
        <Alert kind="info">For your privacy you were signed out after a few minutes without activity.</Alert>
      )}
      {error && <Alert kind="error">{error}</Alert>}
      <div>
        <label htmlFor={`${id}-email`} className="block text-sm font-medium">
          Email address
        </label>
        <input id={`${id}-email`} name="email" type="email" autoComplete="email" required className={inputClass} />
      </div>
      <div>
        <label htmlFor={`${id}-password`} className="block text-sm font-medium">
          Password
        </label>
        <input
          id={`${id}-password`}
          name="password"
          type="password"
          autoComplete="current-password"
          required
          className={inputClass}
        />
      </div>
      <button type="submit" disabled={busy} className={primaryButtonClass}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
      <p className="text-sm text-foreground/70">
        <Link href="/account/forgot-password" className="text-brand-blue-dark underline">
          Forgot your password, or never set one?
        </Link>
      </p>
      <p className="text-sm text-foreground/70">
        No account yet? You don&apos;t need one to book. After your first{" "}
        <Link href="/request" className="text-brand-blue-dark underline">
          ride request
        </Link>{" "}
        we email you a link to set one up.
      </p>
    </form>
  );
}
