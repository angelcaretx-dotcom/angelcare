import Image from "next/image";
import Link from "next/link";
import { siteConfig } from "@/lib/site-config";

/**
 * Real logo artwork (public/logo.svg). The mark's colors are decorative
 * logo artwork, not live text, so they're exempt from WCAG text-contrast
 * minimums — see the note in globals.css for why interactive text/buttons
 * use the separate "-dark" brand tokens instead of these exact tones.
 */
export function Logo() {
  return (
    <Link
      href="/"
      className="flex shrink-0 items-center gap-2 whitespace-nowrap"
      aria-label={`${siteConfig.name} home`}
    >
      <Image src="/logo.svg" alt="" width={36} height={34} priority />
      <span className="text-base font-bold tracking-tight text-foreground sm:text-lg">
        {siteConfig.name}
      </span>
    </Link>
  );
}
