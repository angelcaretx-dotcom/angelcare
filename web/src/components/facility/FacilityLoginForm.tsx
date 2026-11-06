"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useId, useState, type FormEvent } from "react";
import { Alert, inputClass, primaryButtonClass, secondaryButtonClass } from "@/components/account/ui";
import { facilityApi, FacilityApiError } from "@/lib/facility";

/** Only ever return to a portal page on this site. */
function safeNext(value: string | null): string {
  return value && value.startsWith("/facility") && !value.startsWith("//") ? value : "/facility";
}

/** Two steps (ADR 0042): the password, then the 6-digit code we email. */
export function FacilityLoginForm() {
  const id = useId();
  const router = useRouter();
  const params = useSearchParams();
  const [step, setStep] = useState<"password" | "code">("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function fail(err: unknown) {
    setError(err instanceof FacilityApiError ? err.message : "Couldn't reach us. Please try again.");
    setBusy(false);
  }

  async function sendPassword(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await facilityApi.login(email, password);
      setStep("code");
      setBusy(false);
    } catch (err) {
      fail(err);
    }
  }

  async function sendCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await facilityApi.loginCode(String(new FormData(event.currentTarget).get("code") ?? ""));
      setPassword("");
      router.replace(safeNext(params.get("next")));
    } catch (err) {
      fail(err);
    }
  }

  if (step === "code") {
    return (
      <form onSubmit={sendCode} className="space-y-5">
        {error && <Alert kind="error">{error}</Alert>}
        <Alert kind="info">
          We emailed a 6-digit code to <strong>{email}</strong>. It works for 10 minutes.
        </Alert>
        <div>
          <label htmlFor={`${id}-code`} className="block text-sm font-medium">
            Code
          </label>
          <input
            id={`${id}-code`}
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="\s*\d{6}\s*"
            maxLength={8}
            required
            autoFocus
            className={inputClass}
          />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={busy} className={primaryButtonClass}>
            {busy ? "Checking…" : "Sign in"}
          </button>
          <button type="button" disabled={busy} onClick={() => sendPassword()} className={secondaryButtonClass}>
            Email a new code
          </button>
          <button
            type="button"
            onClick={() => {
              setStep("password");
              setError(null);
            }}
            className="text-sm underline"
          >
            Start again
          </button>
        </div>
      </form>
    );
  }

  return (
    <form onSubmit={sendPassword} className="space-y-5">
      {params.get("expired") && (
        <Alert kind="info">
          For your patients&apos; privacy you were signed out after a few minutes without activity.
        </Alert>
      )}
      {error && <Alert kind="error">{error}</Alert>}
      <div>
        <label htmlFor={`${id}-email`} className="block text-sm font-medium">
          Work email address
        </label>
        <input
          id={`${id}-email`}
          name="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className={inputClass}
        />
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
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className={inputClass}
        />
      </div>
      <button type="submit" disabled={busy} className={primaryButtonClass}>
        {busy ? "Checking…" : "Continue"}
      </button>
      <p className="text-sm text-foreground/70">
        <Link href="/facility/forgot-password" className="text-brand-blue-dark underline">
          Forgot your password, or never set one?
        </Link>
      </p>
      <p className="text-sm text-foreground/70">
        Portal accounts are set up by our office for the facilities we work with. To get one, give us a call.
      </p>
    </form>
  );
}
