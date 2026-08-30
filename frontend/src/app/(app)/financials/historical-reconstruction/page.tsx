// @featuretrace:demo_bank — App Router thin wrapper for the historical reconstruction workflow page.
// Layer: frontend
// Data flow: delegates entirely to HistoricalReconstructionPage → /financials/onboarding/* (building-scoped).
// Related: frontend/src/pages/dashboard/financial/HistoricalReconstructionPage.tsx
//           backend/routers/financial_onboarding.py
"use client";
import HistoricalReconstructionPage from "@/pages/dashboard/financial/HistoricalReconstructionPage";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/financials/historical-reconstruction/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return <HistoricalReconstructionPage/>;
}
