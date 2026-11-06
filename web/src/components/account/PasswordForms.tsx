"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useId, useState, type FormEvent } from "react";
import { riderApi, RiderApiError } from "@/lib/rider";
import { Alert, inputClass, primaryButtonClass } from "./ui";

/** Shared with the facility portal (ADR 0042), which passes its own API call and label. */
export function ForgotPasswordForm({
  send = riderApi.forgotPassword,
  emailLabel = "Email address you booked with",
}: { send?: (email: string) => Promise<unknown>; emailLabel?: string } = {}) {
  const id = useId();
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await send(String(new FormData(event.currentTarget).get("email") ?? ""));
      setSent(true);
    } catch (err) {
      setError(err instanceof RiderApiError ? err.message : "Couldn't reach us. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <Alert kind="success">
        If that email has an account with us, we&apos;ve sent it a link to choose a password. It works for 3
        days. Check your junk folder if it doesn&apos;t arrive in a few minutes.
      </Alert>
    );
  }
  return (
    <form onSubmit={onSubmit} className="space-y-5">
      {error && <Alert kind="error">{error}</Alert>}
      <div>
        <label htmlFor={`${id}-email`} className="block text-sm font-medium">
          {emailLabel}
        </label>
        <input id={`${id}-email`} name="email" type="email" autoComplete="email" required className={inputClass} />
      </div>
      <button type="submit" disabled={busy} className={primaryButtonClass}>
        {busy ? "Sending…" : "Email me a link"}
      </button>
    </form>
  );
}

export function SetPasswordForm({
  save = riderApi.setPassword,
  home = "/account",
  forgotHref = "/account/forgot-password",
}: {
  save?: (uid: string, token: string, password: string) => Promise<unknown>;
  home?: string;
  forgotHref?: string;
} = {}) {
  const id = useId();
  const router = useRouter();
  const params = useSearchParams();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<string[]>([]);
  const uid = params.get("uid") ?? "";
  const token = params.get("token") ?? "";

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const password = String(data.get("password") ?? "");
    if (password !== String(data.get("password2") ?? "")) {
      setFieldErrors(["The two passwords don't match."]);
      return;
    }
    setBusy(true);
    setError(null);
    setFieldErrors([]);
    try {
      await save(uid, token, password);
      router.replace(home);
    } catch (err) {
      if (err instanceof RiderApiError) {
        setFieldErrors(err.fieldErrors.password ?? []);
        if (!err.fieldErrors.password) setError(err.message);
      } else {
        setError("Couldn't reach us. Please try again.");
      }
      setBusy(false);
    }
  }

  if (!uid || !token) {
    return (
      <Alert kind="error">
        This link is incomplete. Open the link from your email again, or{" "}
        <Link href={forgotHref} className="underline">
          ask for a new one
        </Link>
        .
      </Alert>
    );
  }
  return (
    <form onSubmit={onSubmit} className="space-y-5">
      {error && (
        <Alert kind="error">
          {error}{" "}
          <Link href={forgotHref} className="underline">
            Get a new link
          </Link>
        </Alert>
      )}
      <div>
        <label htmlFor={`${id}-password`} className="block text-sm font-medium">
          New password
        </label>
        <input
          id={`${id}-password`}
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          className={inputClass}
          aria-describedby={`${id}-help`}
        />
        <p id={`${id}-help`} className="mt-1 text-xs text-foreground/60">
          At least 8 characters. Not just numbers, and not a common password.
        </p>
      </div>
      <div>
        <label htmlFor={`${id}-password2`} className="block text-sm font-medium">
          New password again
        </label>
        <input
          id={`${id}-password2`}
          name="password2"
          type="password"
          autoComplete="new-password"
          required
          className={inputClass}
        />
        {fieldErrors.length > 0 && <p className="mt-1 text-sm text-red-700">{fieldErrors.join(" ")}</p>}
      </div>
      <button type="submit" disabled={busy} className={primaryButtonClass}>
        {busy ? "Saving…" : "Save password and sign in"}
      </button>
    </form>
  );
}
