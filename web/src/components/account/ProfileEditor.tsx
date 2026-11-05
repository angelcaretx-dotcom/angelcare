"use client";

import { useId, useState, type FormEvent } from "react";
import { readParts } from "@/lib/addresses";
import { riderApi, RiderApiError } from "@/lib/rider";
import { AddressFields } from "../AddressFields";
import { AccountNav, Alert, inputClass, Loading, primaryButtonClass, secondaryButtonClass } from "./ui";
import { useRiderSession } from "./useRiderSession";

export function ProfileEditor() {
  const id = useId();
  const { profile, setProfile, handleError, signOut } = useRiderSession();
  const [message, setMessage] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [addressErrors, setAddressErrors] = useState<Record<string, string[]>>({});

  async function run(action: () => Promise<Parameters<typeof setProfile>[0]>, success: string) {
    setBusy(true);
    setMessage(null);
    setAddressErrors({});
    try {
      setProfile(await action());
      setMessage({ kind: "success", text: success });
      return true;
    } catch (err) {
      if (err instanceof RiderApiError) setAddressErrors(err.fieldErrors);
      const fields = err instanceof RiderApiError ? Object.values(err.fieldErrors).flat().join(" ") : "";
      const text = fields || handleError(err);
      if (text) setMessage({ kind: "error", text });
      return false;
    } finally {
      setBusy(false);
    }
  }

  function saveDetails(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    run(
      () =>
        riderApi.updateMe({
          full_name: String(data.get("full_name") ?? ""),
          phone: String(data.get("phone") ?? ""),
          mobility_notes: String(data.get("mobility_notes") ?? ""),
        }),
      "Details saved.",
    );
  }

  async function addAddress(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const ok = await run(
      () => riderApi.addAddress(String(data.get("label") ?? ""), readParts(data)),
      "Address saved.",
    );
    if (ok) form.reset();
  }

  if (!profile) return <Loading />;

  return (
    <div className="space-y-10">
      <AccountNav onSignOut={signOut} />
      {message && <Alert kind={message.kind}>{message.text}</Alert>}

      <section aria-labelledby={`${id}-details`} className="space-y-5">
        <h2 id={`${id}-details`} className="text-lg font-semibold">
          Your details
        </h2>
        <p className="text-sm text-foreground/70">
          Used to fill in the booking form for you. Signed in as <strong>{profile.email}</strong>.
        </p>
        <form onSubmit={saveDetails} className="space-y-5">
          <div className="grid gap-5 sm:grid-cols-2">
            <div>
              <label htmlFor={`${id}-name`} className="block text-sm font-medium">
                Full name
              </label>
              <input id={`${id}-name`} name="full_name" defaultValue={profile.full_name} className={inputClass} />
            </div>
            <div>
              <label htmlFor={`${id}-phone`} className="block text-sm font-medium">
                Phone number
              </label>
              <input id={`${id}-phone`} name="phone" type="tel" defaultValue={profile.phone} className={inputClass} />
            </div>
          </div>
          <div>
            <label htmlFor={`${id}-mobility`} className="block text-sm font-medium">
              Mobility needs
            </label>
            <textarea
              id={`${id}-mobility`}
              name="mobility_notes"
              rows={3}
              defaultValue={profile.mobility_notes}
              placeholder="e.g. uses a manual wheelchair, needs assistance transferring"
              className={inputClass}
            />
          </div>
          <button type="submit" disabled={busy} className={primaryButtonClass}>
            Save details
          </button>
        </form>
      </section>

      <section aria-labelledby={`${id}-addresses`} className="space-y-5">
        <h2 id={`${id}-addresses`} className="text-lg font-semibold">
          Saved addresses
        </h2>
        {profile.addresses.length === 0 ? (
          <p className="text-sm text-foreground/70">None yet. Save home, your clinic or dialysis center to book faster.</p>
        ) : (
          <ul className="divide-y divide-black/10 rounded-2xl border border-black/10">
            {profile.addresses.map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-4 p-4 text-sm">
                <span>
                  {a.label && <strong>{a.label}: </strong>}
                  {a.address}
                </span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => run(() => riderApi.removeAddress(a.id), "Address removed.")}
                  className="text-red-700 underline"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
        <form onSubmit={addAddress} className="space-y-4">
          <div className="sm:w-1/2">
            <label htmlFor={`${id}-label`} className="block text-sm font-medium">
              Name (optional)
            </label>
            <input id={`${id}-label`} name="label" maxLength={60} placeholder="Home" className={inputClass} />
          </div>
          <AddressFields id={`${id}-new`} legend="New address" required errors={addressErrors} />
          <button type="submit" disabled={busy} className={secondaryButtonClass}>
            Save address
          </button>
        </form>
      </section>
    </div>
  );
}
