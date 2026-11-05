# ADR 0032: Round trips and separate address fields on the booking form

- Status: Accepted
- Date: 2026-11-04

## Context

Reviewing the live booking form, the owner asked three things:

1. Shouldn't city, state and ZIP be separate fields?
2. If the patient needs a return ride, do they have to make a second booking?
3. What if the patient wants the driver to wait?

The single address box let riders leave out the city or ZIP. Claims
(Phase 30) and broker files (Phase 28) need those on their own. A
return ride meant a second request that staff had to pair up by hand.
Wait-and-return, where the driver stays during the appointment, could
only be written in the notes.

## Decision

**Address parts.**
- Pickup and drop-off are each entered as street, apt / suite / room
  (optional), city, state (Texas preselected) and ZIP (5 digits, or
  ZIP+4). Each part is validated on its own and its errors show on that
  field.
- `TripRequest` stores the parts (`pickup_street` … `dropoff_zip`) and
  still composes `pickup_address`/`dropoff_address` from them. Dispatch,
  the driver app, maps links and trips keep using that one line.
- Requests made before this change keep only the one line.
- The booking API still accepts a one-line address when no parts are
  sent, so an older copy of the website keeps working during a
  deployment.
- In the admin, the one-line address is read-only when parts exist:
  edit the parts instead.
- Saved addresses (ADR 0031) are stored as parts too. The booking form
  offers "Use a saved address", which fills in every part.

**Return ride, on the same booking.** "Do you need a ride back?" has
four answers (`TripRequest.return_ride`):

| Answer | Stored | Return leg when scheduled |
| --- | --- | --- |
| No, one way | `none` | none |
| Yes, at a set time | `scheduled` + `return_datetime` (same day, after the pickup) | at that time |
| Yes, I'll call when I'm ready | `will_call` | `will_call`, estimated time |
| Yes, the driver waits for me | `wait` | `driver_waits`, same driver and vehicle, estimated time |

The question is hidden for equipment deliveries, and the API refuses a
return ride for them.

**Return leg created with the outbound trip.** When staff create a trip
from a round-trip request in the admin:
- That trip becomes the `outbound` leg.
- A `return` leg is created and linked through `outbound_trip`
  (`trips/returns.py`). It has the reverse route and the same passenger,
  payer, source and service. Its creation is audited.
- Will-call and wait-and-return times are **estimates**: an hour after
  the appointment time if one is set, otherwise two hours after the
  pickup. Both are flagged on the day board ("will-call",
  "driver waits") for dispatch to adjust.
- A set-time return is unassigned, so dispatch picks its driver.
- If the return leg fails validation, the outbound trip is still saved
  and staff are told to add the return leg by hand.

`Trip.driver_waits` is new. It's valid only on a return leg, and never
together with will-call.

**Rider side.**
- My trips shows "Ride back" with the answer, plus the time when it is
  fixed. If staff cancel the return leg, the ride back is no longer
  shown as promised.
- Cancelling online cancels both legs.
- "Book again" refills the address parts and the will-call or wait
  choice. A set return time is asked again, because it belonged to that
  day.

## Consequences

- One request can now mean two trips. Trips still link to a request one
  to one: the return leg reaches the request through its outbound trip.
- Wait time pricing isn't decided. The form promises only that the
  driver waits, not a price; rates come with Phase 29.
- Release: `transportation.0005`, `trips.0005` and `riders.0002` add
  columns only; run `ops/migrate-production.ps1` before pushing. Until
  both deployments finish, the new form can briefly meet the old API.
  Such a booking fails with a form error, not silently.
