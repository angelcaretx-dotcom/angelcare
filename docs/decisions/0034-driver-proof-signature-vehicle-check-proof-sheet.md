# ADR 0034: Driver proof: signature at pickup, daily vehicle check and odometer, trip proof sheet

- Status: Accepted
- Date: 2026-11-05

## Context

ADR 0029 gave every trip a timeline: time and GPS for each step. Payers
audit more than that:
- that the rider was there (a signature);
- that the vehicle was safe (a pre-trip inspection);
- how far it went (odometer).

They also want one document per trip they can be handed.

The owner decided (2026-11-05):
- the rider signs **at pickup**;
- the odometer is read at the **start and end of each vehicle's day**,
  not per trip;
- a failed inspection item **blocks the vehicle until it's cleared**;
- the proof sheet is a **PDF created on download, any time, and never
  stored**.

## Decision

**Signature at pickup.**
- *Rider picked up* can only be saved with either:
  - a signature drawn on the phone (a canvas; an optional "signed by"
    name for a caregiver), or
  - a reason the rider couldn't sign: unable, declined, or other with a
    note.
- The server re-draws the image as a small greyscale PNG
  (`trips/signatures.py`, Pillow). It refuses:
  - anything that isn't a PNG;
  - images that are too big;
  - an empty box.

  Only the drawing is kept.
- It's stored on the *Picked up* `TripEvent` (`signature`,
  `signed_by_name`, `signature_missing_reason`). That record is
  append-only like the rest of the timeline.
- Dispatchers see it on the trip's timeline in the admin.

**Daily vehicle check** (`vehicles.VehicleCheck`, `vehicles/checks.py`).
Once per vehicle per Texas day.
- **Start.** The Today screen shows *Start of day: check {plate}* for
  each vehicle on the driver's trips. The driver ticks each checklist
  item that's OK and types the odometer.
  - The checklist has 13 standard items. Wheelchair vans add the lift or
    ramp and the tie-downs; stretcher vans add the stretcher locks.
  - The odometer can't be lower than the vehicle's last reading.
  - A trip's first step is refused until its vehicle has today's check,
    and so is a trip with no vehicle assigned.
    No-show and cancelled at the door don't need it.
- **Failed item.** An unticked item is a problem.
  - It needs a note.
  - It sets the vehicle to *Out of service*. The existing assignment
    rule then stops anyone assigning trips to it, and the start rule
    stops trips starting in it.
  - Staff get an email: vehicle, driver and the problems. No rider
    details.
  - Only an **Administrator** clears it: *Daily vehicle checks* → the
    check → "what was fixed" → Save. The vehicle goes back to *Active*,
    unless staff had put it in another status, or another failed check
    still blocks it.
  - Dispatchers can read checks but not clear them.
- **End of day.** The driver types the odometer again, which gives the
  day's miles.
  - The reading can't be lower than the morning's, or more than 1,000
    miles above it.
  - A missing end reading from the last 7 days is asked for on the
    Today screen.
- Every start, end and clearing is audited.

**Proof sheet** (`trips/proof.py`, fpdf2). On any trip in the admin,
*Proof sheet (PDF)* downloads a PDF built at that moment. It contains:
- the trip, payer, broker trip number and prior authorization;
- the rider, the driver (with license number) and the vehicle (with VIN);
- every step with its time, GPS position and accuracy;
- the signature, or why there isn't one;
- the vehicle's check that day, with its odometer readings and miles.

It is never stored: no file, no storage use. Anyone who can view the
trip can download it. Each download is audited (`exported`) and sent
`no-store`.

## Consequences

- No per-trip loaded or deadhead miles. Mileage is per vehicle-day.
  Per-trip miles, if a payer needs them, come from map estimates
  (Phase 29/32).
- One check per vehicle per day: a second driver using the same
  vehicle that day doesn't re-inspect it.
- Signatures are PHI (they identify the rider). They live only in the
  database behind staff login and the audit trail, and are included in
  the encrypted nightly backup.
- A downloaded proof sheet holds PHI. Whoever downloads it is
  responsible for where it goes.
- New server libraries: fpdf2, Pillow, fonttools, defusedxml.
- Release: `vehicles.0002`, `trips.0006` and `notifications.0004`
  change the database. Run `ops/migrate-production.ps1` before pushing.
