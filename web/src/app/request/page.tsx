import type { Metadata } from "next";
import { Container } from "@/components/Container";
import { TripRequestForm } from "@/components/TripRequestForm";
import { siteConfig } from "@/lib/site-config";

export const metadata: Metadata = {
  title: "Request Transportation",
  description: `Submit a non-emergency medical transportation request to ${siteConfig.name}.`,
};

export default function RequestPage() {
  return (
    <Container className="py-16 sm:py-24">
      <div className="max-w-2xl">
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          Request Transportation
        </h1>
        <p className="mt-4 text-foreground/70">
          Fill out the form below to request non-emergency medical
          transportation. A member of our team will contact you to confirm
          the details.
        </p>
      </div>

      <div className="mt-10 max-w-2xl">
        <TripRequestForm />
      </div>
    </Container>
  );
}
