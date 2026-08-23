# ADR 0007: Driver and Vehicle domains

- Status: Accepted
- Date: 2026-08-23

## Context

Real dispatch (assigning a trip to a driver and vehicle) needs drivers
and vehicles to exist as records first. Section 7/8 of the project
directive describe fairly rich concepts for both -- background checks,
training, certifications, insurance, maintenance history, incidents,
documents.

## Decision

Built `drivers` and `vehicles` apps, each scoped to universal,
confirmable facts only:

- **Driver**: legal name, contact, employment type (employee/
  contractor), driver's license number + expiration date, status
  (active/inactive/suspended/terminated), notes.
- **Vehicle**: VIN (unique), make/model/year, license plate,
  wheelchair/stretcher capability (independent booleans, not a single
  "type" -- one vehicle can serve more than one service type),
  passenger capacity, registration + inspection expiration dates,
  status (active/maintenance/out_of_service/inactive), notes.

**Deliberately NOT built**, same reasoning as ADR 0006: insurance
requirements, background checks, training/certification records,
medical/fitness or drug-testing documentation, maintenance/mileage/
fuel/repair/incident history. These need either a Document domain that
doesn't exist yet, or real state-specific NEMT regulatory requirements
/ company policy that hasn't been confirmed (REQUIRES OFFICIAL SOURCE
/ REQUIRES BUSINESS DECISION) -- inventing structured fields for them
would mean fabricating compliance-relevant data.

License and registration/inspection expiration dates ARE tracked now,
specifically so a future dispatch/assignment feature can block
assigning an expired-credential driver or vehicle (Section 7/8) --
but that enforcement doesn't exist yet, since there's no assignment
feature to enforce it in. Tracking the date now avoids a data-migration
problem later; building the enforcement now would be enforcing a rule
against a feature that doesn't exist.

RBAC (ADR 0005) extended: `Dispatcher` gets view-only on both (they
need to see who/what is available, not edit HR/fleet records --
least privilege). `Administrator` gets full CRUD. Status changes on
both are wired into the audit log the same way as `TripRequest`/
`Passenger`.

## Consequences

- Trip lifecycle/dispatch (the next phase) can now reference real
  `Driver`/`Vehicle` records for assignment.
- Credential-expiration enforcement, insurance, training records,
  maintenance history remain explicitly tracked as future work, not
  silently missing.
