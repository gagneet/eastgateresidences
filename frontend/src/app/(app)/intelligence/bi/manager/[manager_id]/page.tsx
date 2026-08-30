import ManagerBIAnalyticsPage from "@/pages/dashboard/ManagerBIAnalyticsPage";

interface Props {
    params: { manager_id: string };
}
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/intelligence/bi/manager/[manager_id]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page({ params }: Props) {
    return <ManagerBIAnalyticsPage strataManagerId={params.manager_id} />;
}
