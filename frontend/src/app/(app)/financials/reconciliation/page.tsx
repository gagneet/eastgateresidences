import type {Metadata} from "next";
import {Suspense} from "react";

export const metadata: Metadata = {
    title: "Finance Reconciliation | StrataOS",
    description: "Portal vs ledger balance reconciliation, per unit, with candidate/input transaction status",
};
/**
 * @generated FunctionHeader
 * Function: ReconciliationRoutePage
 * Path: frontend/src/app/(app)/financials/reconciliation/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function ReconciliationRoutePage() {
    const ReconciliationPageComponent = require("@/pages/dashboard/financial/ReconciliationPage").default;
    return (
        <Suspense>
            <ReconciliationPageComponent/>
        </Suspense>
    );
}
