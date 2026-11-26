# ADR 0047: The price is emailed from the admin, not told by phone

- Status: Accepted
- Date: 2026-11-26

## Context

A website request showed no price (owner, 2026-11-05: no rates on the
website), and the confirmation said the office would call. The only way
a customer learned what a ride costs was a phone call, with the price
worked out by hand from the rate card. The rate cards (ADR 0039) were
only used after the ride, to make invoices.

**Owner decision (2026-11-26):** the office doesn't call customers with
a price. Staff send the **total price by email, from the admin**.

## Decision

**"Email the price"** is a button on a trip request's page (Trip
requests → a request). It opens a page that:

1. shows the request (who, service, both addresses, pickup time, ride
   back), so staff can look the distance up;
2. takes the **one-way miles**, and for a wait-and-return ride the
   **expected wait in minutes**;
3. works the price out from the rate card and shows it line by line;
4. on **"Email $… to …"**, emails the customer the total.

Nothing is sent until staff have seen the price. If the rate card
changes between checking and sending, the new price is shown again
instead of being sent.

**The price** (`billing/pricing.py`, `quote`) is worked out the way the
ride will be invoiced, so the invoice matches what the customer was
told:

- the rate card is the one invoicing would use: the linked passenger's
  payer, else **private pay**, in effect on the pickup's Texas date;
- each ride is base rate + miles beyond the included ones + the
  after-hours fee when its pickup time falls in the card's window;
- **a round trip is two rides**, the ride back over the same miles
  (a card can charge the booking and loading fees once for the round
  trip instead: ADR 0048);
- a wait-and-return ride adds the expected wait beyond the free minutes
  (in blocks, where the card says so: ADR 0048).

No price is made, and the page says why, when: there's no rate card in
effect, the card has no rate for that service, the card charges per
mile and no miles were entered, or the total is zero. A rider whose
payer is **Medicaid or a broker gets no price**: those rides are paid at
the program's rates and the rider owes nothing.

Two things a price can't know in advance are said on the page:

- a ride back with no set time (will-call, driver waits) gets no
  after-hours fee in the price;
- the invoice uses the wait the driver app records, which can differ
  from the expected wait.

**The email** (`customer_quote`) carries the reference number, the
**total price**, and what it covers ("One-way ride", "Round trip (there
and back)", "Round trip, with the driver waiting for you", "Delivery").
Like the confirmation (ADR 0027) it repeats no name, address, date,
time or service level. For a wait-and-return ride it also says how many
minutes of waiting the price covers and the per-minute rate after that:
the customer has to know it to agree to the price. It says the ride is
**not booked yet**, and to call or send a website message with the
reference number to book it.

**The record** (`billing.Quote`): one row per email sent, with the
amount, the lines it was worked out from, the miles, the wait, the card,
who sent it and to which address. A changed price is a new quote; a
sent one is never edited (Billing → Prices emailed is read-only). If
the email can't be sent, the quote is kept marked **not delivered**,
staff are told the customer has not received it, and the request is
left as it was.

**After sending:**
- a New or Reviewed request becomes **Contacted** (audited);
- the request's page shows the price last emailed;
- when a trip is made from the request, its **billable miles** are
  filled from the emailed quote if left blank, and the ride back made
  with it gets the same miles. Miles typed on the trip win.

**Who:** emailing a price needs the quote permission and access to trip
requests. Administrators have it; Dispatchers don't, and don't see the
button or the price, as with every other billing figure (ADR 0018,
0039). It can be granted per person under Billing in staff access
(ADR 0030).

**Wording changed with it:** the request confirmation email and the
rider's "My trips" status no longer say "we'll call you".

## Consequences

- Miles are typed by staff, from a map. There is still no maps provider
  (roadmap decision 7), so the system can't measure the distance itself.
- The customer still has to call or write to accept: there is no
  "accept this price" link. That is the natural next step, and the quote
  record is where an acceptance would be kept.
- The price is only as good as the private-pay rate card. With no card
  in effect on the ride's date, no price can be sent.
- A quote for a facility's own contract isn't covered: a facility
  booking is priced private-pay unless its passenger has a payer.
- Quotes go to the trash with their request and come back with it
  (ADR 0046).
- **Release:** `billing.0004` creates the table and `notifications.0009`
  adds the email type. Run `ops/migrate-production.ps1` before pushing.
