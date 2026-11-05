"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  checkCode,
  saveToken,
  savedToken,
  startVerification,
  TRAP_FIELD,
  VerifyError,
  type VerifyPurpose,
} from "@/lib/verify";
import { Turnstile, type TurnstileHandle } from "./Turnstile";

const inputClass =
  "mt-1 w-40 rounded-lg border border-black/15 px-3 py-2 text-lg tracking-[0.3em] focus:border-brand-teal-dark focus:outline-none";

/**
 * A field people never see. Form-filling bots fill it in, and the API then
 * quietly ignores the submission (ADR 0033).
 */
export function TrapField() {
  return (
    <div aria-hidden="true" className="absolute -left-[9999px] h-px w-px overflow-hidden">
      <label>
        Leave this empty
        <input type="text" name={TRAP_FIELD} tabIndex={-1} autoComplete="off" defaultValue="" />
      </label>
    </div>
  );
}

interface Pending {
  email: string;
  verificationId: string;
  trap: string;
}

/**
 * Confirms the person can read the email they typed before a form is sent
 * (ADR 0033). Call `verify(email, trap)` from the form's submit handler: it
 * resolves to a token (straight away if this tab confirmed that email
 * recently), or null if the person backs out. Render `widget` inside the
 * form and `panel` right after it.
 */
export function useEmailVerification(purpose: VerifyPurpose) {
  const id = useId();
  const openedAt = useRef(0);
  const turnstile = useRef<TurnstileHandle>(null);
  const resolver = useRef<((token: string | null) => void) | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    openedAt.current = Date.now();
  }, []);

  async function send(email: string, trap: string): Promise<string> {
    try {
      const { verification_id } = await startVerification({
        email,
        purpose,
        elapsedMs: Date.now() - openedAt.current,
        turnstileToken: turnstile.current?.token() ?? "",
        trap,
      });
      return verification_id;
    } finally {
      turnstile.current?.reset();
    }
  }

  /** Throws VerifyError when a code can't be sent (shown by the form). */
  async function verify(email: string, trap = ""): Promise<string | null> {
    const saved = savedToken(email);
    if (saved) return saved;
    const verificationId = await send(email, trap);
    setError(null);
    setNotice(null);
    setPending({ email, verificationId, trap });
    return new Promise((resolve) => {
      resolver.current = resolve;
    });
  }

  function finish(token: string | null) {
    setPending(null);
    resolver.current?.(token);
    resolver.current = null;
  }

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!pending) return;
    const code = String(new FormData(event.currentTarget).get("code") ?? "").replace(/\s/g, "");
    setBusy(true);
    setError(null);
    try {
      const { token, expires_at } = await checkCode(pending.verificationId, code);
      saveToken(pending.email, token, expires_at);
      finish(token);
    } catch (err) {
      setError(err instanceof VerifyError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      const verificationId = await send(pending.email, pending.trap);
      setPending({ ...pending, verificationId });
      setNotice("We sent a new code. Use the newest email.");
    } catch (err) {
      setError(err instanceof VerifyError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  const panel: ReactNode = pending ? (
    <section
      aria-labelledby={`${id}-title`}
      className="mt-6 rounded-2xl border border-brand-teal-dark/30 bg-sky-50 p-6"
    >
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        Check your email
      </h2>
      <p className="mt-2 text-sm text-foreground/80">
        We sent a 6-digit code to <strong>{pending.email}</strong>. Enter it below to{" "}
        {purpose === "booking" ? "send your ride request" : "send your message"}. It works for 10 minutes;
        check your junk folder if it hasn&apos;t arrived.
      </p>
      <form onSubmit={confirm} className="mt-4 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor={`${id}-code`} className="block text-sm font-medium">
            Code from the email
          </label>
          <input
            id={`${id}-code`}
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="\d{6}"
            maxLength={6}
            required
            autoFocus
            aria-invalid={error ? true : undefined}
            className={inputClass}
          />
        </div>
        <button
          type="submit"
          disabled={busy}
          className="rounded-full bg-brand-blue-dark px-6 py-3 text-sm font-semibold text-white disabled:opacity-60"
        >
          {busy ? "Checking…" : "Confirm"}
        </button>
      </form>
      {error && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {error}
        </p>
      )}
      {notice && !error && <p className="mt-2 text-sm text-green-800">{notice}</p>}
      <div className="mt-4 flex flex-wrap gap-4 text-sm">
        <button type="button" disabled={busy} onClick={resend} className="underline">
          Send a new code
        </button>
        <button type="button" onClick={() => finish(null)} className="underline">
          Change the email address
        </button>
      </div>
    </section>
  ) : null;

  return {
    verify,
    /** Waiting for the code: the form's own button should be disabled. */
    waiting: pending !== null,
    widget: <Turnstile ref={turnstile} />,
    panel,
  };
}
