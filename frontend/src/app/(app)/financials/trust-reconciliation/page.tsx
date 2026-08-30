import type {Metadata} from "next";
import {Suspense} from "react";

export const metadata: Metadata = {
    title: "Trust Reconciliation | StrataOS",
    description: "Bank reconciliation and period lock management for trust accounting",
};
/**
 * @generated FunctionHeader
 * Function: TrustReconciliationPage
 * Path: frontend/src/app/(app)/financials/trust-reconciliation/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function TrustReconciliationPage() {
    const TrustReconciliationPageComponent = require("@/pages/dashboard/TrustReconciliationPage").default;
    return (
        <Suspense>
            <TrustReconciliationPageComponent/>
        </Suspense>
    );
}
