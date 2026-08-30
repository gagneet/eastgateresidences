import type {Metadata} from "next";
import {Suspense} from "react";
import MarketplacePage from "@/pages/public/MarketplacePage";
/**
 * @generated FunctionHeader
 * Function: generateMetadata
 * Path: frontend/src/app/(public)/marketplace/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export async function generateMetadata({searchParams}: {
    searchParams: Promise<{ [key: string]: string | string[] | undefined }>
}): Promise<Metadata> {
    const {location} = await searchParams;

    if (location) {
        const locName = String(location).toUpperCase();
        return {
            title: `${locName} Community Marketplace | Property & Strata Services`,
            description: `Explore townhouse sales, apartments for rent, and strata management services in ${locName}. Your local hub for community listings.`,
        };
    }

    return {
        title: "Community Marketplace | Property Sales, Rentals & Services",
        description: "Browse townhouses for sale, apartments for rent, and local services in the East Gate community and beyond. Your hub for strata living opportunities.",
        keywords: ["townhouse sales", "apartments rent", "apartments sales", "unit sales", "strata services", "garage sale"],
    };
}
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(public)/marketplace/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return (
        <Suspense fallback={<div className="min-h-screen p-20 text-center">Loading Marketplace...</div>}>
            <MarketplacePage/>
        </Suspense>
    );
}
