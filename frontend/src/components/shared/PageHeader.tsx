"use client";

/**
 * @featuretrace:design-system — Canonical page chrome; renders each page's single <h1>.
 * Layer: frontend
 * Data flow: page → PageHeader → rendered title row (replaces per-page gradient hero bands).
 * Related: frontend/src/components/shared/StatTile.tsx
 *           frontend/src/lib/chartTheme.ts
 * Toggle: none
 * Tests: tests/frontend/e2e/intelligence-visual-verification.spec.js (asserts exactly one <h1>)
 *
 * Canonical page chrome for every dashboard route.
 *
 * WHY THIS FILE EXISTS
 * Before GAP-UI-001 no page-header component existed anywhere in the codebase,
 * so all ~140 dashboard pages hand-rolled their own title row. They drifted:
 * different sizes, weights, colours, and on the `/intelligence/*` pages a
 * gradient hero band used nowhere else in the app. This is the one component
 * that owns that row.
 *
 * Renders a real `<h1>` so every page has exactly one top-level heading — the
 * gradient hero bands it replaces were `<div>`s, leaving several pages with no
 * h1 at all for screen readers and document outline.
 */

import * as React from "react";
import {cn} from "@/lib/utils";

export interface PageHeaderProps {
    /** Page title. Rendered as the page's single `<h1>`. */
    title: React.ReactNode;
    /** One-line explanation of what the page is for. */
    description?: React.ReactNode;
    /** Icon element (typically a lucide icon) shown beside the title. */
    icon?: React.ReactNode;
    /** Actions aligned to the trailing edge — buttons, filters, refresh. */
    actions?: React.ReactNode;
    /** Status/among-title chips, e.g. a data-source or as-of badge. */
    badges?: React.ReactNode;
    className?: string;
}

/**
 * Standard dashboard page header: title, optional description, optional icon,
 * optional trailing actions.
 */
export function PageHeader({
                               title,
                               description,
                               icon,
                               actions,
                               badges,
                               className,
                           }: PageHeaderProps) {
    return (
        <header
            className={cn(
                "flex min-w-0 max-w-full flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-start sm:justify-between sm:pb-6",
                className,
            )}
        >
            <div className="flex min-w-0 max-w-full items-start gap-3">
                {icon && (
                    <span
                        aria-hidden="true"
                        className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-muted text-primary"
                    >
                        {icon}
                    </span>
                )}
                <div className="min-w-0 max-w-full">
                    <div className="flex flex-wrap items-center gap-2">
                        <h1 className="min-w-0 break-words text-xl font-semibold leading-tight tracking-tight text-foreground sm:text-2xl">
                            {title}
                        </h1>
                        {badges}
                    </div>
                    {description && (
                        <p className="mt-1 break-words text-sm text-muted-foreground">{description}</p>
                    )}
                </div>
            </div>
            {actions && (
                <div className="flex min-w-0 flex-wrap items-center gap-2 sm:shrink-0">{actions}</div>
            )}
        </header>
    );
}

export default PageHeader;
