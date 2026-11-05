"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

/**
 * Cloudflare Turnstile (ADR 0033): Cloudflare's "are you a person?"
 * check, usually invisible. Renders nothing unless
 * NEXT_PUBLIC_TURNSTILE_SITE_KEY is set; the API skips the check when
 * its secret key isn't set either.
 */

const SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "";
const SCRIPT_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

interface TurnstileApi {
  render(container: HTMLElement, options: Record<string, unknown>): string;
  reset(widgetId: string): void;
  remove(widgetId: string): void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

let scriptPromise: Promise<void> | null = null;

function loadScript(): Promise<void> {
  if (window.turnstile) return Promise.resolve();
  scriptPromise ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SCRIPT_SRC;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => {
      scriptPromise = null;
      reject(new Error("Turnstile failed to load"));
    };
    document.head.appendChild(script);
  });
  return scriptPromise;
}

export interface TurnstileHandle {
  /** The current token ("" when Turnstile is off or hasn't finished). */
  token(): string;
  /** Tokens are single-use: get a fresh one after each code request. */
  reset(): void;
}

export const Turnstile = forwardRef<TurnstileHandle>(function Turnstile(_props, ref) {
  const container = useRef<HTMLDivElement>(null);
  const widgetId = useRef<string | null>(null);
  const token = useRef("");

  useImperativeHandle(ref, () => ({
    token: () => token.current,
    reset: () => {
      token.current = "";
      if (widgetId.current && window.turnstile) window.turnstile.reset(widgetId.current);
    },
  }));

  useEffect(() => {
    if (!SITE_KEY) return;
    let cancelled = false;
    loadScript()
      .then(() => {
        if (cancelled || !container.current || !window.turnstile) return;
        widgetId.current = window.turnstile.render(container.current, {
          sitekey: SITE_KEY,
          callback: (value: string) => {
            token.current = value;
          },
          "expired-callback": () => {
            token.current = "";
          },
          "refresh-expired": "auto",
          appearance: "interaction-only",
        });
      })
      .catch(() => {
        // Blocked or offline: the API decides; the emailed code still applies.
      });
    return () => {
      cancelled = true;
      if (widgetId.current && window.turnstile) window.turnstile.remove(widgetId.current);
      widgetId.current = null;
    };
  }, []);

  if (!SITE_KEY) return null;
  return <div ref={container} className="min-h-0" />;
});
