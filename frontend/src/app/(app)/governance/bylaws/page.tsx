"use client";
import React, {useEffect, useState} from "react";
import {useAuth} from "@/contexts/AuthContext";
import {Card, CardContent} from "@/components/ui/card";
import {Badge} from "@/components/ui/badge";
import {Input} from "@/components/ui/input";
import {Skeleton} from "@/components/ui/skeleton";
import {Button} from "@/components/ui/button";
import {
    Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription
} from "@/components/ui/dialog";
import {BookOpen, Search, X} from "lucide-react";
import Link from "next/link";

// Section icon mapping by keyword in title
const SECTION_ICONS: Record<string, string> = {
    conduct: "🏠",
    parking: "🚗",
    vehicle: "🚗",
    waste: "♻️",
    recycl: "♻️",
    pet: "🐾",
    appearance: "🏗️",
    renovation: "🏗️",
    ameniti: "🏊",
    facilit: "🏊",
    smoking: "🚭",
    "short-term": "🏨",
    letting: "🏨",
    damage: "⚠️",
    liabilit: "⚠️",
    compliance: "📋",
    enforcement: "📋",
    communicat: "📬",
    amendment: "📝",
};
/**
 * @generated FunctionHeader
 * Function: getSectionIcon
 * Path: frontend/src/app/(app)/governance/bylaws/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function getSectionIcon(title: string): string {
    const t = title.toLowerCase();
    for (const [key, icon] of Object.entries(SECTION_ICONS)) {
        if (t.includes(key)) return icon;
    }
    return "📄";
}

interface BylawSection {
    number: string;
    title: string;
    content: string;
}
/**
 * @generated FunctionHeader
 * Function: parseSections
 * Path: frontend/src/app/(app)/governance/bylaws/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function parseSections(content: string): BylawSection[] {
    const sections: BylawSection[] = [];
    // Split on ### headers
    const chunks = content.split(/(?=^###\s)/m);
    for (const chunk of chunks) {
        const headerMatch = chunk.match(/^###\s+(.+?)(?:\n|$)/);
        if (!headerMatch) continue;
        const fullTitle = headerMatch[1].trim();
        // Strip leading number "1. Title" → number="1", title="Title"
        const numMatch = fullTitle.match(/^(\d+)\.\s+(.+)$/);
        const number = numMatch ? numMatch[1] : "";
        const title = numMatch ? numMatch[2] : fullTitle;
        const sectionContent = chunk.replace(/^###\s+.+?(?:\n|$)/, "").trim();
        sections.push({number, title, content: sectionContent});
    }
    return sections;
}
// Simple inline markdown renderer (bold + bullet lists)
/**
 * @generated FunctionHeader
 * Function: RenderContent
 * Path: frontend/src/app/(app)/governance/bylaws/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function RenderContent({text}: { text: string }) {
    const lines = text.split("\n");
    const elements: React.ReactNode[] = [];
    let listBuffer: string[] = [];
    /**
     * @generated FunctionHeader
     * Function: flushList
     * Path: frontend/src/app/(app)/governance/bylaws/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const flushList = () => {
        if (listBuffer.length > 0) {
            elements.push(
                <ul key={`ul-${elements.length}`} className="list-disc pl-5 space-y-0.5 my-1">
                    {listBuffer.map((item, i) => (
                        <li key={i} className="text-sm text-slate-600">{renderInline(item)}</li>
                    ))}
                </ul>
            );
            listBuffer = [];
        }
    };
    /**
     * @generated FunctionHeader
     * Function: renderInline
     * Path: frontend/src/app/(app)/governance/bylaws/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const renderInline = (line: string) => {
        const parts = line.split(/(\*\*[^*]+\*\*)/g);
        return parts.map((part, i) =>
            part.startsWith("**") && part.endsWith("**")
                ? <strong key={i}>{part.slice(2, -2)}</strong>
                : part
        );
    };

    lines.forEach((line, i) => {
        if (line.startsWith("- ")) {
            listBuffer.push(line.slice(2));
        } else {
            flushList();
            if (line.startsWith("**") && line.includes("**", 2)) {
                elements.push(
                    <p key={i} className="font-semibold text-sm text-slate-800 mt-3 mb-0.5">
                        {renderInline(line)}
                    </p>
                );
            } else if (line.startsWith("---")) {
                elements.push(<hr key={i} className="my-3 border-slate-200"/>);
            } else if (line.trim() === "") {
                // skip blank lines (list flush handles spacing)
            } else {
                elements.push(
                    <p key={i} className="text-sm text-slate-600 leading-relaxed">
                        {renderInline(line)}
                    </p>
                );
            }
        }
    });
    flushList();
    return <>{elements}</>;
}
/**
 * @generated FunctionHeader
 * Function: BylawsPage
 * Path: frontend/src/app/(app)/governance/bylaws/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function BylawsPage() {
    const {api, selectedBuilding} = useAuth() as any;
    const [sections, setSections] = useState<BylawSection[]>([]);
    const [docMeta, setDocMeta] = useState<{ version: string; effective_date: string } | null>(null);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [selected, setSelected] = useState<BylawSection | null>(null);

    useEffect(() => {
        api.get("/by-laws/current")
            .then((res: any) => {
                const data = res.data;
                setDocMeta({version: data.version, effective_date: data.effective_date});
                setSections(parseSections(data.content || ""));
            })
            .catch(() => setSections([]))
            .finally(() => setLoading(false));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const filtered = sections.filter(s =>
        !search ||
        s.title.toLowerCase().includes(search.toLowerCase()) ||
        s.content.toLowerCase().includes(search.toLowerCase())
    );

    return (
        <div className="space-y-8">
            {/* Header */}
            <div className="flex items-end justify-between gap-4">
                <div>
                    <h1 className="text-3xl font-black text-slate-900 tracking-tight">
                        By-Laws <span className="text-indigo-600">&amp; Rules</span>
                    </h1>
                    <p className="text-slate-500 mt-1 text-sm">
                        Strata scheme by-laws, house rules, and living guidelines
                        {docMeta && (
                            <span className="ml-2 text-xs text-slate-400">
                                · Version {docMeta.version} · Effective {docMeta.effective_date}
                            </span>
                        )}
                    </p>
                </div>
                <Link href="/documents" className="text-xs text-indigo-600 hover:underline font-medium">
                    View all documents →
                </Link>
            </div>

            {/* Search */}
            <div className="relative max-w-md">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                <Input
                    placeholder="Search by-laws..."
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    className="pl-9"
                />
            </div>

            {loading ? (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {[...Array(6)].map((_, i) => <Skeleton key={i} className="h-24 rounded-xl"/>)}
                </div>
            ) : sections.length === 0 ? (
                <div>
                    <p className="text-sm text-muted-foreground mb-4">
                        No by-laws have been uploaded yet. Your strata manager can add them via the Documents section.
                    </p>
                    <p className="text-xs text-slate-400 mt-4">
                        For the full by-laws document, visit{" "}
                        <Link href="/documents" className="text-indigo-600 hover:underline">Documents</Link>.
                    </p>
                </div>
            ) : (
                <>
                    <p className="text-xs text-slate-400 -mt-4">
                        Click any section to read the full details
                    </p>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {filtered.map((section, i) => (
                            <Card
                                key={i}
                                className="rounded-xl hover:shadow-md hover:border-indigo-200 transition-all cursor-pointer group"
                                onClick={() => setSelected(section)}
                            >
                                <CardContent className="flex items-start gap-3 p-4">
                                    <div
                                        className="rounded-lg bg-indigo-50 group-hover:bg-indigo-100 transition-colors w-10 h-10 flex items-center justify-center text-xl shrink-0">
                                        {getSectionIcon(section.title)}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-2 flex-wrap">
                                            {section.number && (
                                                <Badge variant="outline"
                                                       className="text-[10px] shrink-0 text-indigo-600 border-indigo-200">
                                                    §{section.number}
                                                </Badge>
                                            )}
                                            <p className="font-semibold text-sm text-slate-800">{section.title}</p>
                                        </div>
                                        <p className="text-xs text-slate-500 mt-1 line-clamp-2">
                                            {section.content.replace(/\*\*/g, "").replace(/^- /gm, "• ").slice(0, 120)}…
                                        </p>
                                    </div>
                                    <BookOpen
                                        className="h-4 w-4 text-slate-300 group-hover:text-indigo-400 transition-colors mt-0.5 shrink-0"/>
                                </CardContent>
                            </Card>
                        ))}
                        {filtered.length === 0 && search && (
                            <p className="col-span-3 text-center text-muted-foreground py-8">
                                No by-laws match &ldquo;{search}&rdquo;
                            </p>
                        )}
                    </div>
                </>
            )}

            {/* Detail dialog */}
            <Dialog open={!!selected} onOpenChange={open => !open && setSelected(null)}>
                <DialogContent className="max-w-2xl max-h-[80vh] overflow-hidden flex flex-col">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2 text-lg">
                            {selected && (
                                <>
                                    <span className="text-2xl">{getSectionIcon(selected.title)}</span>
                                    {selected.number && (
                                        <Badge variant="outline" className="text-xs text-indigo-600 border-indigo-200">
                                            §{selected.number}
                                        </Badge>
                                    )}
                                    {selected.title}
                                </>
                            )}
                        </DialogTitle>
                        <DialogDescription className="text-xs text-slate-400">
                            {selectedBuilding?.name || 'Building'} By-Laws
                            {docMeta && ` · Version ${docMeta.version}`}
                        </DialogDescription>
                    </DialogHeader>
                    <div className="overflow-y-auto flex-1 pr-1 space-y-1">
                        {selected && <RenderContent text={selected.content}/>}
                    </div>
                    <div className="pt-3 border-t flex justify-end">
                        <Button variant="outline" size="sm" onClick={() => setSelected(null)}>
                            <X className="h-4 w-4 mr-1"/> Close
                        </Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
