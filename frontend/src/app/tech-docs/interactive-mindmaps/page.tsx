"use client";

import dynamic from "next/dynamic";

const ArchitectureConsole = dynamic(
    () => import("@/components/tech-docs/ArchitectureConsole"),
    {ssr: false}
);
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/tech-docs/interactive-mindmaps/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return <ArchitectureConsole/>;
}
