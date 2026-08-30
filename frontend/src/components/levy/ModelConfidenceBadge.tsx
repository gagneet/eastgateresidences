// @ts-nocheck
"use client";
import React from "react";
import {Tooltip, TooltipContent, TooltipProvider, TooltipTrigger} from "@/components/ui/tooltip";
import {Info} from "lucide-react";

interface ConfidenceData {
    score: number;
    band: "High" | "Medium" | "Low";
    factors: string[];
    levy_coverage_pct: number;
    asset_data_score: number;
    group_granularity_score: number;
}

interface Props {
    confidence: ConfidenceData | null | undefined;
}
/**
 * @generated FunctionHeader
 * Function: ModelConfidenceBadge
 * Path: frontend/src/components/levy/ModelConfidenceBadge.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const ModelConfidenceBadge: React.FC<Props> = ({confidence}) => {
    if (!confidence) return null;
    const color =
        confidence.band === "High" ? "bg-emerald-100 text-emerald-800 border-emerald-300" :
            confidence.band === "Medium" ? "bg-amber-100 text-amber-800 border-amber-300" :
                "bg-rose-100 text-rose-800 border-rose-300";

    return (
        <TooltipProvider>
            <Tooltip>
                <TooltipTrigger asChild>
          <span
              className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold border cursor-help ${color}`}>
            Model Confidence: {confidence.band} ({confidence.score}%)
            <Info className="w-3 h-3"/>
          </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs text-sm space-y-1">
                    <p className="font-semibold mb-1">Data Quality Factors</p>
                    {(confidence.factors || []).map((f: string, i: number) => (
                        <p key={i} className="text-xs text-muted-foreground">• {f}</p>
                    ))}
                    <div className="grid grid-cols-2 gap-1 mt-2 text-xs">
                        <span>Levy Data: {confidence.levy_coverage_pct}%</span>
                        <span>Asset Data: {confidence.asset_data_score}%</span>
                        <span>Groups: {confidence.group_granularity_score}%</span>
                    </div>
                </TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
};
