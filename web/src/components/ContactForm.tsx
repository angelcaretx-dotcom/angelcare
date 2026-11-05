"use client";

import Link from "next/link";
import { useId, useState, type FormEvent } from "react";
import { ApiError, submitContactMessage } from "@/lib/api";
import { forgetToken, TRAP_FIELD, VerifyError } from "@/lib/verify";
import { TrapField, useEmailVerification } from "./EmailVerification";

const inputClass =
  "mt-1 w-full rounded-lg border border-black/15 px-3 py-2 text-sm focus:border-brand-teal-dark focus:outline-none";

/**
 * The website's only way to write to us (ADR 0033): no email address is
 * published. The sender confirms their own email with a code first.
 */
export function ContactForm() {
  const id = useId();
  const verifier = useEmailVerification("contact");
  const [status, setStatus] = useState<"idle" | "sending" | "sent">("idle");
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const email = String(data.get("email") ?? "").trim();
    setError(null);
    setFieldErrors({});
    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }
    setStatus("sending");
    try {
      const token = await verifier.verify(email, String(data.get(TRAP_FIELD) ?? ""));
      if (!token) {
        setStatus("idle");
        return;
      }
      await submitContactMessage({
        name: String(data.get("name") ?? ""),
        email,
        phone: String(data.get("phone") ?? ""),
        message: String(data.get("message") ?? ""),
        verification_token: token,
        [TRAP_FIELD]: String(data.get(TRAP_FIELD) ?? ""),
      });
      setStatus("sent");
    } catch (err) {
      setStatus("idle");
      if (err instanceof ApiError) {
        if (err.fieldErrors?.verification_token) forgetToken(email);
        setFieldErrors(err.fieldErrors ?? {});
        setError(
          err.fieldErrors?.verification_token
            ? "Please send the message again; we'll email you a new code."
            : err.message,
        );
      } else if (err instanceof VerifyError) {
        setFieldErrors(err.fieldErrors);
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again or call us.");
      }
    }
  }

  if (status === "sent") {
    return (
      <div role="status" className="rounded-2xl border border-green-600/30 bg-green-50 p-6 text-green-900">
        <p className="font-semibold">Message sent.</p>
        <p className="mt-2 text-sm">Thank you. We&apos;ll get back to you by email or phone.</p>
      </div>
    );
  }

  const errorFor = (name: string) =>
    fieldErrors[name]?.length ? <p className="mt-1 text-sm text-red-700">{fieldErrors[name].join(" ")}</p> : null;

  return (
    <>
      <form onSubmit={onSubmit} noValidate className="relative space-y-5">
        {error && (
          <div role="alert" className="rounded-lg border border-red-600/30 bg-red-50 p-4 text-sm text-red-900">
            {error}
          </div>
        )}
        <div className="grid gap-5 sm:grid-cols-2">
          <div>
            <label htmlFor={`${id}-name`} className="block text-sm font-medium">
              Your name <span aria-hidden="true">*</span>
            </label>
            <input id={`${id}-name`} name="name" autoComplete="name" required maxLength={200} className={inputClass} />
            {errorFor("name")}
          </div>
          <div>
            <label htmlFor={`${id}-phone`} className="block text-sm font-medium">
              Phone (optional)
            </label>
            <input id={`${id}-phone`} name="phone" type="tel" autoComplete="tel" maxLength={20} className={inputClass} />
            {errorFor("phone")}
          </div>
        </div>
        <div>
          <label htmlFor={`${id}-email`} className="block text-sm font-medium">
            Your email address <span aria-hidden="true">*</span>
          </label>
          <input id={`${id}-email`} name="email" type="email" autoComplete="email" required className={inputClass} />
          <p className="mt-1 text-xs text-foreground/60">We&apos;ll email you a code to confirm it&apos;s yours.</p>
          {errorFor("email")}
        </div>
        <div>
          <label htmlFor={`${id}-message`} className="block text-sm font-medium">
            Message <span aria-hidden="true">*</span>
          </label>
          <textarea id={`${id}-message`} name="message" rows={6} required maxLength={5000} className={inputClass} />
          {errorFor("message")}
        </div>
        <TrapField />
        {verifier.widget}
        <p className="text-xs text-foreground/60">
          To book a ride, please use the{" "}
          <Link href="/request" className="underline">
            request form
          </Link>{" "}
          instead. For emergencies, call 911.
        </p>
        <button
          type="submit"
          disabled={status === "sending" || verifier.waiting}
          className="rounded-full bg-brand-blue-dark px-6 py-3 text-sm font-semibold text-white transition-colors hover:brightness-90 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {status === "sending" && !verifier.waiting ? "Sending…" : "Send message"}
        </button>
      </form>
      {verifier.panel}
    </>
  );
}
