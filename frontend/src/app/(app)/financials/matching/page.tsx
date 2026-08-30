// @featuretrace:financial_matching — App Router thin wrapper for the payment matching review queue page.
// Layer: frontend
// Data flow: delegates entirely to MatchingReviewPage → /financial/matching/* (building-scoped).
// Related: frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx
//           backend/routers/financial_matching.py
"use client";
import MatchingReviewPage from "@/pages/dashboard/financial/MatchingReviewPage";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/financials/matching/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return <MatchingReviewPage/>;
}
