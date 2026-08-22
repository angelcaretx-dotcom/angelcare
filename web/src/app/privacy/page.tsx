import type { Metadata } from "next";
import { Container } from "@/components/Container";
import { siteConfig } from "@/lib/site-config";

export const metadata: Metadata = {
  title: "Privacy Policy",
  description: `How ${siteConfig.name} handles information submitted through this website.`,
};

export default function PrivacyPage() {
  return (
    <Container className="py-16 sm:py-24">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
        Privacy Policy
      </h1>

      <div className="mt-4 max-w-2xl rounded-lg border border-amber-500/40 bg-amber-50 p-4 text-sm text-amber-900">
        <strong>Draft — pending legal review.</strong> This page describes,
        factually, what information this website collects and why. It has
        not been reviewed by an attorney and should not be treated as a
        final, binding privacy policy until it is.
      </div>

      <div className="mt-8 max-w-2xl space-y-6 text-foreground/80">
        <section>
          <h2 className="text-lg font-semibold text-foreground">
            Information we collect
          </h2>
          <p className="mt-2">
            When you submit the transportation request form on this site, we
            collect the information you provide: your name, phone number,
            email address, pickup and drop-off addresses, requested date and
            time, service type, and any mobility or additional notes you
            choose to include.
          </p>
        </section>
        <section>
          <h2 className="text-lg font-semibold text-foreground">
            How we use it
          </h2>
          <p className="mt-2">
            We use this information solely to contact you and coordinate the
            transportation you requested. We do not sell this information.
          </p>
        </section>
        <section>
          <h2 className="text-lg font-semibold text-foreground">
            Contact
          </h2>
          <p className="mt-2">
            Questions about this policy or your information can be sent to{" "}
            <a href={siteConfig.emailHref} className="text-brand-cyan-dark hover:underline">
              {siteConfig.email}
            </a>
            .
          </p>
        </section>
      </div>
    </Container>
  );
}
