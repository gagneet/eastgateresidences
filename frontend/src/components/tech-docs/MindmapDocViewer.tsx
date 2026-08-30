"use client";

import React, {useEffect, useId, useMemo, useState} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {AlertTriangle, ExternalLink, FileText, Loader2} from "lucide-react";

type Status = "idle" | "loading" | "ready" | "error";

type Props = {
    href: string;
    label: string;
    source: string;
};

let mermaidPromise: Promise<typeof import("mermaid").default> | null = null;
/**
 * @generated FunctionHeader
 * Function: getMermaid
 * Path: frontend/src/components/tech-docs/MindmapDocViewer.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function getMermaid() {
    if (!mermaidPromise) {
        mermaidPromise = import("mermaid").then((mod) => {
            const mermaid = mod.default;
            mermaid.initialize({
                startOnLoad: false,
                theme: "dark",
                securityLevel: "strict",
                fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
            });
            return mermaid;
        });
    }
    return mermaidPromise;
}
/**
 * @generated FunctionHeader
 * Function: MindmapDocViewer
 * Path: frontend/src/components/tech-docs/MindmapDocViewer.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function MindmapDocViewer({href, label, source}: Props) {
    const [status, setStatus] = useState<Status>("idle");
    const [markdown, setMarkdown] = useState<string>("");
    const [errorMessage, setErrorMessage] = useState<string>("");

    useEffect(() => {
        let cancelled = false;
        setStatus("loading");
        setErrorMessage("");

        fetch(href, {cache: "no-store"})
            .then(async (response) => {
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const text = await response.text();
                if (!cancelled) {
                    setMarkdown(text);
                    setStatus("ready");
                }
            })
            .catch((err: unknown) => {
                if (cancelled) return;
                setErrorMessage(err instanceof Error ? err.message : "Unknown error");
                setStatus("error");
            });

        return () => {
            cancelled = true;
        };
    }, [href]);

    const components = useMemo(
        () => ({
            code({className, children, ...props}: any) {
                const match = /language-(\w+)/.exec(className ?? "");
                if (match?.[1] === "mermaid") {
                    return <MermaidBlock chart={String(children ?? "").replace(/\n$/, "")}/>;
                }
                return (
                    <code className={className} {...props}>
                        {children}
                    </code>
                );
            },
            a({href: linkHref, children, ...props}: any) {
                const isExternal = typeof linkHref === "string" && /^https?:\/\//.test(linkHref);
                return (
                    <a
                        href={linkHref}
                        target={isExternal ? "_blank" : undefined}
                        rel={isExternal ? "noopener noreferrer" : undefined}
                        className="text-cyan-300 underline-offset-4 hover:underline"
                        {...props}
                    >
                        {children}
                    </a>
                );
            },
        }),
        []
    );

    return (
        <div className="flex h-full flex-col overflow-hidden">
            <header
                className="flex items-center justify-between gap-3 border-b border-slate-800 bg-slate-950/70 px-6 py-3">
                <div className="min-w-0">
                    <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-cyan-300">
                        <FileText className="h-3.5 w-3.5" aria-hidden="true"/>
                        Mindmap doc
                    </div>
                    <h2 className="mt-0.5 truncate text-lg font-semibold text-white">{label}</h2>
                    <p className="truncate text-xs text-slate-500">{source}</p>
                </div>
                <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-200 hover:border-cyan-400/50 hover:text-cyan-200 focus:outline-none focus:ring-2 focus:ring-cyan-400/40"
                >
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden="true"/>
                    Open raw
                </a>
            </header>

            <div className="flex-1 overflow-y-auto px-6 py-5" aria-busy={status === "loading"}>
                {status === "loading" && (
                    <div className="flex items-center gap-2 text-sm text-slate-400" role="status">
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true"/>
                        Loading {label}...
                    </div>
                )}

                {status === "error" && (
                    <div
                        className="flex items-start gap-3 rounded-2xl border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-100"
                        role="alert">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true"/>
                        <div>
                            <div className="font-semibold">Could not load this doc</div>
                            <div className="mt-1 text-xs text-amber-200/80">
                                {errorMessage}. The file should be served at{" "}
                                <code className="rounded bg-amber-900/40 px-1">{href}</code>.
                            </div>
                        </div>
                    </div>
                )}

                {status === "ready" && (
                    <article
                        className="prose prose-invert max-w-none prose-headings:text-white prose-p:text-slate-300 prose-li:text-slate-300 prose-strong:text-slate-100 prose-code:rounded prose-code:bg-slate-900 prose-code:px-1 prose-code:py-0.5 prose-code:text-cyan-200 prose-code:before:content-none prose-code:after:content-none prose-pre:bg-slate-900 prose-table:text-sm prose-th:text-slate-200 prose-td:border-slate-800 prose-a:text-cyan-300">
                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
                            {markdown}
                        </ReactMarkdown>
                    </article>
                )}
            </div>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: MermaidBlock
 * Path: frontend/src/components/tech-docs/MindmapDocViewer.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function MermaidBlock({chart}: { chart: string }) {
    const [svg, setSvg] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const reactId = useId();
    const renderId = `mermaid-${reactId.replace(/[^a-zA-Z0-9]/g, "")}`;

    useEffect(() => {
        let cancelled = false;
        getMermaid()
            .then((mermaid) => mermaid.render(renderId, chart))
            .then(({svg: rendered}) => {
                if (!cancelled) setSvg(rendered);
            })
            .catch((err: unknown) => {
                if (cancelled) return;
                setError(err instanceof Error ? err.message : "Unable to render diagram");
            });
        return () => {
            cancelled = true;
        };
    }, [chart, renderId]);

    if (error) {
        return (
            <pre className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-100">
        {`Mermaid render error: ${error}\n\n${chart}`}
      </pre>
        );
    }

    if (!svg) {
        return (
            <pre className="rounded-xl bg-slate-900 p-3 text-xs text-slate-300" aria-busy="true">
        {chart}
      </pre>
        );
    }

    return (
        <div
            className="my-4 overflow-x-auto rounded-xl border border-slate-800 bg-slate-950 p-3"
            role="img"
            aria-label="Mermaid diagram"
            dangerouslySetInnerHTML={{__html: svg}}
        />
    );
}
