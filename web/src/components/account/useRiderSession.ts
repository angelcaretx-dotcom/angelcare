"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { riderApi, RiderApiError, type RiderProfile } from "@/lib/rider";

const ACTIVITY_EVENTS = ["pointerdown", "keydown", "scroll", "touchstart"] as const;

/**
 * Loads the signed-in rider, or sends the visitor to sign in. Signs out
 * after the server's idle timeout with no activity -- and takes trip
 * details off the screen -- because riders and caregivers share tablets
 * and facility computers (ADR 0031).
 */
export function useRiderSession() {
  const router = useRouter();
  const [profile, setProfile] = useState<RiderProfile | null>(null);
  const lastActivity = useRef(0);

  const toLogin = useCallback(
    (reason?: "expired") => {
      const params = new URLSearchParams({ next: window.location.pathname });
      if (reason) params.set("expired", "1");
      router.replace(`/account/login?${params}`);
    },
    [router],
  );

  /** For callers: a signed-out error means the server session ended. */
  const handleError = useCallback(
    (error: unknown): string => {
      if (error instanceof RiderApiError && error.signedOut) {
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
    riderApi
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
        riderApi.logout().catch(() => {});
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
    await riderApi.logout().catch(() => {});
    router.replace("/account/login");
  }, [router]);

  return { profile, setProfile, handleError, signOut };
}
