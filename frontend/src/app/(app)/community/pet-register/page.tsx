"use client";
import PetRegisterPage from "@/pages/dashboard/PetRegisterPage";
import FeatureGuard from "@/components/layout/FeatureGuard";
// GAP-FT-006: pet-register/page.tsx was fully unguarded — any authenticated
// user who knew the URL could view and manage all pet registrations regardless
// of role or feature state. The 'pet_register' toggle existed in the seed but
// controlled nothing. FeatureGuard blocks the page when the toggle is off.
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/community/pet-register/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return (
        <FeatureGuard featureKey="pet_register">
            <PetRegisterPage/>
        </FeatureGuard>
    );
}
