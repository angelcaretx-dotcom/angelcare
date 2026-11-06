import type { Metadata } from "next";
import { type ReactNode } from "react";
import { Container } from "@/components/Container";

// The facility portal (ADR 0042): private, never indexed, never in the sitemap.
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function FacilityLayout({ children }: { children: ReactNode }) {
  return <Container className="py-16 sm:py-24">{children}</Container>;
}
