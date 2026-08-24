# ADR 0009: Document domain

- Status: Accepted
- Date: 2026-08-24

## Context

Driver and Vehicle (ADR 0007) track credential *dates* (license,
registration, inspection expiration) but there was nowhere to actually
attach the file backing those dates up -- no scanned license, no
registration PDF. Section 16 describes a fairly broad document system
attachable to many entity types (passenger, driver, vehicle, trip,
payer, facility, company, compliance record).

## Decision

Built a `documents` app with a single `Document` model using Django's
built-in generic relations (`ContentType` + `GenericForeignKey`) so it
can attach to any model without depending on that model's app --
`documents` has no import-time dependency on `drivers` or `vehicles`;
they depend on it (via `DocumentInline`), not the reverse.

Fields: document type (a small, grounded enum -- driver's license,
vehicle registration/insurance/inspection, plus an `other` escape
hatch -- not every category Section 16 could eventually need),
file, optional expiration date, and a review workflow (pending ->
verified/rejected, with `verified_by`/`verified_at` and a required
rejection reason).

**Upload UX**: a `GenericTabularInline` (`DocumentInline`) attaches to
`DriverAdmin`/`VehicleAdmin` so staff upload a document directly from
that record's page. **Verification/rejection happens only in the
standalone `Document` admin list**, not inline -- so the audit-logged
status-change logic lives in exactly one place regardless of where a
document was uploaded.

**Deliberately NOT built**: explicit version-chaining (re-uploading
creates a new row rather than overwriting -- historical truth is
preserved -- but there's no `supersedes`/`superseded_by` link yet;
real complexity worth designing once there's an actual re-upload
workflow to design it around, not speculatively now).

RBAC (ADR 0005): `Dispatcher` gets view-only, `Administrator` full
CRUD -- matching the existing least-privilege pattern (Dispatcher
already can't edit Driver/Vehicle HR/fleet records directly).

## Two bugs caught and fixed during this work

1. **Generic inline formset prefix**: Django's default prefix for a
   `GenericInlineModelAdmin` is `<app_label>-<model_name>-<ct_field>-
   <fk_field>` (here: `documents-document-content_type-object_id`),
   not the `<model>_set` convention a normal FK inline uses. Existing
   `DriverAdmin`/`VehicleAdmin` admin tests broke until their POST
   payloads included this formset's management-form fields, even
   though those tests don't touch documents at all -- any admin
   change-form POST must include every inline's formset management
   fields, not just the fields you care about.
2. **`instance.pk is None` doesn't detect "new" for UUID-default
   PKs**: `Document.id` gets its UUID at Python instantiation, not at
   DB save time, so that's never a reliable "is this new" check.
   `formset.new_objects` (populated by `formset.save(commit=False)`)
   is the correct signal -- used in `DocumentUploaderAdminMixin`.

## Consequences

- **Production storage gap, flagged not silently shipped**: local
  filesystem storage (Django's default) works for local dev, but
  Fly.io's containers have ephemeral disk -- an uploaded file would be
  lost on the next deploy. Production needs a real object storage
  backend (Supabase Storage is the natural fit, already used for the
  database) before Document uploads are trustworthy in production.
  Tracked in known-limitations.md; not addressed here since it's a
  deployment-time decision, not a local-dev blocker (ADR 0004).
