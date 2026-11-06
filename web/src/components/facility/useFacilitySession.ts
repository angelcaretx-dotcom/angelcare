"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { facilityApi, FacilityApiError, type FacilityProfile } from "@/lib/facility";

const ACTIVITY_EVENTS = ["pointerdown", "keydown", "scroll", "touchstart"] as const;

/**
 * Loads the signed-in facility user, or sends the visitor to sign in.
 * Signs out after the server's idle timeout with no activity and clears
 * patients' rides from the screen: facility workstations are shared (ADR 0042).
 */
export function useFacilitySession() {
  const router = useRouter();
  const [profile, setProfile] = useState<FacilityProfile | null>(null);
  const lastActivity = useRef(0);

  const toLogin = useCallback(
    (reason?: "expired") => {
      const params = new URLSearchParams({ next: window.location.pathname });
      if (reason) params.set("expired", "1");
      router.replace(`/facility/login?${params}`);
    },
    [router],
  );

  const handleError = useCallback(
    (error: unknown): string => {
      if (error instanceof FacilityApiError && error.signedOut) {
        setProfile(null);
        toLogin("expired");
        return "";
      }
      return error instanceof Error ? error.message : "Something went wrong.";
    },
    [toLogin],
  );

  useEffect(() => {
    let cancelled = false;
    facilityApi
      .me()
      .then((me) => {
        if (!cancelled) setProfile(me);
      })
      .catch(() => {
        if (!cancelled) toLogin();
      });
    return () => {
      cancelled = true;
    };
  }, [toLogin]);

  useEffect(() => {
    if (!profile) return;
    lastActivity.current = Date.now();
    const touch = () => {
      lastActivity.current = Date.now();
    };
    ACTIVITY_EVENTS.forEach((e) => window.addEventListener(e, touch, { passive: true }));
    const timer = window.setInterval(() => {
      if (Date.now() - lastActivity.current > profile.idle_timeout_seconds * 1000) {
        setProfile(null);
        facilityApi.logout().catch(() => {});
        toLogin("expired");
      }
    }, 15_000);
    return () => {
      ACTIVITY_EVENTS.forEach((e) => window.removeEventListener(e, touch));
      window.clearInterval(timer);
    };
  }, [profile, toLogin]);

  const signOut = useCallback(async () => {
    setProfile(null);
    await facilityApi.logout().catch(() => {});
    router.replace("/facility/login");
  }, [router]);

  return { profile, handleError, signOut };
}
