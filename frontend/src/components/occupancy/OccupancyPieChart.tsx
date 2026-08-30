// @featuretrace:occupancy_intelligence — Pie chart component for occupancy status distribution
// Layer: frontend
// Data flow: OccupancyIntelligencePage → OccupancyPieChart (summary data, building-scoped)
// Related: frontend/src/pages/dashboard/OccupancyIntelligencePage.jsx
// Scope: (building-scoped)

"use client";

import React from "react";
import {Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip} from "recharts";
import {Card, CardContent, CardHeader, CardTitle} from "@/components/ui/card";
import {Users} from "lucide-react";

interface OccupancyPieChartProps {
    tenantCount: number;
    ownerOccupiedCount: number;
    investorUnknownCount: number;
    totalLots: number;
}

const STATUS_CONFIG = {
    tenant: {label: "Tenant", color: "#ef4444"},          // red
    owner_occupied: {label: "Owner-Occupied", color: "#22c55e"},  // green
    investor_unknown: {label: "Investor / Unknown", color: "#94a3b8"},  // grey
};
/**
 * @generated FunctionHeader
 * Function: CustomTooltip
 * Path: frontend/src/components/occupancy/OccupancyPieChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CustomTooltip = ({active, payload}: any) => {
    if (!active || !payload?.length) return null;
    const {name, value} = payload[0];
    return (
        <div className="bg-card border border-border rounded-lg shadow-lg px-3 py-2 text-sm">
            <p className="font-semibold">{name}</p>
            <p className="text-muted-foreground">{value} lot{value !== 1 ? "s" : ""}</p>
        </div>
    );
};
/**
 * @generated FunctionHeader
 * Function: OccupancyPieChart
 * Path: frontend/src/components/occupancy/OccupancyPieChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const OccupancyPieChart: React.FC<OccupancyPieChartProps> = ({
                                                                        tenantCount,
                                                                        ownerOccupiedCount,
                                                                        investorUnknownCount,
                                                                        totalLots,
                                                                    }) => {
    const data = [
        {name: STATUS_CONFIG.tenant.label, value: tenantCount, color: STATUS_CONFIG.tenant.color},
        {
            name: STATUS_CONFIG.owner_occupied.label,
            value: ownerOccupiedCount,
            color: STATUS_CONFIG.owner_occupied.color
        },
        {
            name: STATUS_CONFIG.investor_unknown.label,
            value: investorUnknownCount,
            color: STATUS_CONFIG.investor_unknown.color
        },
    ].filter(d => d.value > 0);

    return (
        <Card data-testid="occupancy-pie-chart">
            <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-base">
                    <Users className="h-4 w-4 text-primary"/>
                    Occupancy Distribution
                </CardTitle>
                <p className="text-xs text-muted-foreground">{totalLots} lots total</p>
            </CardHeader>
            <CardContent>
                {totalLots === 0 ? (
                    <div className="flex items-center justify-center h-48 text-muted-foreground text-sm">
                        No occupancy data yet. Run recompute to classify lots.
                    </div>
                ) : (
                    <ResponsiveContainer width="100%" height={220}>
                        <PieChart>
                            <Pie
                                data={data}
                                cx="50%"
                                cy="50%"
                                innerRadius={55}
                                outerRadius={85}
                                paddingAngle={2}
                                dataKey="value"
                            >
                                {data.map((entry, i) => (
                                    <Cell key={i} fill={entry.color} stroke="white" strokeWidth={2}/>
                                ))}
                            </Pie>
                            <Tooltip content={<CustomTooltip/>}/>
                            <Legend
                                iconType="circle"
                                iconSize={8}
                                formatter={(value) => (
                                    <span className="text-xs font-medium text-foreground">{value}</span>
                                )}
                            />
                        </PieChart>
                    </ResponsiveContainer>
                )}
            </CardContent>
        </Card>
    );
};

export default OccupancyPieChart;