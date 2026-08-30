"use client";
import {Suspense} from "react";
import FinancePage from "@/pages/dashboard/FinancePage";
import FeatureGuard from "@/components/layout/FeatureGuard";

export default function Page() {
    return (
        <Suspense>
            <FeatureGuard featureKey="finance">
                <FinancePage/>
            </FeatureGuard>
        </Suspense>
    );
}
