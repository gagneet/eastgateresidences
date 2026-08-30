"use client";
import {Suspense} from "react";
import OccupancyIntelligencePage from "@/pages/dashboard/OccupancyIntelligencePage";
import FeatureGuard from "@/components/layout/FeatureGuard";
import {Loader2} from "lucide-react";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/intelligence/occupancy/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return (
        <Suspense fallback={
            <div className="flex items-center justify-center min-h-[400px]">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground"/>
            </div>
        }>
            <FeatureGuard featureKey="occupancy_intelligence">
                <OccupancyIntelligencePage/>
            </FeatureGuard>
        </Suspense>
    );
}
