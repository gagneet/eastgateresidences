import {MetadataRoute} from 'next';
import {absoluteUrl} from '@/lib/siteConfig';
/**
 * @generated FunctionHeader
 * Function: robots
 * Path: frontend/src/app/robots.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function robots(): MetadataRoute.Robots {
    return {
        rules: {
            userAgent: '*',
            allow: '/',
            disallow: [
                '/dashboard',
                '/api/',
                '/_next/',
                '/static/',
            ],
        },
        sitemap: absoluteUrl('/sitemap.xml'),
    };
}
