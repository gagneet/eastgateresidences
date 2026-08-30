import PowerhouseThreadDetailPage from "@/pages/dashboard/powerhouse/PowerhouseThreadDetailPage";

type PageProps = {
  params: { threadId: string };
};
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/powerhouse/thread/[threadId]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page({ params }: PageProps) {
  return <PowerhouseThreadDetailPage threadId={params.threadId} />;
}

