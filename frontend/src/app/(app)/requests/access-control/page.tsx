"use client";
import RequestsPage from "@/pages/dashboard/RequestsPage";
import FeatureGuard from "@/components/layout/FeatureGuard";
/**
 * Canonical nested route for the "Keys, Fobs & Access" request form
 * (admin_staff "Key register" nav item). Replaces the old /admin mispoint.
 * Guarded by the same 'requests' feature toggle as /requests.
 */
export default function Page() {
    return (
        <FeatureGuard featureKey="requests">
            <RequestsPage initialTab="access-control"/>
        </FeatureGuard>
    );
}
