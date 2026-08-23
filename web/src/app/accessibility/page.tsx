import type { Metadata } from "next";
import { Container } from "@/components/Container";
import { siteConfig } from "@/lib/site-config";

export const metadata: Metadata = {
  title: "Accessibility",
  description: `Accessibility statement for the ${siteConfig.name} website.`,
};

export default function AccessibilityPage() {
  return (
    <Container className="py-16 sm:py-24">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
        Accessibility Statement
      </h1>
      <div className="mt-6 max-w-2xl space-y-4 text-foreground/80">
        <p>
          {siteConfig.name} is committed to making this website usable by as
          many people as possible. This site is built with semantic HTML,
          labeled form fields, visible keyboard focus states, and a
          skip-to-content link, and is designed to work with keyboard
          navigation and screen readers.
        </p>
        <p>
          If you encounter any accessibility barrier while using this site,
          please contact us at{" "}
          <a href={siteConfig.emailHref} className="text-brand-teal-dark hover:underline">
            {siteConfig.email}
          </a>{" "}
          or call{" "}
          <a href={siteConfig.phoneHref} className="text-brand-teal-dark hover:underline">
            {siteConfig.phone}
          </a>{" "}
          and we will work to address it.
        </p>
      </div>
    </Container>
  );
}
