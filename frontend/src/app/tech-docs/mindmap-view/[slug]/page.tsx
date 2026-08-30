"use client";

// GAP-DOC-001 — Deep-link viewer for individual mindmap markdown files.
// Renders /tech-docs/mindmap/<slug>.md via MindmapDocViewer so mermaid blocks
// display as interactive SVG diagrams rather than raw code.
// Access restricted to super_admin to match the rest of /tech-docs/*.

import dynamic from "next/dynamic";
import {useParams, useRouter, notFound} from "next/navigation";
import {useEffect} from "react";
import {useAuth} from "@/contexts/AuthContext";
import {mindmapDocs} from "@/lib/mindmap/interactiveMindmapData";

const MindmapDocViewer = dynamic(
    () => import("@/components/tech-docs/MindmapDocViewer"),
    {ssr: false,
 loading: () => <div className="p-8 text-sm text-slate-400">Loading diagram…</div>}
);

// Allowlist: slug → MindmapDoc entry.  Any slug not present here returns 404.
const SLUG_MAP = Object.fromEntries(
    mindmapDocs.map((doc) => {
        const slug = doc.href.replace("/tech-docs/mindmap/", "").replace(".md", "");
        return [slug, doc];
    })
);
/**
 * @generated FunctionHeader
 * Function: MindmapViewPage
 * Path: frontend/src/app/tech-docs/mindmap-view/[slug]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function MindmapViewPage() {
    const params = useParams();
    const slug = (params?.slug as string) ?? "";
    const router = useRouter();
    const {isAdmin, loading} = useAuth() as any;

    useEffect(() => {
        if (loading) return;
        if (!isAdmin()) router.replace("/dashboard");
    }, [loading, isAdmin, router]);

    const doc = SLUG_MAP[slug];
    if (!doc) notFound();

    if (loading || !isAdmin()) return null;

    return (
        <div className="min-h-screen bg-slate-950 p-4">
            <MindmapDocViewer href={doc.href} label={doc.label} source={doc.source}/>
        </div>
    );
}
