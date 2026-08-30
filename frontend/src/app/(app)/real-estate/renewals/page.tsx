"use client";
import RealEstateDashboard from "@/pages/dashboard/RealEstateDashboard";
/**
 * Canonical nested route for the Real Estate "Lease renewals" tab.
 * Replaces the legacy /real-estate?tab=renewals deep-link (still honoured).
 */
export default function Page() {
    return <RealEstateDashboard initialTab="renewals"/>;
}
