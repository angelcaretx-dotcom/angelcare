import type { Metadata } from "next";
import Link from "next/link";
import { Container } from "@/components/Container";
import { services, siteConfig } from "@/lib/site-config";

export const metadata: Metadata = {
  title: "Services",
  description: `Ambulatory, wheelchair, and stretcher non-emergency medical transportation from ${siteConfig.name}.`,
};

export default function ServicesPage() {
  return (
    <Container className="py-16 sm:py-24">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
        Our Services
      </h1>
      <p className="mt-4 max-w-2xl text-foreground/70">
        {siteConfig.name} offers the following non-emergency medical
        transportation services across {siteConfig.serviceAreaLower}.
      </p>

      <div className="mt-12 space-y-12">
        {services.map((service) => (
          <div key={service.slug} id={service.slug} className="border-t border-black/10 pt-8">
            <h2 className="text-2xl font-semibold text-brand-blue-dark">
              {service.name}
            </h2>
            <p className="mt-3 max-w-2xl text-foreground/80">{service.summary}</p>
          </div>
        ))}
      </div>

      <div className="mt-16 rounded-2xl bg-zinc-50 p-8 text-center">
        <p className="text-lg font-semibold">
          Not sure which service you need?
        </p>
        <p className="mt-2 text-foreground/70">
          Call us at{" "}
          <a href={siteConfig.phoneHref} className="text-brand-blue-dark font-semibold">
            {siteConfig.phone}
          </a>{" "}
          or submit a request and we&apos;ll help determine the right fit.
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
