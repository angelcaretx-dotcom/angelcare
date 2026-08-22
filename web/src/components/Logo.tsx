import Link from "next/link";

/**
 * Text wordmark used until the provided logo file (pasted in chat, not yet
 * saved to disk) is added at `public/logo.png`. Once that file exists,
 * swap this for a next/image render of it.
 */
export function Logo() {
  return (
    <Link
      href="/"
      className="flex items-center gap-2 text-lg font-bold tracking-tight"
      aria-label="AngelCare Transit home"
    >
      <span className="text-brand-maroon">Angel</span>
      <span className="text-brand-cyan">Care</span>
      <span className="text-foreground/70 font-medium">Transit</span>
    </Link>
  );
}
