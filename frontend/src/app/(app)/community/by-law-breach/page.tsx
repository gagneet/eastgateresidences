// @featuretrace:by-law-breach-register — Route shell for the dispute register page.
// Layer: frontend
// Data flow: /community/by-law-breach -> ByLawBreachPage -> /by-law-breach/reports
//            -> by_law_breach_reports (building-scoped).
// Related: frontend/src/pages/dashboard/ByLawBreachPage.tsx
//          frontend/src/components/layout/DashboardLayout.tsx  (nav entry + by_law_breach toggle)
"use client";
import ByLawBreachPage from "@/pages/dashboard/ByLawBreachPage";

export default function Page() {
    return <ByLawBreachPage/>;
}
