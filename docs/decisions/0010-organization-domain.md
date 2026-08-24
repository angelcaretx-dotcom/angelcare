# ADR 0010: Organization domain

- Status: Accepted
- Date: 2026-08-24

## Context

Nothing in the system represented "AngelCare Transit" itself as a
data record -- only as hard-coded config in `web/src/lib/site-config.ts`.
Every other remaining roadmap item (Billing, Compliance) will
eventually need a real entity to anchor to. Section 1 describes a
fuller Organization concept (branches, locations, departments,
operating areas, business hours, holidays).

## Decision

A single `Organization` model, seeded via a data migration from facts
already confirmed in `docs/business-decisions-log.md` (legal name,
phone, email, service area) -- not invented here, just recorded as a
real row instead of scattered config.

**Deliberately a single record**, not a multi-branch model: there's no
confirmed second location, so branches/locations/departments/business
hours/holidays aren't built. Not enforced as a hard database
constraint (a future multi-entity structure isn't impossible), just an
admin-level practical limit (`OrganizationAdmin.has_add_permission`
refuses a second row; deletion is blocked entirely).

**This does NOT replace `web/src/lib/site-config.ts`.** ADR 0001
deliberately decoupled `web/` from calling `api/` for anything but the
trip request submission -- the public site doesn't fetch org info from
the API, and this ADR doesn't change that. Two representations of the
same facts (frontend static config, backend DB record) is an accepted
tradeoff for keeping that separation, not an oversight; if `web/`
becomes an authenticated app needing dynamic org data later,
unification can be revisited then.

No RBAC role currently touches Organization beyond the Django
superuser (or, if manually granted, an Administrator's own permission
in `/admin/` for `add`/`change` since it's registered normally) --
there's no dispatch/billing workflow yet that reads it, so no group
permissions were seeded for it.

## Consequences

- Future domains (Billing, Compliance) that need "which entity is this
  under" have a real record to `ForeignKey` to instead of inventing one
  under time pressure later.
- Two sources of the same facts (frontend config, backend record) must
  both be updated if a confirmed business fact changes (e.g. phone
  number) -- a known, accepted tradeoff, not a bug.
