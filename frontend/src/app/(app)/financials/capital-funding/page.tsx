"use client";

import {Suspense} from "react";
import FeatureGuard from "@/components/layout/FeatureGuard";
import CapitalFundingPage from "@/pages/dashboard/CapitalFundingPage";

export default function Page() {
    return (
        <Suspense>
            <FeatureGuard featureKey="capital_funding_workspace">
                <CapitalFundingPage/>
            </FeatureGuard>
        </Suspense>
    );
}
