import Link from "next/link";
import type { Metadata } from "next";
import { Container } from "@/components/Container";
import { siteConfig } from "@/lib/site-config";

export const metadata: Metadata = {
  title: "About Us",
  description: `About ${siteConfig.name}, a non-emergency medical transportation provider serving ${siteConfig.serviceArea}.`,
};

export default function AboutPage() {
  return (
    <Container className="py-16 sm:py-24">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
        About {siteConfig.name}
      </h1>
      <div className="mt-6 max-w-2xl space-y-4 text-foreground/80">
        <p>
          {siteConfig.name} provides non-emergency medical transportation
          (NEMT) across {siteConfig.serviceAreaLower}, offering
          ambulatory and wheelchair transportation for passengers getting to
          and from medical appointments, plus medical supply and equipment
          delivery.
        </p>
        <p>
          For questions about our company, service history, or credentials,
          please{" "}
          <Link href="/contact" className="text-brand-teal-dark hover:underline">
            contact us
          </Link>{" "}
          directly.
        </p>
      </div>
    </Container>
  );
}
