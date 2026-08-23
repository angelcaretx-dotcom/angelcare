import type { Metadata } from "next";
import { Container } from "@/components/Container";
import { siteConfig } from "@/lib/site-config";

export const metadata: Metadata = {
  title: "Terms of Use",
  description: `Terms of use for the ${siteConfig.name} website.`,
};

export default function TermsPage() {
  return (
    <Container className="py-16 sm:py-24">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
        Terms of Use
      </h1>

      <div className="mt-4 max-w-2xl rounded-lg border border-amber-500/40 bg-amber-50 p-4 text-sm text-amber-900">
        <strong>Draft — pending legal review.</strong> This page has not been
        reviewed by an attorney and should not be treated as a final,
        binding agreement until it is. It does not contain pricing,
        cancellation, or service-level terms, which have not yet been
        defined.
      </div>

      <div className="mt-8 max-w-2xl space-y-6 text-foreground/80">
        <section>
          <h2 className="text-lg font-semibold text-foreground">
            Use of this website
          </h2>
          <p className="mt-2">
            This website provides information about {siteConfig.name} and a
            form to request non-emergency medical transportation. Submitting
            a request through this website does not guarantee that
            transportation will be provided; all requests are subject to
            confirmation by our team.
          </p>
        </section>
        <section>
          <h2 className="text-lg font-semibold text-foreground">
            Not for emergencies
          </h2>
          <p className="mt-2">
            This website and its request form are not monitored in real
            time and must not be used for medical emergencies. If you are
            experiencing a medical emergency, call 911 immediately.
          </p>
        </section>
        <section>
          <h2 className="text-lg font-semibold text-foreground">Contact</h2>
          <p className="mt-2">
            Questions about these terms can be sent to{" "}
            <a href={siteConfig.emailHref} className="text-brand-teal-dark hover:underline">
              {siteConfig.email}
            </a>
            .
          </p>
        </section>
      </div>
    </Container>
  );
}
