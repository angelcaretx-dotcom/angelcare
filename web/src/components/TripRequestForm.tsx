"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { readParts, type AddressParts } from "@/lib/addresses";
import { ApiError, submitTripRequest, type BookedFor, type ReturnRide, type ServiceType } from "@/lib/api";
import { maybeSignedIn, riderApi, type SavedAddress } from "@/lib/rider";
import { forgetToken, TRAP_FIELD, VerifyError } from "@/lib/verify";
import { AddressFields } from "./AddressFields";
import { TrapField, useEmailVerification } from "./EmailVerification";

interface FieldErrors {
  [key: string]: string[];
}

const serviceTypeOptions: { value: ServiceType; label: string }[] = [
  { value: "ambulatory", label: "Ambulatory" },
  { value: "wheelchair", label: "Wheelchair" },
  { value: "equipment_delivery", label: "Medical Supply & Equipment Delivery" },
];

const bookedForOptions: { value: BookedFor; label: string }[] = [
  { value: "self", label: "Myself" },
  { value: "caregiver", label: "Someone I care for (family member, friend)" },
  { value: "facility", label: "A patient or resident (I work at a care facility)" },
];

const returnRideOptions: { value: ReturnRide; label: string; hint?: string }[] = [
  { value: "none", label: "No, one way" },
  { value: "scheduled", label: "Yes, at a set time" },
  {
    value: "will_call",
    label: "Yes, I'll call when I'm ready",
    hint: "Good when you don't know how long the appointment will take.",
  },
  {
    value: "wait",
    label: "Yes, the driver waits for me",
    hint: "The driver stays nearby during the appointment and brings you back.",
  },
];

function fill(form: HTMLFormElement, values: Record<string, string>) {
  for (const [name, value] of Object.entries(values)) {
    const field = form.elements.namedItem(name);
    if (!value) continue;
    // Selects always have a value (state defaults to Texas), so they're overwritten.
    if (
      field instanceof HTMLSelectElement ||
      ((field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) && !field.value)
    ) {
      field.value = value;
    }
  }
}

/** `{pickup_street: ..., pickup_city: ...}` from a booked address (or an older one-line one). */
function partsFor(prefix: string, parts: AddressParts | null | undefined, line?: string): Record<string, string> {
  const source: Partial<AddressParts> = parts ?? (line ? { street: line } : {});
  return Object.fromEntries(Object.entries(source).map(([part, value]) => [`${prefix}${part}`, value ?? ""]));
}

function isoAt(date: string, time: string): string {
  return date && time ? new Date(`${date}T${time}`).toISOString() : "";
}

export function TripRequestForm() {
  const formId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const [savedAddresses, setSavedAddresses] = useState<SavedAddress[]>([]);
  const [signedIn, setSignedIn] = useState(false);
  const [accountEmail, setAccountEmail] = useState<string | null>(null);
  const verifier = useEmailVerification("booking");
  const [bookedFor, setBookedFor] = useState<BookedFor>("self");
  const [serviceType, setServiceType] = useState<string>("");
  const [returnRide, setReturnRide] = useState<ReturnRide>("none");
  const offersReturn = serviceType !== "equipment_delivery";

  // Signed-in riders (ADR 0031): fill in what we already know, and with
  // ?again=<request id>, the route and service of that earlier ride.
  useEffect(() => {
    if (!maybeSignedIn()) return;
    let cancelled = false;
    (async () => {
      try {
        const me = await riderApi.me();
        const again = new URLSearchParams(window.location.search).get("again");
        const previous = again ? (await riderApi.trips()).find((t) => t.id === again) : undefined;
        const form = formRef.current;
        if (cancelled || !form) return;
        setSignedIn(true);
        setAccountEmail(me.email);
        setSavedAddresses(me.addresses);
        fill(form, {
          full_name: me.full_name,
          phone: me.phone,
          email: me.email,
          mobility_notes: previous?.mobility_notes || me.mobility_notes,
          ...partsFor("pickup_", previous?.pickup, previous?.pickup_address),
          ...partsFor("dropoff_", previous?.dropoff, previous?.dropoff_address),
          service_type: previous?.service_type ?? "",
        });
        if (previous) {
          setServiceType(previous.service_type);
          // A set return time belonged to that day; ask again rather than guess.
          const kind = previous.return_ride?.kind;
          if (kind && kind !== "scheduled") setReturnRide(kind);
        }
      } catch {
        // Not signed in after all (or signed out): an ordinary blank form.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  const [status, setStatus] = useState<"idle" | "submitting" | "success" | "error">(
    "idle",
  );
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus("submitting");
    setErrorMessage(null);
    setFieldErrors({});

    const form = event.currentTarget;
    const data = new FormData(form);

    const pickupDate = String(data.get("pickup_date") ?? "");
    const requestedDatetime = isoAt(pickupDate, String(data.get("pickup_time") ?? ""));
    const ride: ReturnRide = offersReturn ? returnRide : "none";
    const returnDatetime =
      ride === "scheduled" ? isoAt(pickupDate, String(data.get("return_time") ?? "")) || null : null;
    const pickup = readParts(data, "pickup_");
    const dropoff = readParts(data, "dropoff_");
    const email = String(data.get("email") ?? "").trim();

    try {
      // Confirm the email first (ADR 0033): signed-in riders already
      // have, for their own address; everyone else gets a code.
      const ownEmail = accountEmail !== null && email.toLowerCase() === accountEmail.toLowerCase();
      const token = ownEmail
        ? (await riderApi.bookingToken()).token
        : await verifier.verify(email, String(data.get(TRAP_FIELD) ?? ""));
      if (!token) {
        setStatus("idle");
        return;
      }
      await submitTripRequest({
        full_name: String(data.get("full_name") ?? ""),
        phone: String(data.get("phone") ?? ""),
        email,
        pickup_street: pickup.street,
        pickup_unit: pickup.unit,
        pickup_city: pickup.city,
        pickup_state: pickup.state,
        pickup_zip: pickup.zip,
        dropoff_street: dropoff.street,
        dropoff_unit: dropoff.unit,
        dropoff_city: dropoff.city,
        dropoff_state: dropoff.state,
        dropoff_zip: dropoff.zip,
        requested_datetime: requestedDatetime,
        return_ride: ride,
        return_datetime: returnDatetime,
        service_type: String(data.get("service_type") ?? "") as ServiceType,
        mobility_notes: String(data.get("mobility_notes") ?? ""),
        additional_notes: String(data.get("additional_notes") ?? ""),
        booked_for: (String(data.get("booked_for") ?? "") || "self") as BookedFor,
        verification_token: token,
      });
      setStatus("success");
      form.reset();
    } catch (err) {
      setStatus("error");
      if (err instanceof ApiError) {
        setErrorMessage(err.message);
        if (err.fieldErrors) setFieldErrors(err.fieldErrors);
        if (err.fieldErrors?.verification_token) {
          forgetToken(email);
          setErrorMessage("Please submit again; we'll email you a new code to confirm your address.");
        }
      } else if (err instanceof VerifyError) {
        setErrorMessage(err.message);
        setFieldErrors(err.fieldErrors);
      } else {
        setErrorMessage(
          "Something went wrong submitting your request. Please try again or call us.",
        );
      }
    }
  }

  if (status === "success") {
    return (
      <div
        role="status"
        className="rounded-2xl border border-green-600/30 bg-green-50 p-6 text-green-900"
      >
        <p className="font-semibold">Request received.</p>
        <p className="mt-2 text-sm">
          Thank you — we&apos;ve received your transportation request. This is
          not a confirmed booking; our team will contact you to confirm
          details and availability.
        </p>
        {signedIn ? (
          <p className="mt-2 text-sm">
            You can follow it in{" "}
            <Link href="/account" className="underline">
              My trips
            </Link>
            .
          </p>
        ) : (
          bookedFor !== "facility" && (
            <p className="mt-2 text-sm">
              If this is your first request with this email address, we&apos;ve also
              emailed you a link to set up an account where you can track your rides.
            </p>
          )
        )}
      </div>
    );
  }

  return (
    <>
      <form ref={formRef} onSubmit={handleSubmit} noValidate className="relative space-y-6">
        {status === "error" && errorMessage && (
          <div
            role="alert"
            className="rounded-lg border border-red-600/30 bg-red-50 p-4 text-sm text-red-900"
          >
            {errorMessage}
          </div>
        )}

        <fieldset>
          <legend className="block text-sm font-medium">
            Who is this ride for? <span aria-hidden="true">*</span>
          </legend>
          <div className="mt-2 space-y-2">
            {bookedForOptions.map((option) => (
              <label key={option.value} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="booked_for"
                  value={option.value}
                  checked={bookedFor === option.value}
                  onChange={() => setBookedFor(option.value)}
                />
                {option.label}
              </label>
            ))}
          </div>
          {bookedFor === "caregiver" && (
            <p className="mt-2 text-xs text-foreground/60">
              Enter the rider&apos;s name below, and your own phone and email so we can reach you.
            </p>
          )}
          {fieldErrors.booked_for && <ErrorText messages={fieldErrors.booked_for} />}
        </fieldset>

        <div className="grid gap-6 sm:grid-cols-2">
          <Field
            id={`${formId}-full_name`}
            name="full_name"
            label="Full name"
            autoComplete="name"
            required
            errors={fieldErrors.full_name}
          />
          <Field
            id={`${formId}-phone`}
            name="phone"
            type="tel"
            label="Phone number"
            autoComplete="tel"
            required
            errors={fieldErrors.phone}
          />
        </div>

        <Field
          id={`${formId}-email`}
          name="email"
          type="email"
          label="Email address"
          autoComplete="email"
          required
          hint={accountEmail ? undefined : "We'll email you a code to confirm it's yours."}
          errors={fieldErrors.email}
        />

        <AddressFields
          id={`${formId}-pickup`}
          legend="Pickup address"
          prefix="pickup_"
          section="pickup"
          required
          errors={fieldErrors}
          saved={savedAddresses}
        />

        <AddressFields
          id={`${formId}-dropoff`}
          legend="Drop-off address"
          prefix="dropoff_"
          section="dropoff"
          required
          errors={fieldErrors}
          saved={savedAddresses}
        />

        <div className="grid gap-6 sm:grid-cols-2">
          <Field
            id={`${formId}-pickup_date`}
            name="pickup_date"
            type="date"
            label="Pickup date"
            required
            errors={fieldErrors.requested_datetime}
          />
          <Field
            id={`${formId}-pickup_time`}
            name="pickup_time"
            type="time"
            label="Pickup time"
            required
          />
        </div>

        <div>
          <label htmlFor={`${formId}-service_type`} className="block text-sm font-medium">
            Service type <span aria-hidden="true">*</span>
          </label>
          <select
            id={`${formId}-service_type`}
            name="service_type"
            required
            defaultValue=""
            onChange={(event) => setServiceType(event.target.value)}
            className="mt-1 w-full rounded-lg border border-black/15 px-3 py-2 text-sm focus:border-brand-teal-dark focus:outline-none"
          >
            <option value="" disabled>
              Select a service&hellip;
            </option>
            {serviceTypeOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {fieldErrors.service_type && (
            <ErrorText messages={fieldErrors.service_type} />
          )}
        </div>

        {offersReturn && (
          <fieldset>
            <legend className="block text-sm font-medium">
              Do you need a ride back? <span aria-hidden="true">*</span>
            </legend>
            <div className="mt-2 space-y-2">
              {returnRideOptions.map((option) => (
                <label key={option.value} className="flex items-start gap-2 text-sm">
                  <input
                    type="radio"
                    name="return_ride"
                    value={option.value}
                    checked={returnRide === option.value}
                    onChange={() => setReturnRide(option.value)}
                    className="mt-0.5"
                  />
                  <span>
                    {option.label}
                    {option.hint && <span className="block text-xs text-foreground/60">{option.hint}</span>}
                  </span>
                </label>
              ))}
            </div>
            {returnRide === "scheduled" && (
              <div className="mt-4 sm:w-1/2">
                <Field
                  id={`${formId}-return_time`}
                  name="return_time"
                  type="time"
                  label="Return pickup time (same day)"
                  required
                  errors={fieldErrors.return_datetime}
                />
              </div>
            )}
            {returnRide !== "scheduled" && fieldErrors.return_datetime && (
              <ErrorText messages={fieldErrors.return_datetime} />
            )}
            {fieldErrors.return_ride && <ErrorText messages={fieldErrors.return_ride} />}
          </fieldset>
        )}

        <TextAreaField
          id={`${formId}-mobility_notes`}
          name="mobility_notes"
          label="Mobility requirements (optional)"
          placeholder="e.g. uses a manual wheelchair, needs assistance transferring"
        />

        <TextAreaField
          id={`${formId}-additional_notes`}
          name="additional_notes"
          label="Additional notes (optional)"
        />

        <p className="text-xs text-foreground/60">
          Submitting this form sends a transportation request only — it is not
          a confirmed booking. Our team will follow up by phone or email. Do
          not use this form for medical emergencies; call 911 instead.
        </p>

        <TrapField />
        {verifier.widget}

        <button
          type="submit"
          disabled={status === "submitting" || verifier.waiting}
          className="rounded-full bg-brand-blue-dark px-6 py-3 text-sm font-semibold text-white transition-colors hover:brightness-90 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {status === "submitting" && !verifier.waiting ? "Submitting…" : "Submit Request"}
        </button>
      </form>
      {verifier.panel}
    </>
  );
}

function ErrorText({ messages }: { messages: string[] }) {
  return (
    <p className="mt-1 text-sm text-red-700">{messages.join(" ")}</p>
  );
}

function Field({
  id,
  name,
  label,
  type = "text",
  autoComplete,
  required,
  errors,
  hint,
}: {
  id: string;
  name: string;
  label: string;
  type?: string;
  autoComplete?: string;
  required?: boolean;
  errors?: string[];
  hint?: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium">
        {label} {required && <span aria-hidden="true">*</span>}
      </label>
      <input
        id={id}
        name={name}
        type={type}
        autoComplete={autoComplete}
        required={required}
        aria-invalid={errors && errors.length > 0 ? true : undefined}
        className="mt-1 w-full rounded-lg border border-black/15 px-3 py-2 text-sm focus:border-brand-teal-dark focus:outline-none"
      />
      {hint && <p className="mt-1 text-xs text-foreground/60">{hint}</p>}
      {errors && errors.length > 0 && <ErrorText messages={errors} />}
    </div>
  );
}

function TextAreaField({
  id,
  name,
  label,
  placeholder,
}: {
  id: string;
  name: string;
  label: string;
  placeholder?: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      <textarea
        id={id}
        name={name}
        rows={3}
        placeholder={placeholder}
        className="mt-1 w-full rounded-lg border border-black/15 px-3 py-2 text-sm focus:border-brand-teal-dark focus:outline-none"
      />
    </div>
  );
}
