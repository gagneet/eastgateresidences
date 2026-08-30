"use client";
import OCRPage from "@/pages/dashboard/OCRPage";
import FeatureGuard from "@/components/layout/FeatureGuard";

export default function Page() {
    return (
        <FeatureGuard featureKey="document_ocr">
            <OCRPage/>
        </FeatureGuard>
    );
}

