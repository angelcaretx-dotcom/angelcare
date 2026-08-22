import type { Metadata } from "next";
import { Container } from "@/components/Container";
import { siteConfig } from "@/lib/site-config";

export const metadata: Metadata = {
  title: "Contact Us",
  description: `Contact ${siteConfig.name} by phone or email.`,
};

export default function ContactPage() {
  return (
    <Container className="py-16 sm:py-24">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
        Contact Us
      </h1>
      <p className="mt-4 max-w-xl text-foreground/70">
        For scheduling, questions, or to check availability in your area,
        reach out any of the ways below.
      </p>

      <dl className="mt-10 grid max-w-md gap-6">
        <div>
          <dt className="text-sm font-semibold text-foreground/60">Phone</dt>
          <dd className="mt-1 text-lg">
            <a href={siteConfig.phoneHref} className="text-brand-maroon font-semibold hover:underline">
              {siteConfig.phone}
            </a>
          </dd>
        </div>
        <div>
          <dt className="text-sm font-semibold text-foreground/60">Email</dt>
          <dd className="mt-1 text-lg">
            <a href={siteConfig.emailHref} className="text-brand-maroon font-semibold hover:underline">
              {siteConfig.email}
            </a>
          </dd>
        </div>
        <div>
          <dt className="text-sm font-semibold text-foreground/60">Service Area</dt>
          <dd className="mt-1 text-lg">{siteConfig.serviceArea}</dd>
        </div>
      </dl>

      <p className="mt-10 max-w-xl text-sm text-foreground/60">
        If you are experiencing a medical emergency, call 911 immediately.
        This contact information is for non-emergency transportation only.
      </p>
    </Container>
  );
}
