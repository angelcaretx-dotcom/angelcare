# ADR 0046: The trash bin

- Status: Accepted
- Date: 2026-11-08

## Context

Every delete was permanent. Admin history kept a deleted record's name
but not its data, so nothing could be restored. The owner asked for a
trash bin (2026-11-08):
- every delete is kept whole;
- only full admins can see the trash;
- a record can be restored with its links, or deleted for good after a
  check;
- nothing is purged automatically.

**Owner decision on linked records (2026-11-08):** deleting a passenger,
driver or vehicle whose trips have **not** been billed sends the record
and those trips (with their steps and signatures) to the trash
together, and Restore brings them all back linked. Anything billed (an
invoice or claim exists), and a payer used by invoices or claims, stays
blocked; set it Inactive instead. Nothing on the schedule ever points
at a trashed record.

## Decision

**Snapshot, then delete** (`trash/services.py`). A delete still removes
the rows, so every list, search, count, report and the day board is
correct with no change. Before it does, it saves everything it removes
as one `TrashEntry`:
- the record;
- its own parts (what the database already removes with it, e.g. a
  facility's contacts or a driver's credentials);
- the records that depend on it and would otherwise block the delete
  (a passenger's trips, those trips' steps, standing orders,
  authorizations), followed as far as they go.

They're kept in Django's JSON format, with their ids.

- Records outside the delete that pointed at it with a link the delete
  blanks (a trip request's passenger, an import row's trip) are noted
  and re-pointed on restore.
- **Blocked:** a delete that would reach an invoice, claim, 837P file,
  TMHP response file or Claims settings is refused. The delete page
  lists those as "billed: set the record Inactive instead".
- **Where deletes come from:**
  - the admin's delete page and its "delete selected" action
    (`AngelCareModelAdmin.delete_model` / `delete_queryset`);
  - rows deleted in an inline table on a record's page (`save_related`);
  - a rider removing a saved address.

  The facility portal and the driver app have no deletes. A rider
  cancelling a ride is a status change, not a delete.
- **What the trash keeps:** records in the project's own apps. Logins,
  MFA devices and the audit logs aren't kept: switch a login off
  instead. System clean-up isn't trashed either, such as a standing
  order re-making its untouched trips.
- **Permissions:** the delete permission is needed on each kind of
  record a delete takes, as before. Proof and log records that nobody
  deletes on their own go along with their parent without one (trip
  steps, vehicle checks, standing-order skips, import rows, facility
  activity, saved addresses).

**The trash** (Admin → System → Trash):
- **Who:** superusers and members of the Administrator group only.
  Everyone else gets 403 on every trash address and no menu entry.
- **The list:** each item shows the record, its type, how many more
  records went with it, who deleted it, where from (admin, rider
  account, facility portal, driver app) and when.
- **Search and filters:** search by record name (e.g. "TEST") or by who
  deleted it. Filter by type, by where it was deleted from, by date
  deleted (with a date drill-down) and by who.
- **An item:** shows the record read-only, field by field, as it was,
  with linked records named, plus what went with it. Opening one is
  logged as a view (ADR 0027), since it holds rider data.
- **Restore:** one click, one at a time or in bulk. Every row comes back
  with its id, and blanked links are re-pointed. It's refused, with
  nothing changed, when a record with the same id or number exists
  again, or when something it links to is gone.
- **Delete for good:** a confirmation page shows each record, what went
  with it, and anything still linked to it, then deletes on "Yes". This
  works one at a time or in bulk.
- **Logging:** delete, restore and permanent delete each go in the
  audit log and Django's admin history (who and when). Those lines
  stay after the item is gone.
- **No automatic purge.**

## Consequences

- A restore puts the rows back exactly as they were, without running the
  model's save logic. An item deleted before a later schema change may
  not restore. It says so, and nothing is changed.
- Trash items are part of the nightly backup, like any other table.
- Documents (uploaded files) stay in storage when their record is
  deleted, so a restored record finds them again.
- **Release:** `trash.0001` creates the table. Run
  `ops/migrate-production.ps1` before pushing.
