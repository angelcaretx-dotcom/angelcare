import type { Metadata } from "next";
import { type ReactNode } from "react";
import { Container } from "@/components/Container";

// Private, per-rider pages (ADR 0031): never indexed, never in the sitemap.
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function AccountLayout({ children }: { children: ReactNode }) {
  return <Container className="py-16 sm:py-24">{children}</Container>;
}
