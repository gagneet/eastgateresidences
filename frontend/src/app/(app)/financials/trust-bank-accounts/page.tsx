import type {Metadata} from "next";
import {Suspense} from "react";

export const metadata: Metadata = {
    title: "Trust Account & Bank Accounts | StrataOS",
    description: "Manage trust account bank settings, interest rates, advance levy payment tracking and monthly interest income posting.",
};
/**
 * @generated FunctionHeader
 * Function: TrustBankAccountsPage
 * Path: frontend/src/app/(app)/financials/trust-bank-accounts/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function TrustBankAccountsPage() {
    const TrustBankAccountsPageComponent = require("@/pages/dashboard/TrustBankAccountsPage").default;
    return (
        <Suspense>
            <TrustBankAccountsPageComponent/>
        </Suspense>
    );
}
