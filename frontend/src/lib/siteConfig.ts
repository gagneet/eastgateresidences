/**
 * Public site identity — the canonical origin this deployment serves under.
 *
 * WHY THIS EXISTS
 * ---------------
 * `https://eastgateresidences.com.au` was hardcoded across SEO/metadata surfaces
 * (`app/layout.tsx` metadataBase + openGraph, `app/sitemap.ts`, `app/robots.ts`,
 * `components/shared/StructuredData.tsx`). StrataOS is multi-tenant: every strata
 * organisation — and potentially every building — can have its own domain or
 * sub-domain, so one building's hostname baked into shared code is wrong for every
 * other tenant, and leaks East Gate's branding into their sitemap, canonical URLs,
 * Open Graph cards and JSON-LD.
 *
 * These values are read at BUILD time (Next inlines `NEXT_PUBLIC_*`), so they
 * describe the deployment, not the request. Per-request/per-tenant resolution — a
 * true multi-domain setup where one deployment serves many hostnames — is a larger
 * change tracked in `tasks/GAP-ARCH-005-multi-tenant-domain-configuration.md`.
 *
 * Configure in `frontend/.env.local`:
 *   NEXT_PUBLIC_SITE_URL=https://your-building.example.com
 *   NEXT_PUBLIC_SITE_NAME="Your Building"
 */

/** Canonical origin, no trailing slash. */
export const SITE_URL: string = (
    process.env.NEXT_PUBLIC_SITE_URL ||
    process.env.NEXT_PUBLIC_FRONTEND_URL ||
    "http://localhost:3020"
).replace(/\/$/, "");

/** Display name used in titles, Open Graph and structured data. */
export const SITE_NAME: string = process.env.NEXT_PUBLIC_SITE_NAME || "StrataOS";

/** Bare hostname (no scheme), for copy that shows a domain rather than a link. */
export const SITE_DOMAIN: string = (() => {
    try {
        return new URL(SITE_URL).host;
    } catch {
        return SITE_URL.replace(/^https?:\/\//, "");
    }
})();

/** Absolute URL for a site-relative path — required by Open Graph and JSON-LD. */
export function absoluteUrl(path: string): string {
    return `${SITE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}
