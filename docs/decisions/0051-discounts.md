# ADR 0051: A discount on an emailed price, and the owner's corrections to the price email

- Status: Accepted
- Date: 2026-11-26
- Builds on: [ADR 0049](0049-local-mileage-and-itemised-quotes.md) (the
  price page and the itemised price email) and
  [ADR 0050](0050-online-payment.md) (paying the emailed price online).
- Changes: the rows of the price email described in ADR 0049, and its
  "Your ride back" paragraph. Everything else in ADR 0049 stands.

## Context

The owner wants to take money off a price while checking it, before it
is emailed. The same day the owner read a real price email from ADR 0049
and asked for it to be shorter and clearer.

**Owner decisions (2026-11-26):**

1. **"Discount only."** An Administrator can take a dollar amount or a
   percentage off a price on the price page, with a reason. There are no
   promotion codes and nothing for a customer to type.
2. The price email shows **one miles row for a round trip**, never hides
   the real distance behind "included" miles, says what the price covers
   **before** the amounts, and says each thing once.
3. A second press of the Email button must not look like a second email.

## Decision

### The discount on the price page

The price page (`QuoteAdmin.send_view`, `quote_send.html`) has two more
boxes in the same single form: **Discount**, a number with a choice of
`$` or `%`, and **Reason for the discount**. Both are optional. "Work
out the price" shows the lines as before, then "Price before the
discount", then the **Discount** line (negative, with the reason beside
it), then the total.

Nothing is priced or sent, and the page says why, when:

- the discount is 0 or less;
- a percentage is more than 100, or has more than 2 decimal places;
- a dollar amount is more than the price before the discount, or has
  more than 2 decimal places;
- a discount has no reason, or the reason is longer than 200 characters;
- a percentage is so small it would take off $0.00.

A reason typed without a discount is ignored. A percentage is of the
**whole price before the discount**, rounded to cents half-up. A
discount is never carried over from an earlier price: it is typed each
time.

The rules live in one place, `pricing.discount_line`, used by the page
and by `quotes.send`, so a discount the page would refuse is refused by
the service too.

**The "price is the one you checked" guard covers the discount.** The
amount that is checked, shown on the button and compared on a send is
the total after the discount. Changing the discount and pressing Email
without "Work out the price" shows the new price again and sends
nothing.

The limit on a price (`MAX_QUOTE`) applies to the total after the
discount. A total of exactly $0.00 is allowed.

**Who may discount:** whoever may send a price (Administrators). No new
permission.

### How it is stored: no database change

A discount is **one more entry in `Quote.lines`**, the JSON list that
already holds how a price was worked out:

```
{"label": "Discount", "kind": "discount", "leg": "", "quantity": "1",
 "unit": "-12.00", "amount": "-12.00", "percent": "10", "reason": "…"}
```

`percent` is the percentage that was typed, or `""` for a dollar
discount. `Quote.amount` is the total **after** the discount. A price
saved before this has no such entry and behaves exactly as before. No
new table, no new column, no migration.

The `quote_sent` audit row records the discount: its type, the value
typed, the dollars it came to, and the reason.

### What the customer sees

One more row in the price email, after the parts and before "Total
price": `Discount: -$12.00`, or `Discount (10%): -$12.00` for a
percentage. The rows still add up to the total. **The reason is never in
any email**: the notification service is not given it. The wording does
not call it a promotion.

### What the office sees

The price's record (Billing > Prices emailed, "How it was calculated")
shows the discount line and the reason. The list of prices is unchanged
apart from the lower amount.

### Invoices, the profit report and driver pay

A trip whose emailed price had a discount gets a **"Discount" line on
its invoice**, so that when the ride goes as quoted the invoices of the
booking total exactly the emailed price.

**How the discount is shared between the two trips of a round trip**
(`pricing.discount_split`, one function, used for a price that was paid
online and for one that was not):

- the one-way or outbound trip takes the discount, up to its own part of
  the price: the saved lines with no ride named, or named "there";
- the linked ride back takes whatever is left.

This is decided **from the price's saved lines only**, so it never
depends on which invoice is made first.

The discount is the **dollar amount the customer was shown**, even when
it was typed as a percentage, and even when the ride turns out
different: a longer wait is charged in full on top. A **no-show** is
charged the no-show fee and gets no discount.

`pricing.price(trip)` carries the line. So `trip_charge`, the profit
report and percent-of-charge driver pay all see the **discounted**
charge.

Two details the rule needs in practice:

- **The wait line counts on the ride back's side.** In the saved lines
  the wait names no ride, but it is charged on the ride back's invoice.
  Counting it in the first trip's part would let the first trip's share
  be larger than what that trip is charged, and its invoice would be
  negative.
- **A share is never more than the trip's own charges.** When the ride
  turns out shorter than the price (fewer miles on the trip), the
  discount line stops at the trip's charges, so no invoice is negative.

A trip whose whole charge is covered by the discount has nothing to
bill: "Create invoices" lists it under what could not be billed, with
"Nothing to bill: the discount on the emailed price covers this ride."

### With online payment (ADR 0050)

- The amount asked at Stripe is the **total after the discount**.
- **Nothing about a discount is sent to Stripe**: it still receives only
  the amount, the currency, the quote's id and the product name with
  the reference.
- A total under 50 cents, including $0.00, gets **no pay link**. The
  email keeps "call or send a message to book".
- The invoices of a ride that was paid online repeat the paid price's
  saved lines (ADR 0050), with the discount shared by the same rule.

### Corrections to the price email

These change the email's rows and wording only. `Quote.lines`, the price
page, invoices and the sharing rule above still work from the saved
line for each ride.

- **One miles row for a round trip.** When the two rides have the same
  price per mile, the email shows one row with the miles added
  together: `Miles, round trip: 24.8 x $…` (the HTML email uses a real
  multiplication sign). It comes after the booking and loading fees.
  If the prices per mile differ, or if the miles added together times
  the price is not exactly the two amounts added together (each ride is
  rounded to cents on its own), each ride keeps its own row. **A row is
  never printed whose numbers do not multiply out.**
- **Included miles do not hide the distance.** On a card with included
  miles the row says the real miles first:
  `Miles: 12.4 miles, first 5 included: 7.4 x $…`, and for a round trip
  `Miles, round trip: 24.8 miles, first 10 included: 14.8 x $…`. The
  real miles are the miles of the price being sent; the included miles
  are the card's.
- **Fees.** A card that charges the booking and loading fees on each
  ride keeps a row for each ride. With the fees charged once there is
  one "Booking fee" row and, if the card has one, one "Loading fee" row.
- **"Covers" moves up**: directly under the reference, before the
  amounts, in the HTML and the text email.
- **"Your ride back" says each thing once.** It no longer repeats what
  "How your price works" says about a round trip being two rides: "On
  this booking the driver does not wait for you: the ride back is a
  separate trip." For "call when ready" only: "When you call for the
  ride back, there can be a wait if the driver is finishing another
  ride." Then: "If you would rather have the driver wait and bring you
  straight back, tell us and we will send you a new price." Facts only,
  as before: nothing about which choice costs less.
- **A second press of the Email button** within two minutes sends
  nothing, as before. The page now says so instead of repeating the
  green message: "This price was already emailed to (address) a moment
  ago. It was not sent again." `quotes.send` returns the first press's
  quote with `already_sent` set.

The email still never contains the rider's name, an address, a date, a
time or the service level.

## Consequences

- **A discount lowers a driver's percent-of-charge pay for that ride**,
  because that pay is a share of what the trip is billed. The owner may
  want it otherwise later (pay on the price before the discount); that
  would be a change in `pricing.trip_pay`.
- **The profit report shows the discounted charge**, so a discounted
  ride shows less profit. The discount itself is not a separate figure
  in the report; the audit log and the price's record have it.
- **The reason is staff-only**, and it is free text: staff must not put
  a rider's health details in it. It is kept in the quote and in the
  audit log, which only Administrators read.
- **A discount cannot be changed after the price is emailed.** Send a
  new price. Once a price is paid online it is final (ADR 0050).
- **A 100% discount means no invoice**: there is nothing to bill, and
  the trips stay listed under "could not be billed" with the reason.
- **No promotion codes, no automatic discounts, no discount on a
  contract payer's rates.** A discount exists only on a price an
  Administrator emails.
- **Release:** no migration for discounts.
