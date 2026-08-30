import { Accessibility, BedSingle, Footprints, PackagePlus } from "lucide-react";
import Link from "next/link";
import { Container } from "@/components/Container";
import { ImageBanner } from "@/components/ImageBanner";
import { services, siteConfig } from "@/lib/site-config";

// One icon per service, matched by slug -- see src/lib/site-config.ts.
// Generic, professional glyphs (lucide-react), not brand-specific art.
const serviceIcons: Record<string, typeof Footprints> = {
  ambulatory: Footprints,
  wheelchair: Accessibility,
  stretcher: BedSingle,
  "equipment-delivery": PackagePlus,
};

export default function Home() {
  return (
    <>
      <section className="bg-zinc-50 py-12">
        <Container>
          <ImageBanner
            src="/marketing/hero-compassionate-nemt.png"
            alt="An AngelCare Transit driver helping a passenger who uses a wheelchair board a wheelchair-accessible van outside a medical center"
            fadeColor="#fafafa"
          >
            <h1 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
              Non-Emergency Medical Transportation, done right.
            </h1>
            <p className="text-base text-foreground/70">
              {siteConfig.name} provides ambulatory, wheelchair, and stretcher
              transportation across {siteConfig.serviceAreaLower}, so you can
              get to and from medical appointments safely and on time.
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
                className="rounded-full border border-brand-blue-dark bg-white px-6 py-3 text-center text-sm font-semibold text-brand-blue-dark transition-colors hover:bg-brand-blue-dark/5"
              >
                Call {siteConfig.phone}
              </a>
            </div>
          </ImageBanner>
        </Container>
      </section>

      <section className="py-16 sm:py-24">
        <Container>
          <h2 className="text-2xl font-bold tracking-tight sm:text-3xl">
            Our Services
          </h2>
          <div className="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {services.map((service) => {
              const Icon = serviceIcons[service.slug];
              return (
                <div
                  key={service.slug}
                  className="rounded-2xl border border-black/10 p-6 transition-shadow hover:shadow-md"
                >
                  {Icon && (
                    <div className="flex size-11 items-center justify-center rounded-full bg-brand-blue/15">
                      <Icon
                        className="size-6 text-brand-blue-dark"
                        strokeWidth={1.75}
                        aria-hidden="true"
                      />
                    </div>
                  )}
                  <h3 className="mt-4 text-lg font-semibold text-brand-blue-dark">
                    {service.name}
                  </h3>
                  <p className="mt-2 text-sm text-foreground/70">
                    {service.summary}
                  </p>
                </div>
              );
            })}
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
        <Container>
          <ImageBanner
            src="/marketing/hospital-discharge.png"
            alt="An AngelCare Transit driver greeting a passenger and family member outside a hospital's main entrance for a discharge ride home"
            fadeColor="#2d6f91"
          >
            <h2 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">
              Ready to schedule a ride?
            </h2>
            <p className="text-white/90">
              Submit a transportation request online, or call us directly at{" "}
              {siteConfig.phone}.
            </p>
            <Link
              href="/request"
              className="inline-block w-fit rounded-full bg-white px-6 py-3 text-sm font-semibold text-brand-blue-dark transition-colors hover:bg-white/90"
            >
              Request Transportation
            </Link>
          </ImageBanner>
        </Container>
      </section>
    </>
  );
}
