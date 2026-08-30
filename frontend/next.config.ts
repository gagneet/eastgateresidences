import type {NextConfig} from "next";

// Static media served straight out of `public/`. Kept as one list so the "don't
// no-store this" rule and the "do cache this" rule can never drift apart — if they
// did, an extension would either lose its cache header or get two conflicting ones.
const CACHEABLE_ASSET_EXT = [
    "png", "jpg", "jpeg", "gif", "svg", "webp", "avif", "ico",
    "woff", "woff2", "ttf", "otf", "eot",
    "mp4", "webm", "mp3", "wav",
].join("|");

// Same list as a lookahead fragment for the no-store rule's negative match.
const NO_STORE_EXEMPT = `.*\\.(?:${CACHEABLE_ASSET_EXT})$`;

const nextConfig: NextConfig = {
    // Allow cross-origin requests from the backend domain
    async headers() {
        return [
            // Security headers on every response
            {
                source: "/(.*)",
                headers: [
                    {key: "X-Content-Type-Options", value: "nosniff"},
                    {key: "X-Frame-Options", value: "SAMEORIGIN"},
                    {key: "X-XSS-Protection", value: "1; mode=block"},
                    {key: "Referrer-Policy", value: "strict-origin-when-cross-origin"},
                ],
            },
            // Never cache HTML pages.
            // After each production build, JS chunks get new content-hashes and the
            // old files are deleted. If Cloudflare or the browser caches the HTML page
            // (which embeds the old chunk URLs) the browser will request files that no
            // longer exist → 404 returned as text/html → ChunkLoadError /
            // MIME-type mismatch with X-Content-Type-Options: nosniff.
            // Static assets (/_next/static/) are fine to cache forever because their
            // file names already include a content hash.
            //
            // The extension list below is excluded because this rule used to catch
            // every file in `public/` too — logos, icons, fonts, screenshots — so they
            // carried `no-store` and were re-fetched on EVERY navigation. That is the
            // opposite of the intent: the ChunkLoadError risk comes from stale HTML
            // embedding dead chunk URLs, and an image cannot embed anything.
            // Measured 2026-08-24: `/eastgate-logo.png` re-downloaded on every page.
            {
                source: `/((?!_next/static|_next/image|favicon\\.ico|${NO_STORE_EXEMPT}).*)`,
                headers: [
                    {key: "Cache-Control", value: "no-store, must-revalidate"},
                ],
            },
            // Static media in `public/`. Unlike `/_next/static/`, these filenames carry
            // no content hash, so they cannot be `immutable` — replacing a logo would
            // otherwise be invisible until the cache expired. One day of hard caching
            // plus a week of stale-while-revalidate means a repeat visitor re-downloads
            // nothing, and a changed asset propagates within a day (immediately on the
            // next background revalidation).
            //
            // The durable fix is content-hashed URLs — route these through `next/image`
            // or a build step — at which point this can become `immutable`.
            {
                source: `/:path*.:ext(${CACHEABLE_ASSET_EXT})`,
                headers: [
                    {
                        key: "Cache-Control",
                        value: "public, max-age=86400, stale-while-revalidate=604800",
                    },
                ],
            },
        ];
    },

    // Turbopack root configuration (silences the lockfile warning)
    turbopack: {
        root: __dirname,
    },

    // Allow images from the backend domain
    images: {
        remotePatterns: [
            {
                protocol: "https",
                hostname: "eastgateresidences.com.au",
            },
            {
                protocol: "http",
                hostname: "localhost",
            },
        ],
    },

    // Disable x-powered-by header
    poweredByHeader: false,

    // Strict mode for better error detection
    reactStrictMode: true,

    // Compress output
    compress: true,
};

export default nextConfig;
