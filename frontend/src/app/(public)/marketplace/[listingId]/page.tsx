import type {Metadata, ResolvingMetadata} from 'next';
import {ListingDetailPage} from "@/pages/public/MarketplacePage";
import {truncateDescription} from '@/lib/utils';

const BACKEND_URL = process.env.BACKEND_INTERNAL_URL || process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8003';

type Props = {
    params: Promise<{ listingId: string }>;
};
/**
 * @generated FunctionHeader
 * Function: generateMetadata
 * Path: frontend/src/app/(public)/marketplace/[listingId]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export async function generateMetadata(
    {params}: Props,
    parent: ResolvingMetadata
): Promise<Metadata> {
    const {listingId} = await params;

    try {
        const response = await fetch(`${BACKEND_URL}/api/listings/${listingId}`);
        if (!response.ok) {
            console.error(`Failed to fetch listing ${listingId}: ${response.status}`);
            return {title: 'Community Marketplace Listing'};
        }
        const listing = await response.json();

        if (!listing) return {title: 'Listing Not Found'};

        const typeLabel = listing.listing_type === 'for_sale' ? 'Property for Sale' :
            listing.listing_type === 'for_rent' ? 'Property for Rent' :
                listing.listing_type === 'service' ? 'Service' : 'Item for Sale';

        const title = `${listing.title} | ${typeLabel}`;
        const description = truncateDescription(listing.description) || "View this community listing on East Gate Residences.";
        const image = listing.images?.[0] || "/images/east_gate_residences.jpg";

        return {
            title,
            description,
            keywords: [listing.listing_type, listing.title, "Denman Prospect", "strata marketplace"],
            openGraph: {
                title,
                description,
                images: [image],
                type: 'article',
            },
            twitter: {
                card: 'summary_large_image',
                title,
                description,
                images: [image],
            },
        };
    } catch (error) {
        console.error(`Error generating metadata for listing ${listingId}:`, error);
        return {title: 'Community Marketplace Listing'};
    }
}
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(public)/marketplace/[listingId]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return <ListingDetailPage/>;
}
