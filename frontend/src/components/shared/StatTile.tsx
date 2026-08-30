"use client";

/**
 * @featuretrace:design-system — Canonical KPI tile; replaces per-page KPI_COLOR_CLASSES maps.
 * Layer: frontend
 * Data flow: page → StatTile → rendered KPI row.
 * Related: frontend/src/lib/chartTheme.ts
 *           frontend/src/components/shared/PageHeader.tsx
 * Toggle: none
 * Tests: (none yet — see GAP-UI-001 §8e, rated 8/10 partly for this gap)
 *
 * Canonical KPI tile.
 *
 * WHY THIS FILE EXISTS
 * The four BI pages, the intelligence pages and several dashboards each
 * hand-rolled the same "icon chip + big number + caption" tile with different
 * radii, weights, shadows and a per-page `KPI_COLOR_CLASSES` map of raw palette
 * classes. This is the one implementation.
 *
 * `tone` carries MEANING, not decoration — it is for figures whose value has a
 * good/bad reading (SLA breaches, overdue compliance). Leave it `default` for a
 * neutral count. Tone is never the only signal: the caption always states what
 * the number is, so the tile survives greyscale and colour-vision deficiency.
 */

import * as React from "react";
import {cn} from "@/lib/utils";

export type StatTone = "default" | "good" | "warning" | "critical";

const TONE_CHIP: Record<StatTone, string> = {
    default: "bg-muted text-muted-foreground",
    good: "bg-emerald-50 text-emerald-700",
    warning: "bg-amber-50 text-amber-700",
    critical: "bg-red-50 text-red-700",
};

export interface StatTileProps {
    /** Short caption naming the figure, e.g. "Open work orders". */
    label: React.ReactNode;
    /** The figure itself. Pass an em dash for "not available" — never a zero. */
    value: React.ReactNode;
    icon?: React.ReactNode;
    tone?: StatTone;
    /** Secondary line under the value — a delta, an as-of date, a basis note. */
    hint?: React.ReactNode;
    loading?: boolean;
    /** Makes the whole tile a button. Every tile should lead somewhere. */
    onClick?: () => void;
    className?: string;
}

export function StatTile({
                             label,
                             value,
                             icon,
                             tone = "default",
                             hint,
                             loading,
                             onClick,
                             className,
                         }: StatTileProps) {
    const interactive = Boolean(onClick);

    const body = (
        <>
            {icon && (
                <span
                    aria-hidden="true"
                    className={cn("mb-3 flex h-9 w-9 items-center justify-center rounded-xl", TONE_CHIP[tone])}
                >
                    {icon}
                </span>
            )}
            {loading ? (
                <div className="h-8 w-24 animate-pulse rounded bg-muted" aria-hidden="true"/>
            ) : (
                <p className="text-2xl font-semibold tracking-tight text-foreground">{value}</p>
            )}
            <p className="mt-1 text-sm text-muted-foreground">{label}</p>
            {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
            {loading && <span className="sr-only">Loading {typeof label === "string" ? label : "value"}</span>}
        </>
    );

    const base = "rounded-xl border border-border bg-card p-5 shadow-sm text-left";

    if (!interactive) {
        return <div className={cn(base, className)}>{body}</div>;
    }

    return (
        <button
            type="button"
            onClick={onClick}
            className={cn(
                base,
                "min-h-[44px] w-full transition-shadow hover:shadow-md",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                className,
            )}
        >
            {body}
        </button>
    );
}

export default StatTile;
