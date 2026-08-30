import PowerhouseWorkflowInstancePage from "@/pages/dashboard/powerhouse/PowerhouseWorkflowInstancePage";

type PageProps = {
  params: { instanceId: string };
};
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/powerhouse/workflows/[instanceId]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page({ params }: PageProps) {
  return <PowerhouseWorkflowInstancePage instanceId={params.instanceId} />;
}

