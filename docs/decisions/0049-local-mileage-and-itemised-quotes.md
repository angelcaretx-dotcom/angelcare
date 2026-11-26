# ADR 0049: Miles estimated inside the system, a check-and-send price page, and a price email that lists each part

- Status: Accepted
- Date: 2026-11-26
- Supersedes: the "total only" price email of [ADR 0047](0047-emailed-quotes.md)
  and its "miles are typed by staff" consequence. The rest of ADR 0047
  and all of ADR 0048 stand.

## Context

ADR 0047 put the price in an email sent from the admin. Three things
were left to the office: looking the miles up on a map and typing them,
remembering that a new request needs a price, and explaining a total
that arrived with no parts.

The first design for automatic miles sent both addresses to a free map
service. That was dropped before any code was written. The service
considered (openrouteservice, run by HeiGIT in Germany, terms read
2026-11-26) **forbids sending personal data** in its terms of service,
offers no data processing agreement, signs no HIPAA business associate
agreement, and keeps every full request for seven days. A rider's home
address sent together with a clinic's address is exactly the data it
forbids, and exactly the data ADR 0027 keeps away from companies we
have no agreement with.

**Owner decisions (2026-11-26):**

1. Miles are **estimated inside the system** from the two ZIP codes. The
   system never sends an address to a map company. The price page shows
   the estimate as approximate, with a button that opens the route in
   Google Maps so staff confirm the exact miles before sending.
2. The office email for a new request has a button that opens the price
   page, where the **price is already worked out**. An administrator
   checks it and sends it.
3. The customer's email shows the total **with the full breakdown** (each
   fee, the miles and the rate, the wait time), a short explanation of
   how the price works, and what a ride back without a waiting driver
   means.
4. A round trip pays the booking fee and the loading fee once and the
   miles both ways. Wait time is optional: the first free minutes, then
   each started block in full. (Unchanged from ADR 0048; restated
   because the email now explains it to the customer.)
5. For a booking made in the facility portal the **office decides each
   time who pays**. A price is emailed only when the person who booked
   pays.
6. Payment by card was decided but is **not built** here: see "Decided,
   not built yet" at the end.

One more fact shaped this release. On 2026-11-26 the site went down
because new code was live before its database migration had run. So
this release makes **no database change** and can go live by a plain
merge.

## Decision

### Miles: an estimate from the two ZIP codes

`transportation/distance.py`, with the table
`transportation/data/tx_zip_centroids.csv`.

- **The table** is the US Census Bureau's ZIP Code Tabulation Area
  gazetteer (public domain, 2026 file): the centre point of each of the
  1,989 Texas ZIP areas. It is a list of places and holds no rider data.
  `transportation/data/README.md` gives the source, the row count and
  how to regenerate it. Coordinates are copied as published and never
  edited by hand.
- **The ZIP** of each end is the stored ZIP part of the address, or for
  an older request the last 5-digit group in the one-line address (a
  ZIP+4 is accepted; a 5-digit house number at the start of the line is
  not taken for a ZIP).
- **The estimate** is the straight-line distance between the two centre
  points, times 1.3 because roads are longer than straight lines,
  rounded to a tenth of a mile.
- **No estimate**, with the reason shown, when: an address has no ZIP
  code; a ZIP is not in the table (outside Texas, or a ZIP used only for
  PO boxes, which the Census file does not list); both addresses are in
  the same ZIP code (the two centre points are the same point); or the
  table can't be read. None of these is an error: staff read the miles
  off the map as before.
- **No network call.** The module reads one file and does arithmetic. A
  test checks that it imports no HTTP client.

**Its limits, plainly.** It measures between the centres of two ZIP
areas, not between two doors, and it uses one factor for every road. On
a direct freeway route it is too high: downtown Fort Worth to downtown
Dallas is 30.8 miles in a straight line, so the estimate is 40.1, more
than the drive. Where the road has to go around something it can be too
low. In a large rural ZIP area the centre can be many miles from the
address. It is good enough to open the page with a price near the right
one, and not good enough to send.

**The Google Maps button** ("Open the route in Google Maps") is a plain
link with the two addresses in it. It opens in a new tab **in the staff
member's own browser**; the server contacts nobody. It is the same
practice as the driver app's directions link (ADR 0029). The link is
marked `noreferrer`, so Google is not told which admin page it came
from.

### The price page ("Email the price" on a trip request)

- A **Miles** panel: "About X miles one way (Y for the round trip),
  estimated from ZIP codes A and B. This is an estimate, not the driving
  distance.", or the reason there is no estimate, and the map button.
- The miles box starts with the **miles of the last price sent** for the
  request, otherwise with the estimate.
- When the box has a value, the page **opens with the price worked
  out**: the lines, the total and the send form. Opening the page still
  changes nothing except the "viewed" audit row.
- **"How this price is worked out"** lists, one sentence each, the rules
  of the rate card that apply to this request's service: booking fee,
  loading fee, per-mile rate and included miles, the round-trip rule,
  the after-hours rule if the card has one, the wait rule for a
  wait-and-return request, and for a round trip without waiting that the
  driver makes two separate trips. Amounts are shown: the page is for
  Administrators only (ADR 0047).
- **Two confirmations**, both unticked each time:
  - "I checked the miles on the map."
  - "The customer pays for this ride. It is not paid by Medicaid, a
    broker or a facility account."

  Without both, nothing is sent and nothing is saved; the price is shown
  again with a message. The rule is in the service (`quotes.send`), not
  only on the page. Both are written to the `quote_sent` audit row
  (`miles_checked`, `who_pays`).
- For a request **booked in the facility portal**, the page says so
  above the two boxes, names the facility, and says not to email a price
  if the facility is invoiced.
- Everything ADR 0047 refused is still refused: a closed request, no
  email address, a second click, a total too large to be real, a
  Medicaid or broker payer, and a price that is no longer the one that
  was checked.

**Why two confirmations.** A request from the website has no passenger
linked yet, so the system cannot know its payer; the Medicaid refusal of
ADR 0047 only works once a passenger is linked. With the price already
worked out, sending is one press. The second box makes a person say who
pays before a price can reach a Medicaid rider, who must never be sent
one. The first box is there because the miles now start as an estimate:
a wrong estimate must not go out with one click.

### The office email for a new request

It gains a button, **"Check and send the price"**, that opens the price
page for that request. "Review in Admin" and the request's link stay.
The email still carries **no rider details and no dollar amount**: the
price is worked out when the page is opened, behind the login. One staff
email and one customer email per booking, as before.

### The customer's price email

It now carries:

- the reference number;
- **one row per charge**, with its amount: "Booking fee", "Loading fee",
  "Miles: 12.4 x $…" (for a round trip "Miles there" and "Miles back";
  "(after the first N miles)" when the card includes miles),
  "After-hours fee", and "Waiting time: 2 x 15 minutes at $…";
- the **total price** and what it covers ("One-way ride", "Round trip
  (there and back)", "Round trip, with the driver waiting for you",
  "One-way trip");
- **"How your price works"**: two or three sentences about the parts
  this price has. The booking fee covers scheduling the trip and sending
  a vehicle; miles are the driving miles between the two addresses; on a
  round trip the fees are charged once and the miles both ways (or, on a
  card that charges them on each ride, that it is two rides). A
  wait-and-return price also says the free minutes, how many minutes the
  price covers, and the rate after that;
- **"Your ride back"**, only for a round trip where the driver does not
  wait (a ride back at a set time, or when the rider calls): the driver
  leaves after the drop-off and the ride back is a second trip; if you
  would rather have the driver wait, tell us and we send a new price.
  For "call when ready" it adds that there can be a wait if the driver
  is finishing another ride. It states facts only. It does not say which
  choice costs less, how long a wait would be, or that the same driver
  comes back: the system can promise none of those;
- as before: the price is for the trip as requested; the ride is **not
  booked yet**; how to book it.

It still never contains the rider's **name, any address, a date, a time,
or the service level**. The subject line and the preview line contain no
amount and no miles.

**How the service level is kept out of the wording.** A charge line now
carries a `kind` (`base`, `loading`, `miles`, `after_hours`, `wait`) and
a `leg` (`there`, `back`) beside its label. The email's rows are written
from the kind, the leg, the quantity, the rate and the amount only. The
office's label ("Wheelchair trip") is never printed in an email. The
labels themselves are unchanged: invoices, the admin and the claim file
read them. The lines saved with a quote or an invoice keep the label and
gain the two keys; rows saved before this have no such keys and are read
as before.

**What this gives up, and the owner accepted (decision 3).** ADR 0047's
email carried the total only. Rates are per service level (ADR 0048), so
the per-mile rate, the booking fee, and whether there is a loading fee
can tell someone who has seen both kinds of email which service a ride
is. The miles and the wait hint at how far and how long. The email is
still far less than the request itself, and it is sent only to the
address that made the request.

### Pricing: three rules so the invoice matches the emailed price

All three are in `billing/pricing.py`, in `price()`, so the invoice,
the profit report and percent-of-charge driver pay agree.

A trip **"was quoted"** when all of these hold: its request (its own, or
for a ride back its outbound trip's) has a delivered emailed price; the
newest delivered one still has its rate card; that card is for the same
payer the trip is billed to; and the payer is not Medicaid or a broker.
One function, `emailed_quote`, decides this for rules 2 and 3.

1. **Fees once per round trip means a linked ride back.** The booking
   and loading fees are left off only on a Return leg that is linked to
   its outbound trip, on a card that charges them once. A Return leg
   entered on its own has no first ride that paid them, so it pays them.
   Applies to every trip.
2. **A quoted trip is invoiced on the rate card its price used**, even
   when a newer card has started since. Applies to quoted trips only.
3. **No after-hours fee on a quoted ride back that had no set time**
   (will-call, the driver waits, or "at a set time" saved with no time).
   Its emailed price had none, because nobody knew the hour. This goes
   by the ride back the request asked for, the same question the price
   itself asks, not by the will-call or wait tick on the trip: a ride
   back priced at a set time keeps the fee its price included if
   dispatch later marks it will-call, and a ride back added by hand to
   a one-way request was never in the price, so it pays the fee.
   Applies to quoted trips only.

Every other trip prices exactly as before. In particular a contract
payer's or a standing order's ride back at night still pays the
after-hours fee, and a Medicaid or broker trip is never touched by an
emailed price.

### No database change

No new table, column or migration: `makemigrations --check` reports no
changes. The estimate is worked out each time the page opens and is not
stored; the confirmations go in the existing audit log; `kind` and `leg`
go in the existing JSON of the lines.

`ops/migrate-production.ps1` now prints the folder, branch and commit it
is about to migrate from before it asks for the password, so the owner
can see which version's migrations will run. This release does not need
it to be run.

## Consequences

- **The estimate is a starting point.** The price shown when the page
  opens can be several dollars off until the miles are corrected from
  the map. The system cannot check that the map was opened: the tick is
  the sender's word, kept in the audit log.
- **A wait-and-return price opens with no waiting time in it.** A request
  doesn't say how long the wait will be, so staff type the expected
  minutes before sending.
- **The second tick is the only guard for an unlinked request.** A
  Medicaid rider who books on the website and is not yet linked to a
  passenger is protected by the administrator's answer, not by the
  system. Linking the passenger first is still the safer habit; the
  Office guide says so.
- **Texas only, and only ZIP areas.** Out-of-state addresses and
  PO-box-only ZIP codes get no estimate.
- **The table must be deployed with the code.** If the file is missing
  the page says the table could not be read and staff type the miles;
  nothing fails. Refresh the table when the Census publishes a new
  year's file.
- **Still no maps provider** (roadmap decision 7). Exact miles worked
  out automatically, and deadhead miles for trip profit, need a provider
  that signs a business associate agreement. That is the path if the
  owner wants it later.
- **Clicking the map button shows Google the two addresses**, from the
  staff member's browser, with no name and no reference. So does the
  driver app's directions link. `docs/hipaa.md` records both.
- **Each customer now sees the rates for their own ride.** The amounts
  are still not on the website or in this repository.
- **A price emailed before a rate card change is honoured**: the trip is
  invoiced on the old card. To invoice on the new card, send a new
  price.
- The customer still books by calling or writing (ADR 0047). There is no
  "accept" or "pay" link yet.
- **Release:** no migration. Merge and deploy.

## Decided, not built yet

Owner decisions of 2026-11-26, recorded here so the next release starts
from them. Nothing in this release takes a payment.

- **Pay by card:** the price email will carry a Stripe payment link.
- **A paid price is final**, except for waiting beyond what was paid
  for, which is billed after the ride.
- **Refunds:** a full refund when the ride is cancelled at least 24
  hours before pickup, or when AngelCare cannot do the ride. No refund
  for a later cancellation or a no-show.
