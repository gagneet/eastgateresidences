"use client";
import {useParams} from "next/navigation";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const StaffUserDetailPage = require("@/pages/dashboard/admin/StaffUserDetailPage").default as any;
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/admin/staff-users/[id]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    const params = useParams();
    return <StaffUserDetailPage userId={params?.id}/>;
}
