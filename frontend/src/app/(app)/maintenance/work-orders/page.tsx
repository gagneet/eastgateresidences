"use client";
import MaintenancePage from "@/pages/dashboard/MaintenancePage";
import FeatureGuard from "@/components/layout/FeatureGuard";
/**
 * Canonical nested route for the Maintenance "Work Orders" tab.
 * Replaces the legacy /maintenance?tab=work-orders deep-link (still honoured).
 */
export default function Page() {
    return (
        <FeatureGuard featureKey="maintenance">
            <MaintenancePage initialTab="work-orders"/>
        </FeatureGuard>
    );
}
