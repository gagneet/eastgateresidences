// @featuretrace:ap_automation — App Router thin wrapper for the supplier invoice upload page.
// Layer: frontend
// Data flow: delegates entirely to InvoiceUploadPage (pages/supplier/).
// Related: frontend/src/pages/supplier/InvoiceUploadPage.tsx
//           backend/routers/ap_supplier_upload.py
// Scope: (building-scoped)
"use client";
import InvoiceUploadPage from "../../../pages/supplier/InvoiceUploadPage";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/supplier/invoice-upload/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return <InvoiceUploadPage/>;
}