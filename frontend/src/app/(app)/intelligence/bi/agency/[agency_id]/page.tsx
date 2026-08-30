import AgencyBIAnalyticsPage from "@/pages/dashboard/AgencyBIAnalyticsPage";

interface Props {
    params: { agency_id: string };
}
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/intelligence/bi/agency/[agency_id]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page({ params }: Props) {
    return <AgencyBIAnalyticsPage agencyId={params.agency_id} />;
}
