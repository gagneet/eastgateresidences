"use client";

import {Suspense} from "react";
import FeatureGuard from "@/components/layout/FeatureGuard";
import CapitalFundingElectionsPage from "@/pages/dashboard/CapitalFundingElectionsPage";

export default function Page() {
    return (
        <Suspense>
            <FeatureGuard featureKey="capital_funding_owner_elections">
                <CapitalFundingElectionsPage/>
            </FeatureGuard>
        </Suspense>
    );
}
