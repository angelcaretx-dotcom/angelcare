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

/** How long Send waits for the usually-invisible check to finish. */
const WAIT_MS = 6000;

export interface TurnstileHandle {
  /**
   * The token, waiting a moment if the check is still running. "" when
   * Turnstile is off or couldn't load in this browser: the API then
   * allows a few codes an hour without it, so people aren't turned away.
   */
  token(): Promise<string>;
  /** Tokens are single-use: get a fresh one after each code request. */
  reset(): void;
}

export const Turnstile = forwardRef<TurnstileHandle>(function Turnstile(_props, ref) {
  const container = useRef<HTMLDivElement>(null);
  const widgetId = useRef<string | null>(null);
  const token = useRef("");
  // Loading, or showing a challenge the person is working on.
  const state = useRef<"loading" | "interactive" | "failed">("loading");

  useImperativeHandle(ref, () => ({
    token: async () => {
      if (!SITE_KEY) return "";
      const started = Date.now();
      while (!token.current && state.current !== "failed") {
        const waited = Date.now() - started;
        if (waited > (state.current === "interactive" ? 20 * WAIT_MS : WAIT_MS)) break;
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
      return token.current;
    },
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
            state.current = "loading";
          },
          "before-interactive-callback": () => {
            state.current = "interactive";
          },
          "error-callback": () => {
            state.current = "failed";
          },
          "unsupported-callback": () => {
            state.current = "failed";
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
        state.current = "failed";
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
