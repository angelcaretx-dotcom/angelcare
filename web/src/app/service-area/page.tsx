import type { Metadata } from "next";
import { Container } from "@/components/Container";
import { siteConfig } from "@/lib/site-config";

export const metadata: Metadata = {
  title: "Service Area",
  description: `${siteConfig.name} provides non-emergency medical transportation across ${siteConfig.serviceArea}.`,
};

export default function ServiceAreaPage() {
  return (
    <Container className="py-16 sm:py-24">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
        Service Area
      </h1>
      <div className="mt-6 max-w-2xl space-y-4 text-foreground/80">
        <p>
          {siteConfig.name} provides non-emergency medical transportation
          across {siteConfig.serviceAreaLower}.
        </p>
        <p>
          Availability for a specific trip depends on pickup and drop-off
          location, distance, and service type. Please{" "}
          <a href="/contact" className="text-brand-teal-dark hover:underline">
            contact us
          </a>{" "}
          or{" "}
          <a href="/request" className="text-brand-teal-dark hover:underline">
            submit a transportation request
          </a>{" "}
          to confirm availability for your trip.
        </p>
      </div>
    </Container>
  );
}
