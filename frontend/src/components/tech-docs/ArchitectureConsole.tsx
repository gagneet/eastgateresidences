"use client";

import React, {useMemo, useState} from "react";
import {
    Activity,
    AlertTriangle,
    BookOpen,
    Boxes,
    Braces,
    Database,
    FileCode2,
    GitBranch,
    Layers3,
    Network,
    Search,
    ServerCog,
    TimerReset,
} from "lucide-react";

import InteractiveMindmapFlow from "@/components/tech-docs/InteractiveMindmapFlow";
import MindmapDocViewer from "@/components/tech-docs/MindmapDocViewer";
import {
    interactiveMindmapNodes,
    interactiveMindmapEdges,
    mindmapDocs,
    MindmapNode,
    MindmapDoc,
} from "@/lib/mindmap/interactiveMindmapData";

type ConsoleTab =
    | "overview"
    | "graph"
    | "domains"
    | "apis"
    | "collections"
    | "jobs"
    | "issues"
    | "mindmap"
    | "docs";

const tabs: { id: ConsoleTab; label: string; icon: React.ReactNode }[] = [
    {id: "overview", label: "Overview", icon: <Activity className="h-4 w-4"/>},
    {id: "graph", label: "Graph Explorer", icon: <Network className="h-4 w-4"/>},
    {id: "domains", label: "Domains", icon: <Layers3 className="h-4 w-4"/>},
    {id: "apis", label: "APIs", icon: <Braces className="h-4 w-4"/>},
    {id: "collections", label: "Collections", icon: <Database className="h-4 w-4"/>},
    {id: "jobs", label: "Jobs", icon: <TimerReset className="h-4 w-4"/>},
    {id: "issues", label: "Audit Flags", icon: <AlertTriangle className="h-4 w-4"/>},
    {id: "mindmap", label: "Mindmap", icon: <BookOpen className="h-4 w-4"/>},
    {id: "docs", label: "Docs", icon: <FileCode2 className="h-4 w-4"/>},
];
/**
 * @generated FunctionHeader
 * Function: ArchitectureConsole
 * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function ArchitectureConsole() {
    const [activeTab, setActiveTab] = useState<ConsoleTab>("overview");
    const [query, setQuery] = useState("");
    const [selectedNode, setSelectedNode] = useState<MindmapNode | null>(null);

    const filteredNodes = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return interactiveMindmapNodes;
        return interactiveMindmapNodes.filter((node) => {
            const panelText = JSON.stringify(node.panel).toLowerCase();
            return (
                node.label.toLowerCase().includes(q) ||
                node.group.toLowerCase().includes(q) ||
                node.detail.toLowerCase().includes(q) ||
                panelText.includes(q)
            );
        });
    }, [query]);

    const stats = useMemo(() => {
        const domains = interactiveMindmapNodes.filter((n) => n.kind === "domain");
        const apiCount = sumPanel(domains, "apis");
        const collectionCount = sumPanel(domains, "collections");
        const fieldCount = sumPanel(domains, "fields");
        const jobCount = sumPanel(domains, "jobs");
        const issueCount = sumPanel(domains, "issues");
        return {
            domains: domains.length,
            apiCount,
            collectionCount,
            fieldCount,
            jobCount,
            issueCount,
            edgeCount: interactiveMindmapEdges.length,
            docCount: mindmapDocs.length
        };
    }, []);

    return (
        <div className="min-h-screen bg-slate-950 text-slate-100">
            <div className="border-b border-slate-800 bg-slate-950/95 px-6 py-4">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                        <div className="flex items-center gap-2 text-sm text-cyan-300">
                            <ServerCog className="h-4 w-4"/>
                            StrataOS Architecture Console
                        </div>
                        <h1 className="mt-1 text-2xl font-bold tracking-tight text-white">
                            System Map, Dependency Graph & Audit Surface
                        </h1>
                        <p className="mt-1 max-w-3xl text-sm text-slate-400">
                            Auto-generated from backend routers, frontend API usage, Mongo schema, background jobs, and
                            docs/architecture/mindmap.
                        </p>
                    </div>

                    <div className="relative w-full lg:w-[380px]">
                        <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-500"/>
                        <input
                            value={query}
                            onChange={(event) => setQuery(event.target.value)}
                            placeholder="Search API, collection, job, field..."
                            className="w-full rounded-xl border border-slate-700 bg-slate-900 px-9 py-2 text-sm text-slate-100 outline-none ring-cyan-500/40 placeholder:text-slate-500 focus:ring-2"
                        />
                    </div>
                </div>
            </div>

            <div className="flex min-h-[calc(100vh-92px)]">
                <aside className="hidden w-72 shrink-0 border-r border-slate-800 bg-slate-950 p-4 lg:block">
                    <div className="mb-4 grid grid-cols-2 gap-2">
                        <Stat label="Domains" value={stats.domains}/>
                        <Stat label="Edges" value={stats.edgeCount}/>
                        <Stat label="APIs" value={stats.apiCount}/>
                        <Stat label="Docs" value={stats.docCount}/>
                    </div>

                    <nav className="space-y-1">
                        {tabs.map((tab) => (
                            <button
                                key={tab.id}
                                onClick={() => setActiveTab(tab.id)}
                                className={`flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left text-sm transition ${
                                    activeTab === tab.id
                                        ? "bg-cyan-500/15 text-cyan-200 ring-1 ring-cyan-400/30"
                                        : "text-slate-400 hover:bg-slate-900 hover:text-slate-100"
                                }`}
                            >
                                {tab.icon}
                                {tab.label}
                            </button>
                        ))}
                    </nav>

                    <div className="mt-6 rounded-2xl border border-slate-800 bg-slate-900/60 p-4">
                        <div className="flex items-center gap-2 text-sm font-semibold text-white">
                            <GitBranch className="h-4 w-4 text-cyan-300"/>
                            Generation Contract
                        </div>
                        <p className="mt-2 text-xs leading-5 text-slate-400">
                            Run <code className="rounded bg-slate-800 px-1">npm run mindmap:generate</code> to
                            regenerate graph data from code and Mongo schema.
                        </p>
                    </div>
                </aside>

                <main className="flex-1 overflow-hidden">
                    {activeTab === "overview" && <Overview stats={stats} setActiveTab={setActiveTab}/>}
                    {activeTab === "graph" && <InteractiveMindmapFlow/>}
                    {activeTab === "domains" && <DomainGrid nodes={filteredNodes} onSelect={setSelectedNode}/>}
                    {activeTab === "apis" &&
                        <Inventory title="API Inventory" icon={<Braces className="h-5 w-5"/>} field="apis"
                                   nodes={filteredNodes}/>}
                    {activeTab === "collections" &&
                        <Inventory title="Collection Inventory" icon={<Database className="h-5 w-5"/>}
                                   field="collections" nodes={filteredNodes}/>}
                    {activeTab === "jobs" &&
                        <Inventory title="Cron & Worker Inventory" icon={<TimerReset className="h-5 w-5"/>} field="jobs"
                                   nodes={filteredNodes}/>}
                    {activeTab === "issues" &&
                        <Inventory title="Audit Flags & Merge Candidates" icon={<AlertTriangle className="h-5 w-5"/>}
                                   field="issues" nodes={filteredNodes} warning/>}
                    {activeTab === "mindmap" && <MindmapView query={query}/>}
                    {activeTab === "docs" && <DocsView query={query}/>}
                </main>
            </div>

            {selectedNode && <NodeDrawer node={selectedNode} onClose={() => setSelectedNode(null)}/>}
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: Overview
 * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function Overview({stats, setActiveTab}: { stats: any; setActiveTab: (tab: ConsoleTab) => void }) {
    return (
        <div className="h-full overflow-y-auto p-6">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <HeroStat label="Domains mapped" value={stats.domains} icon={<Layers3 className="h-5 w-5"/>}/>
                <HeroStat label="APIs surfaced" value={stats.apiCount} icon={<Braces className="h-5 w-5"/>}/>
                <HeroStat label="Collections linked" value={stats.collectionCount}
                          icon={<Database className="h-5 w-5"/>}/>
                <HeroStat label="Audit flags" value={stats.issueCount} icon={<AlertTriangle className="h-5 w-5"/>}/>
            </div>

            <div className="mt-6 grid gap-4 xl:grid-cols-3">
                <ConsoleCard title="Graph Explorer"
                             description="AWS-console style dependency map for domains, APIs, DB collections and jobs."
                             button="Open graph" onClick={() => setActiveTab("graph")}
                             icon={<Network className="h-6 w-6"/>}/>
                <ConsoleCard title="API → DB → UI"
                             description="Inventory endpoints discovered from backend routers and frontend usage."
                             button="Review APIs" onClick={() => setActiveTab("apis")}
                             icon={<Braces className="h-6 w-6"/>}/>
                <ConsoleCard title="Audit Surface"
                             description="Find overlap, stale routes, duplicate flows and consolidation candidates."
                             button="Open audit flags" onClick={() => setActiveTab("issues")}
                             icon={<AlertTriangle className="h-6 w-6"/>}/>
            </div>

            <div className="mt-6 rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
                <h2 className="text-lg font-semibold text-white">How this console is generated</h2>
                <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {[
                        ["Mindmap docs", "docs/architecture/mindmap/*.md"],
                        ["FastAPI routes", "backend/routers/*.py"],
                        ["Mongo schema", "docs/architecture/database_schema_full_2026.json or live Mongo"],
                        ["Frontend usage", "frontend/src/**/*.tsx"],
                        ["Cron jobs", "backend/cron/*.py"],
                        ["Workers", "backend/workers/*.py"],
                    ].map(([label, value]) => (
                        <div key={label} className="rounded-xl border border-slate-800 bg-slate-950 p-4">
                            <div className="text-sm font-medium text-slate-200">{label}</div>
                            <div className="mt-1 text-xs text-slate-500">{value}</div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: DomainGrid
 * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function DomainGrid({nodes, onSelect}: { nodes: MindmapNode[]; onSelect: (node: MindmapNode) => void }) {
    const domains = nodes.filter((n) => n.kind === "domain");
    return (
        <div className="h-full overflow-y-auto p-6">
            <h2 className="mb-4 text-xl font-semibold text-white">Domain Control Plane</h2>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {domains.map((node) => (
                    <button
                        key={node.id}
                        onClick={() => onSelect(node)}
                        className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 text-left transition hover:border-cyan-400/50 hover:bg-slate-900"
                    >
                        <div className="flex items-start justify-between gap-3">
                            <div>
                                <div className="text-sm text-cyan-300">{node.group}</div>
                                <h3 className="mt-1 text-lg font-semibold text-white">{node.label}</h3>
                            </div>
                            <Boxes className="h-5 w-5 text-slate-500"/>
                        </div>
                        <p className="mt-3 line-clamp-3 text-sm text-slate-400">{node.detail}</p>
                        <div className="mt-4 grid grid-cols-4 gap-2 text-center text-xs">
                            <MiniMetric label="APIs" value={node.panel.apis?.length ?? 0}/>
                            <MiniMetric label="DB" value={node.panel.collections?.length ?? 0}/>
                            <MiniMetric label="Fields" value={node.panel.fields?.length ?? 0}/>
                            <MiniMetric label="Jobs" value={node.panel.jobs?.length ?? 0}/>
                        </div>
                    </button>
                ))}
            </div>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: Inventory
 * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function Inventory({title, icon, field, nodes, warning = false}: {
    title: string;
    icon: React.ReactNode;
    field: keyof MindmapNode["panel"];
    nodes: MindmapNode[];
    warning?: boolean
}) {
    const rows = nodes.flatMap((node) => ((node.panel[field] as string[] | undefined) ?? []).map((item) => ({
        node,
        item
    })));
    return (
        <div className="h-full overflow-y-auto p-6">
            <div className="mb-4 flex items-center gap-2 text-xl font-semibold text-white">
                {icon}
                {title}
            </div>
            <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/60">
                <table className="w-full text-left text-sm">
                    <thead className="bg-slate-950 text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                        <th className="px-4 py-3">Domain</th>
                        <th className="px-4 py-3">Item</th>
                    </tr>
                    </thead>
                    <tbody>
                    {rows.map((row, idx) => (
                        <tr key={`${row.node.id}-${idx}`} className="border-t border-slate-800">
                            <td className="whitespace-nowrap px-4 py-3 text-cyan-300">{row.node.label}</td>
                            <td className={`px-4 py-3 ${warning ? "text-amber-200" : "text-slate-300"}`}>{row.item}</td>
                        </tr>
                    ))}
                    {rows.length === 0 && (
                        <tr>
                            <td className="px-4 py-6 text-slate-500" colSpan={2}>No matching items found.</td>
                        </tr>
                    )}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: MindmapView
 * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function MindmapView({query}: { query: string }) {
    const allDomainDocs = useMemo(
        () => mindmapDocs.filter((doc) => doc.category === "Mindmap Domains"),
        []
    );

    const q = query.trim().toLowerCase();
    const visibleDocs = q
        ? allDomainDocs.filter(
            (doc) => doc.label.toLowerCase().includes(q) || doc.source.toLowerCase().includes(q)
        )
        : allDomainDocs;

    const initial =
        visibleDocs.find((doc) => doc.label.startsWith("00")) ?? visibleDocs[0] ?? allDomainDocs[0] ?? null;
    const [selected, setSelected] = useState<MindmapDoc | null>(initial);
    /**
     * @generated FunctionHeader
     * Function: isVisible
     * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const isVisible = (doc: MindmapDoc | null) =>
        !!doc && visibleDocs.some((entry) => entry.source === doc.source);

    const activeDoc = isVisible(selected) ? selected : visibleDocs[0] ?? allDomainDocs[0] ?? null;

    return (
        <div className="flex h-[calc(100vh-92px)] overflow-hidden">
            <aside
                className="hidden w-72 shrink-0 flex-col border-r border-slate-800 bg-slate-950/60 md:flex"
                aria-label="Mindmap domain list"
            >
                <div className="border-b border-slate-800 px-4 py-3">
                    <div className="flex items-baseline justify-between gap-2">
                        <h2 className="text-sm font-semibold uppercase tracking-wide text-cyan-300">
                            Mindmap domains
                        </h2>
                        <span className="text-xs text-slate-500">
              {visibleDocs.length}/{allDomainDocs.length}
            </span>
                    </div>
                </div>
                <nav className="flex-1 overflow-y-auto p-2" aria-label="Mindmap doc selector">
                    {visibleDocs.length === 0 && (
                        <div className="px-3 py-4 text-xs text-slate-500">
                            No domain matches &ldquo;{query}&rdquo;.
                        </div>
                    )}
                    {visibleDocs.map((doc) => {
                        const isActive = activeDoc?.source === doc.source;
                        return (
                            <button
                                key={doc.source}
                                onClick={() => setSelected(doc)}
                                className={`w-full rounded-lg px-3 py-2 text-left text-sm transition focus:outline-none focus:ring-2 focus:ring-cyan-400/40 ${
                                    isActive
                                        ? "bg-cyan-500/15 text-cyan-100 ring-1 ring-cyan-400/30"
                                        : "text-slate-300 hover:bg-slate-900 hover:text-slate-100"
                                }`}
                                aria-current={isActive ? "page" : undefined}
                            >
                                <div className="truncate font-medium">{doc.label}</div>
                                <div className="truncate text-[11px] text-slate-500">{doc.source}</div>
                            </button>
                        );
                    })}
                </nav>
            </aside>

            <div className="flex-1 overflow-hidden">
                {activeDoc ? (
                    <MindmapDocViewer
                        key={activeDoc.source}
                        href={activeDoc.href}
                        label={activeDoc.label}
                        source={activeDoc.source}
                    />
                ) : (
                    <div className="flex h-full items-center justify-center p-8 text-sm text-slate-400">
                        No mindmap docs available. Run{" "}
                        <code className="mx-1 rounded bg-slate-800 px-1">npm run mindmap:generate</code> to
                        refresh.
                    </div>
                )}
            </div>
        </div>
    );
}

const DOC_CATEGORY_ORDER = [
    "Mindmap Domains",
    "Architecture",
    "API & Reference",
    "Modules",
    "Security & Access",
    "Operations",
];
/**
 * @generated FunctionHeader
 * Function: DocsView
 * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function DocsView({query}: { query: string }) {
    const q = query.trim().toLowerCase();
    const filtered = q
        ? mindmapDocs.filter(
            (doc) =>
                doc.label.toLowerCase().includes(q) ||
                doc.category.toLowerCase().includes(q) ||
                doc.source.toLowerCase().includes(q)
        )
        : mindmapDocs;

    const grouped = new Map<string, MindmapDoc[]>();
    for (const doc of filtered) {
        if (!grouped.has(doc.category)) grouped.set(doc.category, []);
        grouped.get(doc.category)!.push(doc);
    }

    const orderedCategories = [
        ...DOC_CATEGORY_ORDER.filter((cat) => grouped.has(cat)),
        ...Array.from(grouped.keys()).filter((cat) => !DOC_CATEGORY_ORDER.includes(cat)),
    ];

    return (
        <div className="h-full overflow-y-auto p-6">
            <div className="mb-4 flex items-baseline justify-between gap-4">
                <h2 className="text-xl font-semibold text-white">Documentation Registry</h2>
                <span className="text-xs text-slate-500">
          {filtered.length} of {mindmapDocs.length} docs
        </span>
            </div>

            {filtered.length === 0 && (
                <div
                    className="rounded-2xl border border-slate-800 bg-slate-900/60 p-8 text-center text-sm text-slate-400">
                    No docs match &ldquo;{query}&rdquo;.
                </div>
            )}

            {orderedCategories.map((category) => {
                const docs = grouped.get(category) ?? [];
                return (
                    <section key={category} className="mb-8" aria-labelledby={`cat-${slugify(category)}`}>
                        <div className="mb-3 flex items-baseline gap-2">
                            <h3
                                id={`cat-${slugify(category)}`}
                                className="text-sm font-semibold uppercase tracking-wide text-cyan-300"
                            >
                                {category}
                            </h3>
                            <span className="text-xs text-slate-500">{docs.length}</span>
                        </div>
                        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                            {docs.map((doc) => (
                                <a
                                    key={doc.source}
                                    href={doc.href}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="block rounded-2xl border border-slate-800 bg-slate-900/70 p-4 transition hover:border-cyan-400/50 hover:bg-slate-900 focus:outline-none focus:ring-2 focus:ring-cyan-400/40"
                                    aria-label={`Open ${doc.label} (${doc.category}) in a new tab`}
                                >
                                    <div className="flex items-start gap-2">
                                        <FileCode2 className="mt-0.5 h-4 w-4 shrink-0 text-cyan-300"
                                                   aria-hidden="true"/>
                                        <div className="min-w-0">
                                            <div
                                                className="truncate text-sm font-medium text-slate-100">{doc.label}</div>
                                            <div className="mt-1 truncate text-xs text-slate-500">{doc.source}</div>
                                        </div>
                                    </div>
                                </a>
                            ))}
                        </div>
                    </section>
                );
            })}
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: slugify
 * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function slugify(value: string) {
    return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}
/**
 * @generated FunctionHeader
 * Function: NodeDrawer
 * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function NodeDrawer({node, onClose}: { node: MindmapNode; onClose: () => void }) {
    return (
        <div
            className="fixed inset-y-0 right-0 z-50 w-full max-w-xl border-l border-slate-800 bg-slate-950 p-6 shadow-2xl">
            <button onClick={onClose}
                    className="mb-4 rounded-lg border border-slate-700 px-3 py-1 text-sm text-slate-300 hover:bg-slate-900">Close
            </button>
            <div className="text-sm text-cyan-300">{node.group}</div>
            <h2 className="mt-1 text-2xl font-bold text-white">{node.label}</h2>
            <p className="mt-3 text-sm leading-6 text-slate-400">{node.detail}</p>
            <div className="mt-6 space-y-4">
                <DetailSection title="Summary" items={[node.panel.summary]}/>
                <DetailSection title="Frontend" items={node.panel.frontend}/>
                <DetailSection title="APIs" items={node.panel.apis}/>
                <DetailSection title="Collections" items={node.panel.collections}/>
                <DetailSection title="Fields" items={node.panel.fields}/>
                <DetailSection title="Jobs" items={node.panel.jobs}/>
                <DetailSection title="Docs" items={node.panel.docs}/>
                <DetailSection title="Issues" items={node.panel.issues} warning/>
            </div>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: DetailSection
 * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function DetailSection({title, items, warning = false}: { title: string; items?: string[]; warning?: boolean }) {
    if (!items?.length) return null;
    return (
        <section>
            <h3 className="mb-2 text-sm font-semibold text-slate-200">{title}</h3>
            <div className="space-y-2">
                {items.map((item, idx) => (
                    <div key={idx}
                         className={`rounded-lg border px-3 py-2 text-xs ${warning ? "border-amber-500/30 bg-amber-500/10 text-amber-100" : "border-slate-800 bg-slate-900 text-slate-300"}`}>
                        {item}
                    </div>
                ))}
            </div>
        </section>
    );
}
/**
 * @generated FunctionHeader
 * Function: Stat
 * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function Stat({label, value}: { label: string; value: number }) {
    return (
        <div className="rounded-xl border border-slate-800 bg-slate-900 p-3">
            <div className="text-lg font-bold text-white">{value}</div>
            <div className="text-xs text-slate-500">{label}</div>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: HeroStat
 * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function HeroStat({label, value, icon}: { label: string; value: number; icon: React.ReactNode }) {
    return (
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
            <div className="flex items-center justify-between text-cyan-300">{icon}</div>
            <div className="mt-4 text-3xl font-bold text-white">{value}</div>
            <div className="mt-1 text-sm text-slate-400">{label}</div>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: ConsoleCard
 * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ConsoleCard({title, description, button, onClick, icon}: {
    title: string;
    description: string;
    button: string;
    onClick: () => void;
    icon: React.ReactNode
}) {
    return (
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
            <div className="text-cyan-300">{icon}</div>
            <h3 className="mt-3 text-lg font-semibold text-white">{title}</h3>
            <p className="mt-2 text-sm leading-6 text-slate-400">{description}</p>
            <button onClick={onClick}
                    className="mt-4 rounded-xl bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-cyan-400">
                {button}
            </button>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: MiniMetric
 * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function MiniMetric({label, value}: { label: string; value: number }) {
    return (
        <div className="rounded-lg bg-slate-950 px-2 py-2">
            <div className="font-semibold text-white">{value}</div>
            <div className="text-[10px] text-slate-500">{label}</div>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: sumPanel
 * Path: frontend/src/components/tech-docs/ArchitectureConsole.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function sumPanel(nodes: MindmapNode[], key: keyof MindmapNode["panel"]) {
    return nodes.reduce((sum, node) => sum + (((node.panel[key] as string[] | undefined) ?? []).length), 0);
}
