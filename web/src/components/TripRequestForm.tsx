"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ApiError, submitTripRequest, type BookedFor, type ServiceType } from "@/lib/api";
import { maybeSignedIn, riderApi, type SavedAddress } from "@/lib/rider";

interface FieldErrors {
  [key: string]: string[];
}

const serviceTypeOptions: { value: ServiceType; label: string }[] = [
  { value: "ambulatory", label: "Ambulatory" },
  { value: "wheelchair", label: "Wheelchair" },
  { value: "stretcher", label: "Stretcher" },
  { value: "equipment_delivery", label: "Medical Supply & Equipment Delivery" },
];

const bookedForOptions: { value: BookedFor; label: string }[] = [
  { value: "self", label: "Myself" },
  { value: "caregiver", label: "Someone I care for (family member, friend)" },
  { value: "facility", label: "A patient or resident (I work at a care facility)" },
];

function fill(form: HTMLFormElement, values: Record<string, string>) {
  for (const [name, value] of Object.entries(values)) {
    const field = form.elements.namedItem(name);
    if (value && (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement) && !field.value) {
      field.value = value;
    }
  }
}

export function TripRequestForm() {
  const formId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const [savedAddresses, setSavedAddresses] = useState<SavedAddress[]>([]);
  const [signedIn, setSignedIn] = useState(false);
  const [bookedFor, setBookedFor] = useState<BookedFor>("self");

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
        setSavedAddresses(me.addresses);
        fill(form, {
          full_name: me.full_name,
          phone: me.phone,
          email: me.email,
          mobility_notes: previous?.mobility_notes || me.mobility_notes,
          pickup_address: previous?.pickup_address ?? "",
          dropoff_address: previous?.dropoff_address ?? "",
          service_type: previous?.service_type ?? "",
        });
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
    const pickupTime = String(data.get("pickup_time") ?? "");
    const requestedDatetime =
      pickupDate && pickupTime
        ? new Date(`${pickupDate}T${pickupTime}`).toISOString()
        : "";

    try {
      await submitTripRequest({
        full_name: String(data.get("full_name") ?? ""),
        phone: String(data.get("phone") ?? ""),
        email: String(data.get("email") ?? ""),
        pickup_address: String(data.get("pickup_address") ?? ""),
        dropoff_address: String(data.get("dropoff_address") ?? ""),
        requested_datetime: requestedDatetime,
        service_type: String(data.get("service_type") ?? "") as ServiceType,
        mobility_notes: String(data.get("mobility_notes") ?? ""),
        additional_notes: String(data.get("additional_notes") ?? ""),
        booked_for: (String(data.get("booked_for") ?? "") || "self") as BookedFor,
      });
      setStatus("success");
      form.reset();
    } catch (err) {
      setStatus("error");
      if (err instanceof ApiError) {
        setErrorMessage(err.message);
        if (err.fieldErrors) setFieldErrors(err.fieldErrors);
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
    <form ref={formRef} onSubmit={handleSubmit} noValidate className="space-y-6">
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
        errors={fieldErrors.email}
      />

      <div className="grid gap-6 sm:grid-cols-2">
        <Field
          id={`${formId}-pickup_address`}
          name="pickup_address"
          label="Pickup address"
          list={savedAddresses.length ? `${formId}-saved` : undefined}
          required
          errors={fieldErrors.pickup_address}
        />
        <Field
          id={`${formId}-dropoff_address`}
          name="dropoff_address"
          label="Drop-off address"
          list={savedAddresses.length ? `${formId}-saved` : undefined}
          required
          errors={fieldErrors.dropoff_address}
        />
        {savedAddresses.length > 0 && (
          <datalist id={`${formId}-saved`}>
            {savedAddresses.map((a) => (
              <option key={a.id} value={a.address}>
                {a.label}
              </option>
            ))}
          </datalist>
        )}
      </div>

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

      <button
        type="submit"
        disabled={status === "submitting"}
        className="rounded-full bg-brand-blue-dark px-6 py-3 text-sm font-semibold text-white transition-colors hover:brightness-90 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {status === "submitting" ? "Submitting…" : "Submit Request"}
      </button>
    </form>
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
  list,
}: {
  id: string;
  name: string;
  label: string;
  type?: string;
  autoComplete?: string;
  required?: boolean;
  errors?: string[];
  list?: string;
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
        list={list}
        required={required}
        aria-invalid={errors && errors.length > 0 ? true : undefined}
        className="mt-1 w-full rounded-lg border border-black/15 px-3 py-2 text-sm focus:border-brand-teal-dark focus:outline-none"
      />
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
