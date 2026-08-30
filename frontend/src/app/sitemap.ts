import {MetadataRoute} from 'next';
import {SITE_URL as SITE_URL_CONFIG} from '@/lib/siteConfig';

const BACKEND_URL = process.env.BACKEND_INTERNAL_URL || process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8003';
// Canonical origin comes from config, never a tenant's hostname baked into shared code.
const SITE_URL = SITE_URL_CONFIG;

export const dynamic = 'force-dynamic';
/**
 * @generated FunctionHeader
 * Function: sitemap
 * Path: frontend/src/app/sitemap.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
    // Static routes
    const routes = [
        '',
        '/about',
        '/blog',
        '/marketplace',
        '/emergency-services',
        '/privacy',
        '/terms',
        '/login',
        '/register',
    ].map((route) => ({
        url: `${SITE_URL}${route}`,
        lastModified: '2026-02-26',
        changeFrequency: 'weekly' as const,
        priority: route === '' ? 1 : 0.8,
    }));

    try {
        // Dynamic routes - Listings (scope=global returns public listings across all buildings)
        const listingsRes = await fetch(`${BACKEND_URL}/api/listings?scope=global&status=active`, {cache: 'no-store'});
        if (!listingsRes.ok) throw new Error(`Failed to fetch listings: ${listingsRes.status}`);
        const listings = await listingsRes.json();

        // Limit to top 1000 items and ensure we handle non-array responses
        const listingRoutes = Array.isArray(listings) ? listings
            .filter((l: any) => l.is_public)
            .slice(0, 1000)
            .map((l: any) => ({
                url: `${SITE_URL}/marketplace/${l.id}`,
                lastModified: l.updated_at || l.created_at || '2026-02-26',
                changeFrequency: 'daily' as const,
                priority: 0.6,
            })) : [];

        // Dynamic routes - Blog posts
        const blogRes = await fetch(`${BACKEND_URL}/api/blog`, {cache: 'no-store'});
        if (!blogRes.ok) throw new Error(`Failed to fetch blog posts: ${blogRes.status}`);
        const posts = await blogRes.json();

        const blogRoutes = Array.isArray(posts) ? posts
            .filter((p: any) => p.is_published)
            .slice(0, 1000)
            .map((p: any) => ({
                url: `${SITE_URL}/blog/${p.id}`,
                lastModified: p.updated_at || p.created_at || '2026-02-26',
                changeFrequency: 'monthly' as const,
                priority: 0.5,
            })) : [];

        // Dynamic routes - Public building intelligence reports
        let buildingRoutes: MetadataRoute.Sitemap = [];
        try {
            const buildingsRes = await fetch(`${BACKEND_URL}/api/auth/buildings`, {cache: 'no-store'});
            if (buildingsRes.ok) {
                const buildings = await buildingsRes.json();
                buildingRoutes = Array.isArray(buildings)
                    ? buildings
                        .filter((b: any) => b.slug)
                        .map((b: any) => ({
                            url: `${SITE_URL}/building/${b.slug}`,
                            lastModified: '2026-02-26',
                            changeFrequency: 'weekly' as const,
                            priority: 0.7,
                        }))
                    : [];
            }
        } catch {
            // Building routes are optional; continue without them
        }

        return [...routes, ...listingRoutes, ...blogRoutes, ...buildingRoutes];
    } catch (error) {
        console.error('Sitemap generation failed:', error);
        return routes;
    }
}
