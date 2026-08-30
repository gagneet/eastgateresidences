"use client";
// Suburb Radar is the tenant-facing view of Market Intelligence.
// Renders the same underlying market-intelligence content via redirect.
import {useEffect} from "react";
import {useRouter} from "next/navigation";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/intelligence/suburb-radar/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    const router = useRouter();
    useEffect(() => {
        router.replace("/intelligence/market");
    }, [router]);
    return null;
}
