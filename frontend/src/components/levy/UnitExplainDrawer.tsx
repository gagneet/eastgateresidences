// @ts-nocheck
"use client";
import React from "react";
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle} from "@/components/ui/sheet";
import {Badge} from "@/components/ui/badge";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from "@/components/ui/table";
import {Minus, TrendingDown, TrendingUp} from "lucide-react";

interface Driver {
    facility: string;
    amount: number;
    benefit_group_id?: string | null;
}

interface UnitImpact {
    unit_number: string;
    unit_type: string;
    owner_name: string;
    entitlement: number;
    current_levy: number;
    fair_levy: number;
    proposed_levy: number;
    change: number;
    change_pct: number;
    lbfi: number;
    sei: number;
    drivers?: Driver[];
}

interface Props {
    unit: UnitImpact | null;
    open: boolean;
    onClose: () => void;
}
/**
 * @generated FunctionHeader
 * Function: fmt
 * Path: frontend/src/components/levy/UnitExplainDrawer.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmt = (v: number) =>
    `$${v.toLocaleString("en-AU", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
/**
 * @generated FunctionHeader
 * Function: UnitExplainDrawer
 * Path: frontend/src/components/levy/UnitExplainDrawer.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const UnitExplainDrawer: React.FC<Props> = ({unit, open, onClose}) => {
    if (!unit) return null;
    const isIncrease = unit.change > 5;
    const isDecrease = unit.change < -5;

    return (
        <Sheet open={open} onOpenChange={(v: boolean) => !v && onClose()}>
            <SheetContent side="right" className="w-full sm:max-w-xl overflow-y-auto">
                <SheetHeader>
                    <SheetTitle className="text-lg">
                        {unit.unit_number} — Levy Explanation
                    </SheetTitle>
                    <SheetDescription>
                        Why {unit.owner_name}'s levy{" "}
                        {isIncrease ? "increases" : isDecrease ? "decreases" : "stays roughly the same"}{" "}
                        under the fair allocation model
                    </SheetDescription>
                </SheetHeader>

                <div className="mt-6 space-y-6">
                    {/* Summary cards */}
                    <div className="grid grid-cols-3 gap-3">
                        {[
                            {label: "Current Levy", value: fmt(unit.current_levy), className: "bg-slate-50"},
                            {label: "Fair Levy", value: fmt(unit.fair_levy), className: "bg-blue-50"},
                            {
                                label: "Proposed",
                                value: fmt(unit.proposed_levy ?? unit.fair_levy),
                                className: isIncrease ? "bg-rose-50" : isDecrease ? "bg-emerald-50" : "bg-slate-50",
                            },
                        ].map((c) => (
                            <div key={c.label} className={`rounded-lg p-3 text-center ${c.className}`}>
                                <p className="text-xs text-muted-foreground">{c.label}</p>
                                <p className="font-bold text-sm">{c.value}</p>
                            </div>
                        ))}
                    </div>

                    {/* Change badge */}
                    <div className="flex items-center gap-2">
                        {isIncrease ? (
                            <TrendingUp className="w-4 h-4 text-rose-600"/>
                        ) : isDecrease ? (
                            <TrendingDown className="w-4 h-4 text-emerald-600"/>
                        ) : (
                            <Minus className="w-4 h-4 text-slate-400"/>
                        )}
                        <span
                            className={`font-semibold ${
                                isIncrease ? "text-rose-600" : isDecrease ? "text-emerald-600" : "text-slate-500"
                            }`}
                        >
              {unit.change >= 0 ? "+" : ""}
                            {fmt(unit.change)} ({unit.change_pct >= 0 ? "+" : ""}
                            {unit.change_pct}%)
            </span>
                        <Badge variant="outline" className="ml-auto">
                            UOE: {unit.entitlement}
                        </Badge>
                        {unit.sei != null && (
                            <Badge variant="outline">SEI: {(unit.sei * 100).toFixed(1)}%</Badge>
                        )}
                    </div>

                    {/* Explanation narrative */}
                    <div className="rounded-lg bg-blue-50 p-4 text-sm text-blue-900 space-y-1">
                        <p className="font-semibold">
                            Why is this {unit.unit_type}{" "}
                            {isIncrease ? "paying more" : isDecrease ? "paying less" : "unchanged"}?
                        </p>
                        {isDecrease && (
                            <p>
                                Under the benefit-based model, {unit.unit_type}s receive a lower share of costs
                                because some facilities (e.g. basement parking, apartment-only amenities) are not
                                allocated to this unit type. The current UOE model over-attributes those costs here.
                            </p>
                        )}
                        {isIncrease && (
                            <p>
                                Under the benefit-based model, {unit.unit_type}s are allocated more costs because
                                they benefit from a higher proportion of the facilities whose costs are split by
                                actual usage rather than pure entitlement share.
                            </p>
                        )}
                        {!isIncrease && !isDecrease && (
                            <p>
                                This unit's current levy closely matches its benefit-adjusted fair share — minimal
                                redistribution required.
                            </p>
                        )}
                    </div>

                    {/* Facility drivers */}
                    {unit.drivers && unit.drivers.length > 0 && (
                        <div>
                            <p className="font-semibold text-sm mb-2">Facility Cost Allocation Breakdown</p>
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Facility</TableHead>
                                        <TableHead className="text-right">Allocated ($)</TableHead>
                                        <TableHead className="text-right">% of Fair</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {[...unit.drivers]
                                        .sort((a, b) => b.amount - a.amount)
                                        .map((d) => (
                                            <TableRow key={d.facility}>
                                                <TableCell className="text-xs">{d.facility}</TableCell>
                                                <TableCell className="text-right text-xs">{fmt(d.amount)}</TableCell>
                                                <TableCell className="text-right text-xs">
                                                    {unit.fair_levy > 0
                                                        ? ((d.amount / unit.fair_levy) * 100).toFixed(1)
                                                        : "—"}
                                                    %
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                </TableBody>
                            </Table>
                        </div>
                    )}

                    {/* Legal note */}
                    <p className="text-xs text-muted-foreground border-t pt-3">
                        This analysis is based on the benefit allocation model as at last regeneration. Actual
                        levy amounts require a special resolution under s.78 UT(M)A. This is for planning
                        purposes only.
                    </p>
                </div>
            </SheetContent>
        </Sheet>
    );
};
