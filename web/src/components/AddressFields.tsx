"use client";

import { DEFAULT_STATE, US_STATES, type AddressParts } from "@/lib/addresses";
import type { SavedAddress } from "@/lib/rider";

const inputClass =
  "mt-1 w-full rounded-lg border border-black/15 px-3 py-2 text-sm focus:border-brand-teal-dark focus:outline-none";

/**
 * Street / apt / city / state / ZIP as one labelled group (ADR 0032).
 * Inputs are named `${prefix}street` etc.; errors are looked up the same way.
 */
export function AddressFields({
  id,
  legend,
  prefix = "",
  required,
  section,
  errors = {},
  saved = [],
}: {
  id: string;
  legend: string;
  prefix?: string;
  required?: boolean;
  /** Browser autofill section, so pickup and drop-off are filled separately. */
  section?: string;
  errors?: Record<string, string[] | undefined>;
  saved?: SavedAddress[];
}) {
  const auto = (token: string) => (section ? `section-${section} ${token}` : token);

  function applySaved(addressId: string, form: HTMLFormElement | null) {
    const address = saved.find((a) => a.id === addressId);
    if (!address || !form) return;
    // Addresses saved before ADR 0032 only have the one-line form.
    const parts: AddressParts = address.street
      ? address
      : { street: address.address, unit: "", city: "", state: DEFAULT_STATE, zip: "" };
    for (const part of ["street", "unit", "city", "state", "zip"] as const) {
      const field = form.elements.namedItem(`${prefix}${part}`);
      if (field instanceof HTMLInputElement || field instanceof HTMLSelectElement) {
        field.value = parts[part] || (part === "state" ? DEFAULT_STATE : "");
      }
    }
  }

  const field = (part: string) => ({
    id: `${id}-${part}`,
    name: `${prefix}${part}`,
    "aria-invalid": errors[`${prefix}${part}`]?.length ? true : undefined,
    className: inputClass,
  });
  const error = (part: string) => {
    const messages = errors[`${prefix}${part}`];
    return messages?.length ? <p className="mt-1 text-sm text-red-700">{messages.join(" ")}</p> : null;
  };
  const star = required ? <span aria-hidden="true"> *</span> : null;

  return (
    <fieldset className="space-y-3">
      <legend className="block text-sm font-semibold">
        {legend}
        {star}
      </legend>
      {saved.length > 0 && (
        <div>
          <label htmlFor={`${id}-saved`} className="block text-sm font-medium">
            Use a saved address
          </label>
          <select
            id={`${id}-saved`}
            defaultValue=""
            onChange={(event) => applySaved(event.target.value, event.target.form)}
            className={inputClass}
          >
            <option value="">Choose…</option>
            {saved.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label ? `${a.label}: ${a.address}` : a.address}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-6">
        <div className="sm:col-span-4">
          <label htmlFor={`${id}-street`} className="block text-sm font-medium">
            Street address{star}
          </label>
          <input {...field("street")} required={required} autoComplete={auto("address-line1")} />
          {error("street")}
        </div>
        <div className="sm:col-span-2">
          <label htmlFor={`${id}-unit`} className="block text-sm font-medium">
            Apt / suite / room
          </label>
          <input {...field("unit")} autoComplete={auto("address-line2")} />
          {error("unit")}
        </div>
        <div className="sm:col-span-2">
          <label htmlFor={`${id}-city`} className="block text-sm font-medium">
            City{star}
          </label>
          <input {...field("city")} required={required} autoComplete={auto("address-level2")} />
          {error("city")}
        </div>
        <div className="sm:col-span-2">
          <label htmlFor={`${id}-state`} className="block text-sm font-medium">
            State{star}
          </label>
          <select {...field("state")} required={required} defaultValue={DEFAULT_STATE} autoComplete={auto("address-level1")}>
            {US_STATES.map(([code, name]) => (
              <option key={code} value={code}>
                {name}
              </option>
            ))}
          </select>
          {error("state")}
        </div>
        <div className="sm:col-span-2">
          <label htmlFor={`${id}-zip`} className="block text-sm font-medium">
            ZIP code{star}
          </label>
          <input
            {...field("zip")}
            required={required}
            inputMode="numeric"
            maxLength={10}
            pattern="\d{5}(-\d{4})?"
            autoComplete={auto("postal-code")}
          />
          {error("zip")}
        </div>
      </div>
    </fieldset>
  );
}
