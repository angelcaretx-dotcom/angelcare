import Link from "next/link";
import { Logo } from "@/components/Logo";
import { siteConfig } from "@/lib/site-config";

const navLinks = [
  { href: "/services", label: "Services" },
  { href: "/how-it-works", label: "How It Works" },
  { href: "/service-area", label: "Service Area" },
  { href: "/about", label: "About" },
  { href: "/contact", label: "Contact" },
];

const linkClass = "text-foreground/80 hover:text-brand-blue-dark transition-colors";

/**
 * Two layouts, because the logo, five links, "My trips", the phone number
 * and the request button need about 1,010px on one line and the page
 * column is 960px wide:
 * - 1280px and wider: one row, in a slightly wider column than the page.
 * - narrower: the links move to a second row under the logo row.
 * Nothing in the top row may wrap from the `sm` width up: a wrapped phone
 * number or button is what made the items run into each other.
 * On phones the top row is only the logo and a shorter request button;
 * "My trips" joins the links in the second row.
 */
export function Header() {
  return (
    <header className="border-b border-black/10 bg-white">
      <div className="mx-auto flex min-h-16 w-full max-w-5xl items-center justify-between gap-4 px-4 sm:px-6 lg:px-8 xl:max-w-6xl">
        <Logo />
        <nav
          aria-label="Primary"
          className="hidden items-center gap-6 whitespace-nowrap text-sm font-medium xl:flex"
        >
          {navLinks.map((link) => (
            <Link key={link.href} href={link.href} className={linkClass}>
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center gap-3 sm:whitespace-nowrap">
          <Link href="/account" className={`hidden text-sm font-medium sm:inline ${linkClass}`}>
            My trips
          </Link>
          <a
            href={siteConfig.phoneHref}
            className="hidden text-sm font-semibold text-brand-blue-dark sm:block"
          >
            {siteConfig.phone}
          </a>
          <Link
            href="/request"
            className="rounded-full bg-brand-blue-dark px-4 py-2 text-center text-sm font-semibold text-white transition-colors hover:brightness-90"
          >
            <span className="sm:hidden">Request a Ride</span>
            <span className="hidden sm:inline">Request Transportation</span>
          </Link>
        </div>
      </div>
      {/* Below 1280px: the links as a second row, wrapping if they must. No JS menu needed at this content size. */}
      <nav aria-label="Primary" className="border-t border-black/5 xl:hidden">
        <div className="mx-auto flex w-full max-w-5xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2 text-sm font-medium sm:px-6 lg:px-8">
          {navLinks.map((link) => (
            <Link key={link.href} href={link.href} className={`whitespace-nowrap ${linkClass}`}>
              {link.label}
            </Link>
          ))}
          <Link href="/account" className={`whitespace-nowrap sm:hidden ${linkClass}`}>
            My trips
          </Link>
        </div>
      </nav>
    </header>
  );
}
