import Link from "next/link";
import { Container } from "@/components/Container";
import { siteConfig } from "@/lib/site-config";

const footerLinks = [
  { href: "/services", label: "Services" },
  { href: "/service-area", label: "Service Area" },
  { href: "/about", label: "About" },
  { href: "/contact", label: "Contact" },
  { href: "/request", label: "Request Transportation" },
];

const legalLinks = [
  { href: "/privacy", label: "Privacy Policy" },
  { href: "/terms", label: "Terms of Use" },
  { href: "/accessibility", label: "Accessibility" },
];

export function Footer() {
  const year = new Date().getFullYear();

  return (
    <footer className="mt-24 border-t border-black/10 bg-zinc-50">
      <Container className="grid gap-8 py-12 sm:grid-cols-3">
        <div>
          <p className="text-base font-bold">{siteConfig.name}</p>
          <p className="mt-2 max-w-xs text-sm text-foreground/70">
            Non-emergency medical transportation serving {siteConfig.serviceArea}.
          </p>
        </div>

        <nav aria-label="Footer">
          <p className="text-sm font-semibold text-foreground/60">Site</p>
          <ul className="mt-3 space-y-2 text-sm">
            {footerLinks.map((link) => (
              <li key={link.href}>
                <Link
                  href={link.href}
                  className="text-foreground/80 hover:text-brand-maroon transition-colors"
                >
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <div>
          <p className="text-sm font-semibold text-foreground/60">Contact</p>
          <ul className="mt-3 space-y-2 text-sm">
            <li>
              <a href={siteConfig.phoneHref} className="hover:text-brand-maroon">
                {siteConfig.phone}
              </a>
            </li>
            <li>
              <a href={siteConfig.emailHref} className="hover:text-brand-maroon">
                {siteConfig.email}
              </a>
            </li>
          </ul>
        </div>
      </Container>

      <div className="border-t border-black/10">
        <Container className="flex flex-col gap-3 py-6 text-xs text-foreground/60 sm:flex-row sm:items-center sm:justify-between">
          <p>
            &copy; {year} {siteConfig.name}. All rights reserved.
          </p>
          <nav aria-label="Legal" className="flex gap-4">
            {legalLinks.map((link) => (
              <Link key={link.href} href={link.href} className="hover:text-brand-maroon">
                {link.label}
              </Link>
            ))}
          </nav>
        </Container>
      </div>
    </footer>
  );
}
