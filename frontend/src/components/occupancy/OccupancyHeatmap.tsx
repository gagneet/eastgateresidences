// @featuretrace:occupancy_intelligence — Lot-level heatmap grid for occupancy status
// Layer: frontend
// Data flow: OccupancyIntelligencePage → OccupancyHeatmap (lots data, building-scoped)
// Related: frontend/src/pages/dashboard/OccupancyIntelligencePage.jsx
// Scope: (building-scoped)

"use client";

import React, {useState} from "react";
import {Card, CardContent, CardHeader, CardTitle} from "@/components/ui/card";
import {Badge} from "@/components/ui/badge";
import {Grid3X3} from "lucide-react";

interface LotStatus {
    lot_number: string;
    unit_type?: string;
    status: "tenant" | "owner_occupied" | "investor_unknown";
    confidence: number;
    sources: string[];
    last_verified: string;
}

interface OccupancyHeatmapProps {
    lots: LotStatus[];
}

const STATUS_CONFIG = {
    tenant: {
        label: "Tenant",
        bg: "bg-red-100 hover:bg-red-200",
        border: "border-red-300",
        text: "text-red-800",
        dot: "bg-red-500",
        badgeCls: "bg-red-100 text-red-700 border-red-200",
    },
    owner_occupied: {
        label: "Owner-Occupied",
        bg: "bg-green-100 hover:bg-green-200",
        border: "border-green-300",
        text: "text-green-800",
        dot: "bg-green-500",
        badgeCls: "bg-green-100 text-green-700 border-green-200",
    },
    investor_unknown: {
        label: "Investor / Unknown",
        bg: "bg-muted hover:bg-muted/70",
        border: "border-border",
        text: "text-muted-foreground",
        dot: "bg-muted-foreground",
        badgeCls: "bg-muted text-muted-foreground border-border",
    },
};

type FilterStatus = "all" | "tenant" | "owner_occupied" | "investor_unknown";
/**
 * @generated FunctionHeader
 * Function: ConfidenceBar
 * Path: frontend/src/components/occupancy/OccupancyHeatmap.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ConfidenceBar({value}: { value: number }) {
    const pct = Math.round(value * 100);
    const color = pct >= 85 ? "bg-green-500" : pct >= 65 ? "bg-amber-400" : "bg-muted-foreground";
    return (
        <div className="mt-1">
            <div className="flex justify-between text-[10px] text-muted-foreground mb-0.5">
                <span>Confidence</span>
                <span>{pct}%</span>
            </div>
            <div className="h-1 w-full rounded-full bg-muted overflow-hidden">
                <div className={`h-full rounded-full ${color}`} style={{width: `${pct}%`}}/>
            </div>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: OccupancyHeatmap
 * Path: frontend/src/components/occupancy/OccupancyHeatmap.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const OccupancyHeatmap: React.FC<OccupancyHeatmapProps> = ({lots}) => {
    const [filter, setFilter] = useState<FilterStatus>("all");
    const [hovered, setHovered] = useState<string | null>(null);

    const filtered = filter === "all" ? lots : lots.filter(l => l.status === filter);

    const counts = lots.reduce(
        (acc, l) => {
            acc[l.status] = (acc[l.status] || 0) + 1;
            return acc;
        },
        {} as Record<string, number>,
    );

    return (
        <Card data-testid="occupancy-heatmap">
            <CardHeader className="pb-3">
                <div className="flex items-start justify-between flex-wrap gap-2">
                    <CardTitle className="flex items-center gap-2 text-base">
                        <Grid3X3 className="h-4 w-4 text-primary"/>
                        Lot Heatmap
                    </CardTitle>
                    {/* Legend / filter pills */}
                    <div className="flex flex-wrap gap-1.5">
                        {(["all", "tenant", "owner_occupied", "investor_unknown"] as FilterStatus[]).map(s => {
                            const cfg = s === "all" ? null : STATUS_CONFIG[s];
                            const isActive = filter === s;
                            const count = s === "all" ? lots.length : (counts[s] || 0);
                            return (
                                <button
                                    key={s}
                                    onClick={() => setFilter(s)}
                                    className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border transition-all
                                        ${isActive
                                        ? "border-muted-foreground bg-muted text-foreground shadow-sm"
                                        : "border-border bg-card text-muted-foreground hover:bg-muted"
                                    }`}
                                >
                                    {cfg && <span className={`w-2 h-2 rounded-full ${cfg.dot} flex-shrink-0`}/>}
                                    {cfg ? cfg.label : "All"}
                                    <span className="opacity-70">({count})</span>
                                </button>
                            );
                        })}
                    </div>
                </div>
            </CardHeader>
            <CardContent>
                {filtered.length === 0 ? (
                    <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">
                        No lots match the selected filter.
                    </div>
                ) : (
                    <div className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10 gap-1.5">
                        {filtered.map(lot => {
                            const cfg = STATUS_CONFIG[lot.status];
                            const isHovered = hovered === lot.lot_number;
                            return (
                                <div
                                    key={lot.lot_number}
                                    data-testid={`lot-tile-${lot.lot_number}`}
                                    onMouseEnter={() => setHovered(lot.lot_number)}
                                    onMouseLeave={() => setHovered(null)}
                                    className={`relative rounded-lg border p-1.5 cursor-default transition-all
                                        ${cfg.bg} ${cfg.border}
                                        ${isHovered ? "z-10 shadow-lg scale-110" : ""}
                                    `}
                                >
                                    <p className={`text-[10px] font-bold leading-tight ${cfg.text} truncate`}>
                                        {lot.lot_number}
                                    </p>
                                    {lot.unit_type && (
                                        <p className="text-[9px] text-muted-foreground truncate capitalize">
                                            {lot.unit_type.replace("_", " ")}
                                        </p>
                                    )}

                                    {/* Expanded tooltip on hover */}
                                    {isHovered && (
                                        <div
                                            className="absolute left-1/2 -translate-x-1/2 top-full mt-1 z-20 w-44 bg-card rounded-xl border shadow-xl p-2.5">
                                            <p className="text-xs font-bold text-foreground mb-1">{lot.lot_number}</p>
                                            <Badge className={`text-[10px] ${cfg.badgeCls} mb-1.5`}>
                                                {cfg.label}
                                            </Badge>
                                            <ConfidenceBar value={lot.confidence}/>
                                            <p className="text-[10px] text-muted-foreground mt-1">
                                                Sources: {lot.sources.join(", ")}
                                            </p>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                )}
            </CardContent>
        </Card>
    );
};

export default OccupancyHeatmap;