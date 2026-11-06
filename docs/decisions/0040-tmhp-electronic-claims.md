# ADR 0040: Electronic claims to TMHP (837P, 999, 277CA, 835)

- Status: Accepted (file exchange); automated submission waits on TMHP enrollment
- Date: 2026-11-05

## Context

Claims were typed by hand (ADR 0019). The owner decided (2026-11-05)
to submit **to TMHP directly through TexMedConnect, with no
clearinghouse**. That means:
- generate 837P files to the TMHP EDI companion guide;
- handle the return traffic: 277CA rejections, the Remittance & Status
  report, denials and appeals;
- track the **95-day filing deadline** per claim.

## Decision

**Claims settings** (Billing → Claims settings, one page). Everything
TMHP assigns or the 837P needs is entered from TMHP's enrollment and
companion-guide papers:
- the TMHP payer record;
- submitter ID and contact;
- receiver name and ID, and the ID qualifier;
- payer ID;
- billing NPI, TPI and taxonomy;
- billing address with ZIP+4;
- place of service;
- procedure code (T2003), and an optional separate mileage code;
- default diagnosis and modifiers;
- the **Test / Production** switch.

**None of these values is written into the code.** A "Still needed"
line lists what's missing, and no file can be made until it's
complete.

**Rider details for claims.** The passenger page gains Medicaid ID,
date of birth, sex and home address. The 837P requires them.

**Claims** (`claims.Claim`, extended):
- *Create draft claims from TMHP invoices* makes one draft per invoice
  of the TMHP payer that has no open or paid claim. It takes:
  - the amount from the invoice (ADR 0039);
  - the date of service from the trip, on the Texas date;
  - the diagnosis code and modifiers from the defaults; staff can edit
    them per claim.
- Each claim has our **claim ID** (CLM01, `AC…`), and TMHP's claim
  number (ICN) once known.
- The **filing deadline** is the date of service + 95 days. It shows
  amber within 15 days and red when passed.

**837P files** (`claims/x12.py`, standard 005010X222A1). Select draft
claims and choose *Make an 837P file*. A file is made only if every
claim has:
- a rider with a Medicaid ID, date of birth, sex and address;
- a prior authorization;
- a diagnosis code;
- an amount;
- a date of service still inside the 95 days.

Otherwise every problem is listed and nothing changes. The file has
sequential control numbers and is marked **T** until Production is
ticked. Staff download it and upload it in TexMedConnect. Its claims
become *Submitted*. Files are kept, and can't be edited or deleted.

**Responses** (TMHP response files → Add). Upload what TexMedConnect
returns:
- **999:** whether a whole file was accepted. A rejected file rejects
  its claims.
- **277CA:** each claim accepted, with its ICN, or **rejected**, with
  the STC codes. A rejected claim is fixed and sent again as a new
  claim.
- **835**, the electronic Remittance & Status:
  - paid, with the amount; the invoice is marked Paid;
  - or **denied**, with each adjustment as group-reason-amount, e.g.
    CO-197;
  - a reversal (status 22) is flagged.

Claims are matched by our claim ID. Anything not ours is listed, never
guessed. Each file is kept as received, with what it changed.

**Denials and appeals.** *Claims needing attention* lists, soonest
deadline first:
- rejected and denied claims not yet resubmitted, with their reasons;
- drafts within 15 days of the deadline.

*Resubmit* makes a new draft:
- for a **denied** claim, a **replacement**: frequency 7 with REF*F8,
  the original ICN;
- for a rejected one, a fresh claim.

The history stays linked. Only one resubmission can be open at a time.

Everything is audited:
- claims created, files made and downloaded;
- responses read;
- status changes;
- settings changes.

Claims stay Administrator-only (ADR 0019).

## Consequences

- **Automated submission isn't built yet.** Sending files to TMHP by
  machine, and fetching responses the same way, needs TMHP's EDI
  enrollment: a submitter ID, a trading partner agreement and test
  sign-off. Until then the file is uploaded and downloaded in
  TexMedConnect by hand. The formats and the workflow are the same.
- **The companion guide may differ from the national standard.** Any
  TMHP-specific segment rules beyond it must be checked against TMHP's
  companion guide during TMHP testing. The Test flag exists for that.
- **The printed R&S report** (PDF) isn't read. Its electronic form, the
  835, is.
- **Appeal deadlines** beyond the 95-day filing rule aren't tracked:
  none were given.
- **Release:** `claims.0002` and `passengers.0004` change the database.
  Run `ops/migrate-production.ps1` before pushing.
