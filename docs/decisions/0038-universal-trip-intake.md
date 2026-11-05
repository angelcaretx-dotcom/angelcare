# ADR 0038: Universal trip intake (Phase 28, expanded)

- Status: Accepted (v1)
- Date: 2026-11-05

## Context

Orders arrive as files in each sender's own layout: brokers (MTM,
SafeRide), HHSC, facilities and subcontract partners. Neither broker
has onboarded us yet, so their export columns can't be matched in
advance.

The owner asked (2026-11-05) for a **format-agnostic importer**:
- upload any sender's PDF, Excel or CSV;
- trips are created from it without re-typing;
- nothing questionable goes straight onto the schedule.

## Decision

**Operations → Trip imports** (app `intake`). The flow:

1. **Upload.** Choose the source and, optionally, the payer, then the
   file. The original is stored in the private storage bucket and can
   be downloaded from the import page, with the download audited.
2. **Extract** (`intake/extract.py`, on our own server only):
   - CSV: any delimiter, UTF-8 or Windows encoding;
   - Excel `.xlsx`: the first sheet;
   - PDF with text: its tables, or a single order form of
     "Label: value" lines.

   A scanned PDF has no text and is refused with a clear message. OCR
   is v2. Up to 2,000 rows per file.
3. **Map** (`intake/mapping.py`). Each of the sender's columns is
   matched to our fields: trip ID, prior auth, rider name (or first and
   last), phone, date, time, appointment, each address as one line or
   as street/unit/city/state/ZIP, service, return time or will-call,
   mobility aids, oxygen, escort, instructions.
   - A new layout gets a best guess from common column names, which
     staff check on screen.
   - Saving the mapping as a **template** maps that sender's next file
     automatically: a template is used when all its columns are present.
   - MTM's and SafeRide's exports will become two more templates when
     they onboard us.
4. **Check every row** (`intake/normalize.py`). Nothing is guessed.
   - **Hard problems** must be fixed in review, or the row rejected:
     - no rider, phone, date or time;
     - an unreadable value;
     - a bad ZIP or state;
     - the same pickup and drop-off;
     - a past pickup;
     - a return before the pickup;
     - a service other than ambulatory or wheelchair. Codes are
       mapped: "WC" → wheelchair, "AMB" → ambulatory. **Stretcher rows
       go to review and are never created by import.**
   - **Soft problems** need a person to confirm, by approving:
     - a rider we don't have yet;
     - a phone we know under a different name.

     Approving creates the passenger.
   - **Duplicates are skipped:**
     - the sender's trip ID already on the schedule for that source,
       or earlier in the same file;
     - without an ID: the same rider, phone and pickup time.

     Re-importing a file creates nothing.
5. **Review queue.** Each row shows the sender's text beside our reading.
   Staff can correct the fields, then re-check, approve or reject.
   **Ready** rows can be created all at once.
6. **Create.**
   - Approved rows become ordinary trips: the sender's trip ID, the
     prior-auth number, a composed address, Texas times, and notes from
     mobility, oxygen, escort and instructions.
   - A return time or will-call creates a second, linked **return leg**
     (`returns.return_leg_for`).
   - A driver and vehicle are assigned **only** when a standing order
     matches the rider, weekday and time in Texas time, **and** the
     usual checks pass, including Phase 27 credentials and insurance.
     Otherwise the trip is left unassigned for dispatch.
   - The trip page links back to the import line it came from.

**The report** at the top of each import counts: rows read, trips
created, ready, need review, duplicates skipped, failed and rejected.
Each problem is listed against its row.

**Texas time:** every date and time in a file is read as Central time.
A regression test checks that 10:00 AM in a file shows as 10:00 AM on
the day board.

**PHI:**
- Originals and rows live only in the private bucket and the database,
  behind staff login.
- Viewing an import or a row is logged. Uploads, mappings, edits,
  approvals, rejections and downloads are audited.
- Imports send no emails.
- No file is sent to any outside extraction or OCR service. A future
  OCR provider needs a signed BAA first.

**Access:**
- Dispatchers can upload, review and approve, and rename mappings.
- Administrators can also delete mappings.
- Nobody deletes an import: the original is the record of what was
  received.

## Consequences

- **Prior auth:** trips already had a prior-authorization field
  (ADR 0028), so import fills it in. Phase 30 claims will read it.
- **Time-only columns:** an import whose time columns lack a date uses
  the row's pickup date, e.g. for the return and appointment times.
- **"Last, First" names:** a name with exactly one comma is read as
  "Last, First", and all-caps names are title-cased. Staff can correct
  either in review.
- **New libraries:**
  - openpyxl and pdfplumber, with pdfminer.six and pypdfium2;
  - cryptography, pinned to an established release.
- **Later versions:**
  - v2: OCR for scanned PDFs, only with a BAA'd or on-server engine;
  - v3: real MTM and SafeRide templates at onboarding, and their offer
    dates feeding the SLA dashboard (ADR 0037).
- **Release:** `intake.0001` changes the database. Run
  `ops/migrate-production.ps1` before pushing.
