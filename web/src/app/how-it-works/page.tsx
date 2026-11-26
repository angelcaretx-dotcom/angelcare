import type { Metadata } from "next";
import Link from "next/link";
import { Container } from "@/components/Container";
import { siteConfig } from "@/lib/site-config";

export const metadata: Metadata = {
  title: "How It Works",
  description: `How to book a ride with ${siteConfig.name}: request your trip, get your price by email, confirm, and ride. Round trips, wait time and what to expect on the day.`,
};

/**
 * Every statement on this page describes how the service actually runs
 * (docs/decisions/0047, 0048 and 0049, plus the booking and driver-app
 * decisions before them). No prices, and nothing about how drivers and
 * vehicles are vetted: rates and internal rules are not published on the
 * website (owner, 2026-11-05 and 2026-11-26).
 *
 * The emailed price lists each part (0049), so this page says so. What a
 * ride back without waiting means is stated as plain fact, as the price
 * email does: it does not say which choice costs less or how long a wait
 * would be.
 */
const steps = [
  {
    title: "Tell us about the ride",
    body: [
      "Fill in the request form, or call us. We ask who the ride is for, where to pick up and drop off, the date and time, and whether you need ambulatory or wheelchair transportation or a delivery.",
      "On the form we email you a short code to confirm your email address. A request is not a booking yet.",
    ],
  },
  {
    title: "We email your price",
    body: [
      "Our office reviews your request and works out the price for your trip. We send it to you by email with your reference number.",
      "Every trip is priced on its own details, so prices are not listed on this website. The email shows the total price with each part of it listed. For your privacy it does not repeat your trip details: your name, your addresses and the date and time of your ride are left out.",
    ],
  },
  {
    title: "Confirm your ride",
    body: [
      "Your ride is not booked until you confirm it. Call us or send us a message with your reference number, and we put your ride on the schedule.",
      "If you set up an online account, you can follow your ride under My trips, where it shows as Confirmed.",
    ],
  },
  {
    title: "Your driver arrives",
    body: [
      "Your driver comes to the pickup address, helps you board, and secures your wheelchair if you use one.",
      "At pickup you sign on the driver's phone to show the ride took place. If you are not able to sign, the driver notes that instead.",
    ],
  },
  {
    title: "Getting home",
    body: [
      "When you request the ride, choose how you want to get back: at a set time, by calling us when you are ready, or with your driver waiting for you.",
    ],
  },
];

const waitBenefits = [
  {
    title: "No second pickup to wait for",
    body: "Your driver is already outside when your appointment ends. You are not waiting in a lobby for another vehicle to be sent.",
  },
  {
    title: "The same driver and vehicle",
    body: "The driver who brought you takes you home, so you do not explain your needs twice.",
  },
  {
    title: "Easier after a tiring appointment",
    body: "After a treatment or a long visit, leaving straight away matters. So does not having to make a phone call to arrange it.",
  },
  {
    title: "It fits appointments that run long or short",
    body: "You do not have to guess a return time in advance.",
  },
  {
    title: "One booking, one price",
    body: "The wait is part of the price we email you, so family members and caregivers know the cost of the whole outing before the day.",
  },
];

export default function HowItWorksPage() {
  return (
    <Container className="py-16 sm:py-24">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
        How {siteConfig.name} Works
      </h1>
      <p className="mt-4 max-w-2xl text-foreground/70">
        From your first request to the ride home, here is what happens and
        what we need from you at each step.
      </p>

      <ol className="mt-12 space-y-8">
        {steps.map((step, index) => (
          <li key={step.title} className="flex gap-5">
            <span
              aria-hidden="true"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-blue-dark text-base font-bold text-white"
            >
              {index + 1}
            </span>
            <div className="max-w-2xl">
              <h2 className="text-xl font-semibold text-brand-blue-dark">
                <span className="sr-only">Step {index + 1}: </span>
                {step.title}
              </h2>
              <div className="mt-2 space-y-3 text-foreground/80">
                {step.body.map((paragraph) => (
                  <p key={paragraph}>{paragraph}</p>
                ))}
              </div>
            </div>
          </li>
        ))}
      </ol>

      <section aria-labelledby="wait-heading" className="mt-16 rounded-2xl bg-zinc-50 p-8">
        <h2 id="wait-heading" className="text-2xl font-bold tracking-tight">
          Why ask your driver to wait?
        </h2>
        <p className="mt-3 max-w-2xl text-foreground/80">
          Wait time is optional. If you choose it, your driver stays at your
          destination during the appointment and brings you back when you are
          done. Many passengers choose it for these reasons:
        </p>
        <ul className="mt-6 grid gap-6 sm:grid-cols-2">
          {waitBenefits.map((benefit) => (
            <li key={benefit.title}>
              <p className="font-semibold">{benefit.title}</p>
              <p className="mt-1 text-sm text-foreground/70">{benefit.body}</p>
            </li>
          ))}
        </ul>
        <p className="mt-6 max-w-2xl text-foreground/80">
          Without waiting, your driver leaves after dropping you off, and the
          ride back is a second, separate trip. If you call us when you are
          ready, there can be a wait while a driver finishes another ride.
        </p>
        <p className="mt-6 max-w-2xl text-sm text-foreground/70">
          To add it, choose &ldquo;Yes, the driver waits for me&rdquo; on the
          request form. Your emailed price will include it.
        </p>
      </section>

      <section aria-labelledby="facility-heading" className="mt-16 max-w-2xl">
        <h2 id="facility-heading" className="text-2xl font-bold tracking-tight">
          For care facilities
        </h2>
        <p className="mt-3 text-foreground/80">
          Facility staff can book rides for patients and residents and see
          every ride for their facility in the{" "}
          <Link href="/facility/login" className="text-brand-teal-dark hover:underline">
            facility portal
          </Link>
          . Accounts are set up by our office:{" "}
          <Link href="/contact" className="text-brand-teal-dark hover:underline">
            contact us
          </Link>{" "}
          to get started.
        </p>
      </section>

      <div className="mt-16 rounded-2xl bg-zinc-50 p-8 text-center">
        <p className="text-lg font-semibold">Ready to schedule a ride?</p>
        <p className="mt-2 text-foreground/70">
          Submit a request, or call us at{" "}
          <a href={siteConfig.phoneHref} className="font-semibold text-brand-blue-dark">
            {siteConfig.phone}
          </a>
          .
        </p>
        <Link
          href="/request"
          className="mt-4 inline-block rounded-full bg-brand-blue-dark px-6 py-3 text-sm font-semibold text-white hover:brightness-90"
        >
          Request Transportation
        </Link>
      </div>
    </Container>
  );
}
