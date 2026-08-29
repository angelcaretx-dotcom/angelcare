import Image from "next/image";
import { type ReactNode } from "react";

/**
 * Full-width banner: a marketing photo (owner-provided, real AngelCare
 * imagery — see docs/business-decisions-log.md) as a background, with
 * its left side faded/blurred into the page background so real, live
 * HTML content can sit on top without colliding with the baked-in
 * logo/headline/icons/button already in that photo's left half.
 *
 * The blur layer intentionally reuses the exact same next/image src +
 * sizes as the sharp base layer, so both resolve to the same optimized
 * URL and the browser serves the second one from cache -- no doubled
 * image download for the blur effect.
 */
export function ImageBanner({
  src,
  alt,
  children,
  fadeColor = "var(--background)",
}: {
  src: string;
  alt: string;
  children: ReactNode;
  /** CSS color the left side fades into. Defaults to the page background. */
  fadeColor?: string;
}) {
  const sizes = "(max-width: 1024px) 100vw, 1024px";

  return (
    <div className="relative overflow-hidden rounded-2xl">
      <div className="relative min-h-90 w-full sm:aspect-1672/941 sm:min-h-0">
        <Image src={src} alt={alt} fill sizes={sizes} className="object-cover" />

        {/* Blurred duplicate, masked to the left, fading out toward the right */}
        <Image
          src={src}
          alt=""
          aria-hidden="true"
          fill
          sizes={sizes}
          className="object-cover blur-2xl"
          style={{
            maskImage: "linear-gradient(to right, black 0%, black 45%, transparent 75%)",
            WebkitMaskImage:
              "linear-gradient(to right, black 0%, black 45%, transparent 75%)",
          }}
        />

        {/* Solid-to-transparent color scrim, so the faded area blends
            into the page background instead of just looking dimmed */}
        <div
          className="absolute inset-0"
          style={{
            background: `linear-gradient(to right, ${fadeColor} 0%, ${fadeColor} 32%, transparent 68%)`,
          }}
        />

        <div
          className="relative z-10 flex h-full flex-col justify-center gap-4 p-6 sm:p-10"
          style={{ maxWidth: "min(28rem, 60%)" }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}
