// @featuretrace:demo_bank — App Router wrapper for financial-only onboarding (building-scoped).
// @featuretrace:financial-onboarding — App Router route for financial-only onboarding (building-scoped).
// Layer: frontend
// Data flow: delegates to FinancialOnboardingPage → HistoricalReconstructionPage → Demo Bank reconstruction/sync APIs.
// Related: frontend/src/pages/dashboard/admin/FinancialOnboardingPage.tsx

"use client";

import FinancialOnboardingPage from "@/pages/dashboard/admin/FinancialOnboardingPage";

export default function Page() {
    return <FinancialOnboardingPage/>;
}
