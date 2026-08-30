// @featuretrace:invoices — App Router entry point: renders InvoicesPage at /financials/invoices.
// Layer: frontend
// Data flow: page.tsx → InvoicesPage → GET /invoices → invoice_documents (building-scoped).
// Related: frontend/src/pages/dashboard/InvoicesPage.jsx
//           backend/routers/invoices.py
"use client";
import InvoicesPage from "@/pages/dashboard/InvoicesPage";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/financials/invoices/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return <InvoicesPage/>;
}
