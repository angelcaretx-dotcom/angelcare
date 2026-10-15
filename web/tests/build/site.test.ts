/**
 * Checks against the real prerendered HTML from `next build` (ADR 0022)
 * -- what visitors and search engines actually receive, not a component
 * rendered in isolation. Run `npm run build` first.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { JSDOM } from "jsdom";
import { describe, expect, it } from "vitest";
import sitemap from "@/app/sitemap";
import { siteConfig } from "@/lib/site-config";

const APP_DIR = join(__dirname, "../../.next/server/app");
const PUBLIC_DIR = join(__dirname, "../../public");

if (!existsSync(APP_DIR)) {
  throw new Error(`No build output at ${APP_DIR} -- run \`npm run build\` first.`);
}

// Framework-generated error pages, not site content.
const FRAMEWORK_PAGES = new Set(["_not-found.html", "_global-error.html"]);

const routes = sitemap().map((entry) => new URL(entry.url).pathname);

function fileForRoute(route: string): string {
  return route === "/" ? "index.html" : `${route.slice(1)}.html`;
}

function load(file: string): Document {
  return new JSDOM(readFileSync(join(APP_DIR, file), "utf8")).window.document;
}

/**
 * Visible text, one text node at a time joined by spaces. (`textContent`
 * glues adjacent elements together -- "Phone817-766-9228Email..." -- and
 * includes the RSC payload inside <script> tags.)
 */
function visibleText(doc: Document): string {
  const walker = doc.createTreeWalker(doc.body, 4 /* NodeFilter.SHOW_TEXT */);
  const parts: string[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const tag = node.parentElement?.tagName;
    if (tag !== "SCRIPT" && tag !== "STYLE") parts.push(node.textContent ?? "");
  }
  return parts.join(" ");
}

const pages = routes.map((route) => ({ route, file: fileForRoute(route) }));

describe("sitemap", () => {
  it("lists every prerendered page, and every listed page exists", () => {
    const built = readdirSync(APP_DIR)
      .filter((f) => f.endsWith(".html") && !FRAMEWORK_PAGES.has(f))
      .sort();
    expect(built).toEqual(pages.map((p) => p.file).sort());
  });

  it("uses the canonical site URL", () => {
    for (const entry of sitemap()) {
      expect(entry.url.startsWith(siteConfig.url)).toBe(true);
    }
  });
});

describe.each(pages)("$route", ({ file }) => {
  const doc = load(file);

  it("has a title and a meta description", () => {
    expect(doc.title.trim()).not.toBe("");
    expect(doc.querySelector('meta[name="description"]')?.getAttribute("content")?.trim()).toBeTruthy();
  });

  it("has exactly one h1", () => {
    expect(doc.querySelectorAll("h1")).toHaveLength(1);
  });

  it("gives every image an alt attribute", () => {
    const missing = [...doc.querySelectorAll("img")]
      .filter((img) => !img.hasAttribute("alt"))
      .map((img) => img.getAttribute("src"));
    expect(missing).toEqual([]);
  });

  it("has no internal links to pages or files that don't exist", () => {
    const broken = [...doc.querySelectorAll('a[href^="/"]')]
      .map((a) => a.getAttribute("href")!.split(/[?#]/)[0])
      .filter((path) => !routes.includes(path) && !existsSync(join(PUBLIC_DIR, path)));
    expect(broken).toEqual([]);
  });

  it("only uses the real AngelCare phone number and email", () => {
    // Never-invent-facts guard: every contact detail must come from
    // site-config.ts (sourced from docs/business-decisions-log.md).
    const hrefs = [...doc.querySelectorAll("a[href]")].map((a) => a.getAttribute("href")!);
    for (const tel of hrefs.filter((h) => h.startsWith("tel:"))) {
      expect(tel).toBe(siteConfig.phoneHref);
    }
    for (const mail of hrefs.filter((h) => h.startsWith("mailto:"))) {
      expect(mail).toBe(siteConfig.emailHref);
    }
    const text = visibleText(doc);
    const phones = text.match(/\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b/g) ?? [];
    // 911 is mentioned (emergency disclaimer) but never in this format.
    expect([...new Set(phones)].filter((p) => p !== siteConfig.phone)).toEqual([]);
    const emails = text.match(/[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g) ?? [];
    expect([...new Set(emails)].filter((e) => e !== siteConfig.email)).toEqual([]);
  });
});

describe("page titles", () => {
  it("are unique across the site", () => {
    const titles = pages.map(({ file }) => load(file).title);
    expect(new Set(titles).size).toBe(titles.length);
  });
});

describe("/request", () => {
  it("ships the trip request form with every field the API needs", () => {
    const form = load("request.html").querySelector("form");
    expect(form).not.toBeNull();
    const names = [...form!.querySelectorAll("[name]")].map((el) => el.getAttribute("name"));
    for (const name of [
      "full_name",
      "phone",
      "email",
      "pickup_address",
      "dropoff_address",
      "pickup_date",
      "pickup_time",
      "service_type",
    ]) {
      expect(names).toContain(name);
    }
  });
});
