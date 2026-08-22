import Link from "next/link";
import { Container } from "@/components/Container";
import { Logo } from "@/components/Logo";
import { siteConfig } from "@/lib/site-config";

const navLinks = [
  { href: "/services", label: "Services" },
  { href: "/service-area", label: "Service Area" },
  { href: "/about", label: "About" },
  { href: "/contact", label: "Contact" },
];

export function Header() {
  return (
    <header className="border-b border-black/10 bg-white">
      <Container className="flex h-16 items-center justify-between">
        <Logo />
        <nav
          aria-label="Primary"
          className="hidden items-center gap-6 text-sm font-medium md:flex"
        >
          {navLinks.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="text-foreground/80 hover:text-brand-maroon transition-colors"
            >
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center gap-3">
          <a
            href={siteConfig.phoneHref}
            className="hidden text-sm font-semibold text-brand-maroon sm:block"
          >
            {siteConfig.phone}
          </a>
          <Link
            href="/request"
            className="rounded-full bg-brand-maroon px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand-maroon-dark"
          >
            Request Transportation
          </Link>
        </div>
      </Container>
      {/* Mobile nav: simple wrapped list, no JS menu needed at this content size */}
      <nav
        aria-label="Primary mobile"
        className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-black/5 px-4 py-2 text-sm font-medium md:hidden"
      >
        {navLinks.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className="text-foreground/80 hover:text-brand-maroon transition-colors"
          >
            {link.label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
