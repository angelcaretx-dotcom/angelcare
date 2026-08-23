import Link from "next/link";
import { Container } from "@/components/Container";
import { services, siteConfig } from "@/lib/site-config";

export default function Home() {
  return (
    <>
      <section className="bg-zinc-50 py-16 sm:py-24">
        <Container className="flex flex-col items-start gap-6">
          <h1 className="max-w-2xl text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
            Non-Emergency Medical Transportation, done right.
          </h1>
          <p className="max-w-xl text-lg text-foreground/70">
            {siteConfig.name} provides ambulatory, wheelchair, and stretcher
            transportation across {siteConfig.serviceAreaLower}, so
            you can get to and from medical appointments safely and on time.
          </p>
          <div className="flex flex-col gap-3 sm:flex-row">
            <Link
              href="/request"
              className="rounded-full bg-brand-blue-dark px-6 py-3 text-center text-sm font-semibold text-white transition-colors hover:brightness-90"
            >
              Request Transportation
            </Link>
            <a
              href={siteConfig.phoneHref}
              className="rounded-full border border-brand-blue-dark px-6 py-3 text-center text-sm font-semibold text-brand-blue-dark transition-colors hover:bg-brand-blue-dark/5"
            >
              Call {siteConfig.phone}
            </a>
          </div>
        </Container>
      </section>

      <section className="py-16 sm:py-24">
        <Container>
          <h2 className="text-2xl font-bold tracking-tight sm:text-3xl">
            Our Services
          </h2>
          <div className="mt-8 grid gap-6 sm:grid-cols-3">
            {services.map((service) => (
              <div
                key={service.slug}
                className="rounded-2xl border border-black/10 p-6"
              >
                <h3 className="text-lg font-semibold text-brand-blue-dark">
                  {service.name}
                </h3>
                <p className="mt-2 text-sm text-foreground/70">
                  {service.summary}
                </p>
              </div>
            ))}
          </div>
          <Link
            href="/services"
            className="mt-8 inline-block text-sm font-semibold text-brand-teal-dark hover:underline"
          >
            Learn more about our services &rarr;
          </Link>
        </Container>
      </section>

      <section className="bg-zinc-50 py-16 sm:py-24">
        <Container className="flex flex-col items-start gap-4">
          <h2 className="text-2xl font-bold tracking-tight sm:text-3xl">
            Serving {siteConfig.serviceArea}
          </h2>
          <p className="max-w-2xl text-foreground/70">
            {siteConfig.name} operates across Texas. Contact us to confirm
            availability for your specific pickup and drop-off locations.
          </p>
          <Link
            href="/service-area"
            className="text-sm font-semibold text-brand-teal-dark hover:underline"
          >
            View service area details &rarr;
          </Link>
        </Container>
      </section>

      <section className="py-16 sm:py-24">
        <Container className="flex flex-col items-start gap-4 rounded-2xl bg-brand-blue-dark px-8 py-12 text-white sm:items-center sm:text-center">
          <h2 className="text-2xl font-bold tracking-tight sm:text-3xl">
            Ready to schedule a ride?
          </h2>
          <p className="max-w-xl text-white/90">
            Submit a transportation request online, or call us directly at{" "}
            {siteConfig.phone}.
          </p>
          <Link
            href="/request"
            className="rounded-full bg-white px-6 py-3 text-sm font-semibold text-brand-blue-dark transition-colors hover:bg-white/90"
          >
            Request Transportation
          </Link>
        </Container>
      </section>
    </>
  );
}
