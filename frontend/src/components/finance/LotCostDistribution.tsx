"use client";
import React, {useState} from "react";
import {Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Button} from "@/components/ui/button";
import {Badge} from "@/components/ui/badge";
import {Building2} from "lucide-react";

import {formatMoneyCompact} from '@/lib/currency';
interface LotSummary {
    lot_number: string;
    total_admin_paid: number;
    total_sinking_paid: number;
    total_council: number;
    total_water: number;
    total_land_tax: number;
    total_interest: number;
    total_cost: number;
    cost_per_uoe: number;
    arrears_flag: boolean;
    risk_flag: boolean;
}

interface LotCostDistributionProps {
    summaries: LotSummary[];
    year: string;
}

type ViewMode = "all" | "top10" | "flagged";
/**
 * @generated FunctionHeader
 * Function: LotCostDistribution
 * Path: frontend/src/components/finance/LotCostDistribution.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const LotCostDistribution: React.FC<LotCostDistributionProps> = ({summaries, year}) => {
    const [viewMode, setViewMode] = useState<ViewMode>("top10");
    const [selected, setSelected] = useState<LotSummary | null>(null);

    const filtered = (() => {
        const sorted = [...summaries].sort((a, b) => b.total_cost - a.total_cost);
        if (viewMode === "top10") return sorted.slice(0, 10);
        if (viewMode === "flagged") return sorted.filter((s) => s.arrears_flag || s.risk_flag);
        return sorted.slice(0, 30);
    })();

    const chartData = filtered.map((s) => ({
        name: s.lot_number,
        Admin: Math.round(s.total_admin_paid + s.total_sinking_paid),
        Council: Math.round(s.total_council + s.total_land_tax),
        Water: Math.round(s.total_water),
        Interest: Math.round(s.total_interest),
        arrears: s.arrears_flag,
        risk: s.risk_flag,
        total: s.total_cost,
        raw: s,
    }));
    /**
     * @generated FunctionHeader
     * Function: fmt
     * Path: frontend/src/components/finance/LotCostDistribution.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fmt = (v: number) =>
        `$${v.toLocaleString("en-AU", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;

    return (
        <Card>
            <CardHeader>
                <div className="flex items-center justify-between flex-wrap gap-2">
                    <div>
                        <CardTitle className="flex items-center gap-2">
                            <Building2 className="w-5 h-5 text-emerald-600"/>
                            True Cost of Ownership by Lot
                        </CardTitle>
                        <CardDescription>Total financial cost per unit in {year}</CardDescription>
                    </div>
                    <div className="flex gap-2">
                        {(["top10", "flagged", "all"] as const).map((v) => (
                            <Button
                                key={v}
                                size="sm"
                                variant={viewMode === v ? "default" : "outline"}
                                onClick={() => setViewMode(v)}
                            >
                                {v === "top10" ? "Top 10" : v === "flagged" ? "Flagged" : "All"}
                            </Button>
                        ))}
                    </div>
                </div>
                <div className="flex gap-3 text-xs text-gray-500 mt-1">
          <span className="flex items-center gap-1">
            <span className="w-3 h-3 rounded-full bg-red-400 inline-block"/>
              {summaries.filter((s) => s.arrears_flag).length} in arrears
          </span>
                    <span className="flex items-center gap-1">
            <span className="w-3 h-3 rounded-full bg-amber-400 inline-block"/>
                        {summaries.filter((s) => s.risk_flag).length} risk flagged
          </span>
                </div>
            </CardHeader>
            <CardContent>
                <ResponsiveContainer width="100%" height={260}>
                    <BarChart data={chartData} margin={{top: 5, right: 10, bottom: 40, left: 10}}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb"/>
                        <XAxis
                            dataKey="name"
                            angle={-45}
                            textAnchor="end"
                            tick={{fontSize: 9}}
                            interval={0}
                        />
                        <YAxis tickFormatter={(v) => formatMoneyCompact(v)} tick={{fontSize: 9}}/>
                        <Tooltip
                            formatter={(val: number | undefined, name: string | number | undefined): [React.ReactNode, React.ReactNode] => [fmt(val ?? 0), String(name ?? "")]}
                            contentStyle={{fontSize: 11}}
                        />
                        <Bar dataKey="Admin" stackId="a" fill="#2F4F4F" name="Levy" radius={[0, 0, 0, 0]}>
                            {chartData.map((entry, index) => (
                                <Cell
                                    key={`cell-${index}`}
                                    fill={entry.arrears ? "#ef4444" : entry.risk ? "#f97316" : "#2F4F4F"}
                                    onClick={() => setSelected(entry.raw)}
                                    style={{cursor: "pointer"}}
                                />
                            ))}
                        </Bar>
                        <Bar dataKey="Council" stackId="a" fill="#6b7280" name="Council/Tax"/>
                        <Bar dataKey="Water" stackId="a" fill="#0ea5e9" name="Water"/>
                        <Bar dataKey="Interest" stackId="a" fill="#f59e0b" name="Interest"/>
                    </BarChart>
                </ResponsiveContainer>

                {selected && (
                    <div className="mt-4 p-3 bg-gray-50 rounded-lg border text-xs">
                        <div className="flex items-center justify-between mb-2">
                            <div className="font-semibold text-sm">Unit {selected.lot_number}</div>
                            <div className="flex gap-1">
                                {selected.arrears_flag &&
                                    <Badge className="bg-red-100 text-red-700 text-xs">Arrears</Badge>}
                                {selected.risk_flag &&
                                    <Badge className="bg-amber-100 text-amber-700 text-xs">Risk</Badge>}
                                <Button size="sm" variant="ghost" className="h-6 text-xs"
                                        onClick={() => setSelected(null)}>x</Button>
                            </div>
                        </div>
                        <div className="grid grid-cols-3 gap-2">
                            {[
                                {
                                    label: "Admin + Sinking",
                                    value: selected.total_admin_paid + selected.total_sinking_paid
                                },
                                {label: "Council + Land Tax", value: selected.total_council + selected.total_land_tax},
                                {label: "Water", value: selected.total_water},
                                {label: "Interest", value: selected.total_interest},
                                {label: "Total Cost", value: selected.total_cost},
                                {label: "Cost / UOE", value: selected.cost_per_uoe},
                            ].map((row) => (
                                <div key={row.label} className="bg-white rounded p-2">
                                    <div className="text-gray-500">{row.label}</div>
                                    <div className="font-semibold">{fmt(row.value)}</div>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </CardContent>
        </Card>
    );
};

export default LotCostDistribution;
